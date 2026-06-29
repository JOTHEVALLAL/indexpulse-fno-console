from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable


@dataclass(frozen=True)
class InstrumentSpec:
    name: str
    exchange: str
    tradingsymbol: str
    instrument_token: int


@dataclass(frozen=True)
class FuturesInstrumentSpec(InstrumentSpec):
    expiry: date
    instrument_type: str


@dataclass(frozen=True)
class ResolutionResult:
    ok: bool
    instrument: InstrumentSpec | FuturesInstrumentSpec | None
    error: str | None = None


EXPECTED_UNDERLYINGS = {
    "NIFTY 50": ("NSE", "NIFTY 50"),
    "BANK NIFTY": ("NSE", "NIFTY BANK"),
    "INDIA VIX": ("NSE", "INDIA VIX"),
}

FUTURES_UNDERLYINGS = {
    "NIFTY 50": ("NIFTY", "NIFTY"),
    "BANK NIFTY": ("BANKNIFTY", "BANKNIFTY"),
}


def resolve_instrument(name: str, instruments: Iterable[dict]) -> ResolutionResult:
    expected = EXPECTED_UNDERLYINGS.get(name)
    if not expected:
        return ResolutionResult(False, None, f"Unsupported instrument: {name}")

    expected_exchange, expected_symbol = expected
    matches = [
        item
        for item in instruments
        if item.get("exchange") == expected_exchange and item.get("tradingsymbol") == expected_symbol
    ]
    if not matches:
        return ResolutionResult(False, None, f"Could not resolve {name} on {expected_exchange} as {expected_symbol}.")
    if len(matches) > 1:
        return ResolutionResult(False, None, f"Multiple instrument matches found for {name}.")

    match = matches[0]
    token = match.get("instrument_token")
    if not isinstance(token, int):
        return ResolutionResult(False, None, f"Resolved {name} has invalid instrument token.")

    return ResolutionResult(
        True,
        InstrumentSpec(
            name=name,
            exchange=expected_exchange,
            tradingsymbol=expected_symbol,
            instrument_token=token,
        ),
    )


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


def resolve_nearest_monthly_future(name: str, instruments: Iterable[dict], as_of: date) -> ResolutionResult:
    expected = FUTURES_UNDERLYINGS.get(name)
    if not expected:
        return ResolutionResult(False, None, f"Unsupported futures underlying: {name}")

    underlying_name, symbol_prefix = expected
    candidates: list[tuple[date, dict]] = []
    rejected = []
    for item in instruments:
        expiry = _coerce_expiry(item.get("expiry"))
        tradingsymbol = str(item.get("tradingsymbol", ""))
        exchange = item.get("exchange")
        instrument_type = item.get("instrument_type")
        item_name = str(item.get("name", ""))

        if exchange != "NFO":
            rejected.append("wrong exchange")
            continue
        if instrument_type != "FUT":
            rejected.append("wrong instrument type")
            continue
        if item_name != underlying_name:
            rejected.append("wrong underlying")
            continue
        if not tradingsymbol.startswith(symbol_prefix):
            rejected.append("wrong symbol prefix")
            continue
        if expiry is None:
            rejected.append("missing expiry")
            continue
        if expiry < as_of:
            rejected.append("expired")
            continue
        token = item.get("instrument_token")
        if not isinstance(token, int):
            rejected.append("invalid token")
            continue
        candidates.append((expiry, item))

    if not candidates:
        reason = ", ".join(sorted(set(rejected))) or "no candidates"
        return ResolutionResult(False, None, f"Could not resolve nearest unexpired {name} futures contract: {reason}.")

    expiry, match = sorted(candidates, key=lambda candidate: candidate[0])[0]
    return ResolutionResult(
        True,
        FuturesInstrumentSpec(
            name=name,
            exchange="NFO",
            tradingsymbol=str(match["tradingsymbol"]),
            instrument_token=int(match["instrument_token"]),
            expiry=expiry,
            instrument_type="FUT",
        ),
    )
