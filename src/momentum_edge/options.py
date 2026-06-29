from __future__ import annotations

import json
import math
import shutil
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from .candle_utils import ensure_ist
from .rules import Direction, Signal, SignalEvaluator, SignalStatus
from .scanner_state import DataMode, FreshnessState, freshness_state


DEFAULT_OPTION_RECOMMENDATION_PATH = Path("data") / "option_recommendations.json"


class OptionSide(str, Enum):
    CE = "CE"
    PE = "PE"


class Moneyness(str, Enum):
    ITM = "ITM"
    ATM = "ATM"
    OTM = "OTM"


class SelectionStatus(str, Enum):
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"
    NO_SUITABLE_CONTRACT = "NO_SUITABLE_CONTRACT"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    OPTION_ENTRY_UNAVAILABLE = "OPTION_ENTRY_UNAVAILABLE"


@dataclass(frozen=True)
class OptionSelectionConfig:
    min_volume: int = 1
    min_open_interest: int = 1
    max_spread_pct: float = 8.0
    max_absolute_spread: float = 50.0
    min_premium: float = 5.0
    max_premium: float = 1000.0
    max_quote_age_seconds: int = 180


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    trading_symbol: str
    instrument_token: int
    exchange: str
    instrument_type: str
    option_type: OptionSide
    strike: float
    expiry: date
    lot_size: int
    tick_size: float


@dataclass(frozen=True)
class OptionQuote:
    trading_symbol: str
    ltp: float | None
    quote_timestamp: datetime | None
    volume: int | None
    open_interest: int | None
    oi_day_high: int | None
    oi_day_low: int | None
    best_bid: float | None
    best_bid_quantity: int | None
    best_ask: float | None
    best_ask_quantity: int | None
    ohlc: dict[str, float] | None
    last_traded_quantity: int | None


@dataclass(frozen=True)
class CandidateEvaluation:
    contract: OptionContract
    quote: OptionQuote | None
    moneyness: Moneyness
    midpoint: float | None
    spread: float | None
    spread_pct: float | None
    quote_freshness: FreshnessState | None
    quality_score: float
    quality_breakdown: dict[str, float | str]
    rejection_reasons: list[str]


@dataclass(frozen=True)
class OptionRecommendation:
    key: str
    mode: str
    session_date: str
    underlying_instrument: str
    underlying_signal_key: str | None
    underlying_direction: str
    option_side: str
    trading_symbol: str | None
    instrument_token: int | None
    strike: float | None
    expiry: str | None
    days_to_expiry: int | None
    lot_size: int | None
    tick_size: float | None
    moneyness: str | None
    ltp: float | None
    bid: float | None
    ask: float | None
    midpoint: float | None
    spread: float | None
    spread_percentage: float | None
    volume: int | None
    open_interest: int | None
    quote_timestamp: str | None
    quote_freshness: str | None
    selection_status: str
    quality_score: float
    quality_breakdown: dict[str, Any]
    rejection_reasons: list[str]
    recommendation_timestamp: str
    initial_observed_premium: float | None = None
    option_entry_price: float | None = None
    option_entry_bid: float | None = None
    option_entry_ask: float | None = None
    option_entry_midpoint: float | None = None
    option_entry_slippage: float | None = None
    option_entry_status: str | None = None
    outcome_snapshots: list[dict[str, Any]] | None = None


@dataclass
class OptionRecommendationState:
    recommendations: list[dict[str, Any]]
    keys: list[str]


def empty_option_state() -> OptionRecommendationState:
    return OptionRecommendationState(recommendations=[], keys=[])


def direction_to_option_side(direction: Direction) -> OptionSide:
    return OptionSide.CE if direction == Direction.BULLISH else OptionSide.PE


def underlying_symbol(instrument: str) -> str:
    if instrument == "NIFTY 50":
        return "NIFTY"
    if instrument == "BANK NIFTY":
        return "BANKNIFTY"
    raise ValueError(f"Unsupported option underlying: {instrument}")


def _coerce_expiry(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def discover_option_contracts(instruments: Iterable[dict], underlying: str, option_side: OptionSide, as_of: date) -> list[OptionContract]:
    contracts = []
    for item in instruments:
        expiry = _coerce_expiry(item.get("expiry"))
        option_type = item.get("instrument_type")
        symbol = str(item.get("tradingsymbol", ""))
        token = item.get("instrument_token")
        strike = item.get("strike")
        if item.get("exchange") != "NFO":
            continue
        if option_type not in {OptionSide.CE.value, OptionSide.PE.value}:
            continue
        if option_type != option_side.value:
            continue
        if item.get("name") != underlying:
            continue
        if expiry is None or expiry < as_of:
            continue
        if not isinstance(token, int):
            continue
        if strike is None:
            continue
        strike_value = float(strike)
        if option_type not in symbol or str(int(strike_value)) not in symbol:
            continue
        contracts.append(
            OptionContract(
                underlying=underlying,
                trading_symbol=symbol,
                instrument_token=token,
                exchange="NFO",
                instrument_type=str(option_type),
                option_type=OptionSide(option_type),
                strike=strike_value,
                expiry=expiry,
                lot_size=int(item.get("lot_size") or 0),
                tick_size=float(item.get("tick_size") or 0),
            )
        )
    return contracts


def nearest_expiry(contracts: list[OptionContract]) -> date | None:
    if not contracts:
        return None
    return min(contract.expiry for contract in contracts)


def derive_strike_spacing(strikes: list[float]) -> float:
    unique = sorted(set(strikes))
    diffs = [round(b - a, 6) for a, b in zip(unique, unique[1:]) if b > a]
    if not diffs:
        return 0.0
    return float(median(diffs))


def nearest_atm_strike(strikes: list[float], spot: float) -> float:
    return min(strikes, key=lambda strike: (abs(strike - spot), strike))


def classify_moneyness(option_side: OptionSide, strike: float, atm: float) -> Moneyness:
    if strike == atm:
        return Moneyness.ATM
    if option_side == OptionSide.CE:
        return Moneyness.ITM if strike < atm else Moneyness.OTM
    return Moneyness.ITM if strike > atm else Moneyness.OTM


def strike_candidates(contracts: list[OptionContract], spot: float) -> list[tuple[OptionContract, Moneyness]]:
    expiry = nearest_expiry(contracts)
    if expiry is None:
        return []
    expiry_contracts = [contract for contract in contracts if contract.expiry == expiry]
    strikes = sorted({contract.strike for contract in expiry_contracts})
    atm = nearest_atm_strike(strikes, spot)
    spacing = derive_strike_spacing(strikes)
    if spacing <= 0:
        target_strikes = {atm}
    else:
        target_strikes = {atm - spacing, atm, atm + spacing}
    selected = []
    for contract in expiry_contracts:
        if contract.strike in target_strikes:
            selected.append((contract, classify_moneyness(contract.option_type, contract.strike, atm)))
    return sorted(selected, key=lambda item: item[0].strike)


def option_quote_from_payload(trading_symbol: str, payload: dict | None) -> OptionQuote | None:
    if not payload:
        return None
    depth = payload.get("depth") or {}
    buy = depth.get("buy") or []
    sell = depth.get("sell") or []
    best_bid = buy[0] if buy else {}
    best_ask = sell[0] if sell else {}
    timestamp = payload.get("exchange_timestamp") or payload.get("timestamp") or payload.get("last_trade_time")
    quote_timestamp = ensure_ist(timestamp) if isinstance(timestamp, datetime) else None
    return OptionQuote(
        trading_symbol=trading_symbol,
        ltp=payload.get("last_price"),
        quote_timestamp=quote_timestamp,
        volume=payload.get("volume"),
        open_interest=payload.get("oi"),
        oi_day_high=payload.get("oi_day_high"),
        oi_day_low=payload.get("oi_day_low"),
        best_bid=best_bid.get("price"),
        best_bid_quantity=best_bid.get("quantity"),
        best_ask=best_ask.get("price"),
        best_ask_quantity=best_ask.get("quantity"),
        ohlc=payload.get("ohlc"),
        last_traded_quantity=payload.get("last_quantity"),
    )


def spread_values(bid: float, ask: float) -> tuple[float, float, float]:
    midpoint = (bid + ask) / 2
    spread = ask - bid
    spread_pct = spread / midpoint * 100
    return midpoint, spread, spread_pct


def evaluate_candidate(
    contract: OptionContract,
    moneyness: Moneyness,
    quote: OptionQuote | None,
    now: datetime,
    spot: float,
    confidence: str,
    india_vix: float,
    config: OptionSelectionConfig | None = None,
) -> CandidateEvaluation:
    config = config or OptionSelectionConfig()
    reasons: list[str] = []
    midpoint = spread = spread_pct = None
    freshness = None
    if quote is None:
        reasons.append("Missing quote response.")
    else:
        if quote.quote_timestamp is None:
            reasons.append("Missing quote timestamp.")
        else:
            age = (ensure_ist(now) - ensure_ist(quote.quote_timestamp)).total_seconds()
            freshness = freshness_state(age)
            if age > config.max_quote_age_seconds or freshness == FreshnessState.STALE:
                reasons.append("Quote is stale.")
        if quote.ltp is None or quote.ltp <= 0:
            reasons.append("LTP is unavailable or non-positive.")
        if quote.best_bid is None or quote.best_ask is None:
            reasons.append("Bid or ask is unavailable.")
        elif quote.best_bid <= 0 or quote.best_ask <= 0 or quote.best_ask <= quote.best_bid:
            reasons.append("Invalid or crossed bid/ask market.")
        else:
            midpoint, spread, spread_pct = spread_values(float(quote.best_bid), float(quote.best_ask))
            if spread > config.max_absolute_spread:
                reasons.append("Absolute spread is excessive.")
            if spread_pct > config.max_spread_pct:
                reasons.append("Spread percentage is excessive.")
        if quote.volume is None or quote.volume < config.min_volume:
            reasons.append("Volume is below threshold.")
        if quote.open_interest is None or quote.open_interest < config.min_open_interest:
            reasons.append("Open interest is below threshold.")
        if quote.ltp is not None and (quote.ltp < config.min_premium or quote.ltp > config.max_premium):
            reasons.append("Premium is outside configured range.")

    days_to_expiry = max(0, (contract.expiry - ensure_ist(now).date()).days)
    proximity = 1 / (1 + abs(contract.strike - spot))
    moneyness_score = {"ATM": 18, "ITM": 20, "OTM": 10}[moneyness.value]
    if confidence == "HIGH":
        confidence_score = 15 if moneyness in {Moneyness.ATM, Moneyness.ITM} else 8
    else:
        confidence_score = 15 if moneyness == Moneyness.ITM else 10
    vix_score = 12 if india_vix >= 18 and moneyness == Moneyness.ITM else 10
    spread_score = 15 if spread_pct is not None else 0
    if spread_pct is not None:
        spread_score = max(0, 15 - spread_pct)
    volume_score = 0 if quote is None or quote.volume is None else min(10, math.log10(max(1, quote.volume)) * 2)
    oi_score = 0 if quote is None or quote.open_interest is None else min(10, math.log10(max(1, quote.open_interest)) * 2)
    freshness_score = 10 if freshness == FreshnessState.FRESH else 5 if freshness == FreshnessState.DELAYED else 0
    premium_score = 10 if quote and quote.ltp and config.min_premium <= quote.ltp <= config.max_premium else 0
    expiry_score = max(0, 10 - min(days_to_expiry, 10) * 0.5)
    total = round(moneyness_score + proximity * 10 + spread_score + volume_score + oi_score + freshness_score + premium_score + confidence_score + vix_score + expiry_score, 2)
    breakdown = {
        "moneyness": moneyness_score,
        "strike_proximity": round(proximity * 10, 2),
        "spread_percentage": round(spread_score, 2),
        "volume": round(volume_score, 2),
        "open_interest": round(oi_score, 2),
        "quote_freshness": freshness_score,
        "premium_suitability": premium_score,
        "confidence_compatibility": confidence_score,
        "vix_compatibility": vix_score,
        "days_to_expiry": round(expiry_score, 2),
    }
    return CandidateEvaluation(contract, quote, moneyness, midpoint, spread, spread_pct, freshness, total, breakdown, reasons)


def recommendation_key(mode: DataMode, session_date: date, instrument: str, signal_key: str | None, option_symbol: str | None) -> str:
    return f"OPTION|{mode.value}|{session_date.isoformat()}|{instrument}|{signal_key or 'NO_SIGNAL_KEY'}|{option_symbol or 'NO_CONTRACT'}"


def select_option_contract(
    signal: Signal,
    mode: DataMode,
    instruments: Iterable[dict],
    quote_payloads: dict[str, dict],
    now: datetime,
    india_vix: float,
    config: OptionSelectionConfig | None = None,
) -> tuple[OptionRecommendation, list[CandidateEvaluation]]:
    config = config or OptionSelectionConfig()
    if mode != DataMode.LIVE or signal.signal_status != SignalStatus.READY or not signal.signal_direction:
        key = recommendation_key(mode, signal.alert_timestamp.date(), signal.instrument, SignalEvaluator.alert_key(signal), None)
        return OptionRecommendation(
            key=key,
            mode=mode.value,
            session_date=signal.alert_timestamp.date().isoformat(),
            underlying_instrument=signal.instrument,
            underlying_signal_key=SignalEvaluator.alert_key(signal),
            underlying_direction=signal.signal_direction.value if signal.signal_direction else "",
            option_side="",
            trading_symbol=None,
            instrument_token=None,
            strike=None,
            expiry=None,
            days_to_expiry=None,
            lot_size=None,
            tick_size=None,
            moneyness=None,
            ltp=None,
            bid=None,
            ask=None,
            midpoint=None,
            spread=None,
            spread_percentage=None,
            volume=None,
            open_interest=None,
            quote_timestamp=None,
            quote_freshness=None,
            selection_status=SelectionStatus.NOT_ELIGIBLE.value,
            quality_score=0,
            quality_breakdown={},
            rejection_reasons=["Signal is not eligible for option recommendation."],
            recommendation_timestamp=ensure_ist(now).isoformat(),
        ), []

    side = direction_to_option_side(signal.signal_direction)
    underlying = underlying_symbol(signal.instrument)
    contracts = discover_option_contracts(instruments, underlying, side, ensure_ist(now).date())
    candidates = strike_candidates(contracts, signal.spot_price)
    evaluations = [
        evaluate_candidate(
            contract,
            moneyness,
            option_quote_from_payload(contract.trading_symbol, quote_payloads.get(f"NFO:{contract.trading_symbol}") or quote_payloads.get(contract.trading_symbol)),
            now,
            signal.spot_price,
            signal.confidence_level,
            india_vix,
            config,
        )
        for contract, moneyness in candidates
    ]
    passing = [candidate for candidate in evaluations if not candidate.rejection_reasons]
    selected = None
    if passing:
        if signal.confidence_level == "HIGH":
            preferred = [item for item in passing if item.moneyness in {Moneyness.ATM, Moneyness.ITM}]
        else:
            preferred = [item for item in passing if item.moneyness == Moneyness.ITM]
        if india_vix >= 18:
            itm = [item for item in passing if item.moneyness == Moneyness.ITM]
            preferred = itm or preferred
        selected = max(preferred or passing, key=lambda item: item.quality_score)

    signal_key = SignalEvaluator.alert_key(signal)
    if selected is None:
        key = recommendation_key(mode, signal.alert_timestamp.date(), signal.instrument, signal_key, None)
        rejection_summary = [f"{item.contract.trading_symbol}: {'; '.join(item.rejection_reasons) or 'not selected'}" for item in evaluations]
        return OptionRecommendation(
            key=key,
            mode=mode.value,
            session_date=signal.alert_timestamp.date().isoformat(),
            underlying_instrument=signal.instrument,
            underlying_signal_key=signal_key,
            underlying_direction=signal.signal_direction.value,
            option_side=side.value,
            trading_symbol=None,
            instrument_token=None,
            strike=None,
            expiry=None,
            days_to_expiry=None,
            lot_size=None,
            tick_size=None,
            moneyness=None,
            ltp=None,
            bid=None,
            ask=None,
            midpoint=None,
            spread=None,
            spread_percentage=None,
            volume=None,
            open_interest=None,
            quote_timestamp=None,
            quote_freshness=None,
            selection_status=SelectionStatus.NO_SUITABLE_CONTRACT.value,
            quality_score=0,
            quality_breakdown={"candidates": [item.quality_breakdown for item in evaluations]},
            rejection_reasons=rejection_summary or ["No valid candidate contracts discovered."],
            recommendation_timestamp=ensure_ist(now).isoformat(),
        ), evaluations

    quote = selected.quote
    assert quote is not None
    key = recommendation_key(mode, signal.alert_timestamp.date(), signal.instrument, signal_key, selected.contract.trading_symbol)
    return OptionRecommendation(
        key=key,
        mode=mode.value,
        session_date=signal.alert_timestamp.date().isoformat(),
        underlying_instrument=signal.instrument,
        underlying_signal_key=signal_key,
        underlying_direction=signal.signal_direction.value,
        option_side=side.value,
        trading_symbol=selected.contract.trading_symbol,
        instrument_token=selected.contract.instrument_token,
        strike=selected.contract.strike,
        expiry=selected.contract.expiry.isoformat(),
        days_to_expiry=(selected.contract.expiry - ensure_ist(now).date()).days,
        lot_size=selected.contract.lot_size,
        tick_size=selected.contract.tick_size,
        moneyness=selected.moneyness.value,
        ltp=quote.ltp,
        bid=quote.best_bid,
        ask=quote.best_ask,
        midpoint=selected.midpoint,
        spread=selected.spread,
        spread_percentage=selected.spread_pct,
        volume=quote.volume,
        open_interest=quote.open_interest,
        quote_timestamp=quote.quote_timestamp.isoformat() if quote.quote_timestamp else None,
        quote_freshness=selected.quote_freshness.value if selected.quote_freshness else None,
        selection_status=SelectionStatus.SELECTED.value,
        quality_score=selected.quality_score,
        quality_breakdown=selected.quality_breakdown,
        rejection_reasons=[],
        recommendation_timestamp=ensure_ist(now).isoformat(),
        initial_observed_premium=quote.ltp,
        outcome_snapshots=[],
    ), evaluations


def backup_corrupt_option_state(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_suffix(f"{path.suffix}.corrupt-{timestamp}.bak")
    shutil.copy2(path, backup)
    return backup


def load_option_state(path: Path | str = DEFAULT_OPTION_RECOMMENDATION_PATH) -> OptionRecommendationState:
    state_path = Path(path)
    if not state_path.exists() or state_path.stat().st_size == 0:
        return empty_option_state()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup_corrupt_option_state(state_path)
        save_option_state(empty_option_state(), state_path)
        return empty_option_state()
    return OptionRecommendationState(
        recommendations=list(payload.get("recommendations", [])),
        keys=list(payload.get("keys", [])),
    )


def save_option_state(state: OptionRecommendationState, path: Path | str = DEFAULT_OPTION_RECOMMENDATION_PATH) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def append_recommendation(state: OptionRecommendationState, recommendation: OptionRecommendation) -> tuple[bool, OptionRecommendationState]:
    if recommendation.key in state.keys:
        return False, state
    state.keys.append(recommendation.key)
    state.recommendations.append(asdict(recommendation))
    return True, state


def valid_quote_for_entry(quote: OptionQuote | None, now: datetime, config: OptionSelectionConfig | None = None) -> bool:
    config = config or OptionSelectionConfig()
    if quote is None or quote.quote_timestamp is None or quote.ltp is None or quote.ltp <= 0:
        return False
    age = (ensure_ist(now) - ensure_ist(quote.quote_timestamp)).total_seconds()
    return age <= config.max_quote_age_seconds


def capture_option_entry(recommendation: dict[str, Any], quote: OptionQuote | None, now: datetime, config: OptionSelectionConfig | None = None) -> dict[str, Any]:
    if not valid_quote_for_entry(quote, now, config):
        recommendation["option_entry_status"] = SelectionStatus.OPTION_ENTRY_UNAVAILABLE.value
        return recommendation
    assert quote is not None
    bid = quote.best_bid
    ask = quote.best_ask
    midpoint = None
    if bid is not None and ask is not None and ask > bid > 0:
        midpoint = (bid + ask) / 2
    recommendation["option_entry_price"] = quote.ltp
    recommendation["option_entry_bid"] = bid
    recommendation["option_entry_ask"] = ask
    recommendation["option_entry_midpoint"] = midpoint
    recommendation["option_entry_status"] = "OPTION_ENTRY_CAPTURED"
    initial = recommendation.get("initial_observed_premium")
    recommendation["option_entry_slippage"] = None if initial is None else quote.ltp - initial
    return recommendation


def capture_outcome_snapshot(recommendation: dict[str, Any], outcome: str, quote: OptionQuote | None, now: datetime) -> dict[str, Any]:
    snapshots = recommendation.get("outcome_snapshots") or []
    if quote is None or quote.quote_timestamp is None or quote.ltp is None:
        snapshots.append({"outcome": outcome, "timestamp": ensure_ist(now).isoformat(), "status": "QUOTE_UNAVAILABLE"})
    else:
        bid = quote.best_bid
        ask = quote.best_ask
        midpoint = (bid + ask) / 2 if bid is not None and ask is not None and ask > bid > 0 else None
        snapshots.append(
            {
                "outcome": outcome,
                "timestamp": ensure_ist(now).isoformat(),
                "ltp": quote.ltp,
                "bid": bid,
                "ask": ask,
                "midpoint": midpoint,
                "quote_timestamp": quote.quote_timestamp.isoformat(),
            }
        )
    recommendation["outcome_snapshots"] = snapshots
    return recommendation
