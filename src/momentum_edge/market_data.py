from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .candle_utils import IST, completed_candles, ensure_ist, latest_completed_candle
from .indicators import atr, candle_shape, ema, opening_range, previous_day_levels, relative_volume, session_vwap
from .rules import Candle, Direction, IndicatorSnapshot, MarketContext


@dataclass(frozen=True)
class RawInstrumentData:
    instrument: str
    last_price: float
    candles_5m: list[Candle]
    candles_15m: list[Candle]
    previous_day_candles: list[Candle]


@dataclass(frozen=True)
class RawFuturesData:
    tradingsymbol: str
    instrument_token: int
    expiry: object
    last_price: float
    candles_5m: list[Candle]


@dataclass(frozen=True)
class BuiltMarketContext:
    context: MarketContext
    body_percentage: float
    upper_wick_percentage: float
    lower_wick_percentage: float
    distance_from_vwap: float
    vwap_source: str
    last_completed_5m: datetime
    last_completed_15m: datetime
    futures_symbol: str | None = None
    futures_expiry: object | None = None
    futures_price: float | None = None
    futures_candle: Candle | None = None
    futures_vwap: float | None = None
    futures_spot_basis: float | None = None
    alignment_status: str = "SPOT_ONLY"
    alignment_difference_seconds: float | None = None


class HistoricalRangeError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalRange:
    from_dt: datetime
    to_dt: datetime
    interval: str


@dataclass(frozen=True)
class HistoricalRangePlan:
    selected_session: date
    range_5m: HistoricalRange
    range_15m: HistoricalRange
    previous_day_candidates: tuple[date, ...]
    current_ist_time: datetime
    timezone: str = "Asia/Kolkata"


def _session_candles(candles: list[Candle]) -> list[Candle]:
    return [candle for candle in candles if candle.timestamp.time() >= time(9, 15)]


def is_weekday(session_date: date) -> bool:
    return session_date.weekday() < 5


def previous_weekday(session_date: date) -> date:
    candidate = session_date - timedelta(days=1)
    while not is_weekday(candidate):
        candidate -= timedelta(days=1)
    return candidate


def latest_completed_session_candle_end(session_date: date, minutes: int, now: datetime | None = None) -> datetime:
    local_now = ensure_ist(now) if now is not None else None
    session_start = datetime.combine(session_date, time(9, 15), tzinfo=IST)
    session_end = datetime.combine(session_date, time(15, 30), tzinfo=IST)
    if local_now is None or local_now.date() > session_date or local_now >= session_end:
        return session_end
    if local_now <= session_start:
        return session_start
    floored = floor_to_session_interval(local_now, minutes)
    return floored


def floor_to_session_interval(timestamp: datetime, minutes: int) -> datetime:
    local = ensure_ist(timestamp)
    session_start = datetime.combine(local.date(), time(9, 15), tzinfo=IST)
    elapsed_minutes = int((local - session_start).total_seconds() // 60)
    buckets = max(0, elapsed_minutes // minutes)
    return session_start + timedelta(minutes=buckets * minutes)


def validate_historical_range(from_dt: datetime, to_dt: datetime, interval: str) -> tuple[datetime, datetime]:
    start = ensure_ist(from_dt)
    end = ensure_ist(to_dt)
    if start >= end:
        raise HistoricalRangeError(
            f"Invalid historical range for {interval}: from_dt {start.isoformat()} must be before to_dt {end.isoformat()}."
        )
    return start, end


def _range_for_session(session_date: date, minutes: int, now: datetime | None, interval: str) -> HistoricalRange:
    start = datetime.combine(session_date, time(9, 15), tzinfo=IST)
    end = latest_completed_session_candle_end(session_date, minutes, now)
    start, end = validate_historical_range(start, end, interval)
    return HistoricalRange(start, end, interval)


def determine_latest_trading_session(now: datetime) -> date:
    local_now = ensure_ist(now)
    today = local_now.date()
    session_start = datetime.combine(today, time(9, 15), tzinfo=IST)
    if not is_weekday(today):
        return previous_weekday(today)
    if local_now < session_start + timedelta(minutes=5):
        return previous_weekday(today)
    return today


def historical_range_plan(now: datetime, previous_lookback_days: int = 10) -> HistoricalRangePlan:
    local_now = ensure_ist(now)
    selected = determine_latest_trading_session(local_now)
    previous_selected = previous_weekday(selected)
    try:
        range_5m = _range_for_session(selected, 5, local_now if selected == local_now.date() else None, "5minute")
    except HistoricalRangeError:
        range_5m = _range_for_session(previous_selected, 5, None, "5minute")
    try:
        range_15m = _range_for_session(selected, 15, local_now if selected == local_now.date() else None, "15minute")
    except HistoricalRangeError:
        range_15m = _range_for_session(previous_selected, 15, None, "15minute")
    candidates: list[date] = []
    candidate = previous_selected
    attempts = 0
    while attempts < previous_lookback_days:
        if is_weekday(candidate):
            candidates.append(candidate)
        candidate -= timedelta(days=1)
        attempts += 1
    return HistoricalRangePlan(
        selected_session=selected,
        range_5m=range_5m,
        range_15m=range_15m,
        previous_day_candidates=tuple(candidates),
        current_ist_time=local_now,
    )


def day_window(session_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(session_date, time(9, 15), tzinfo=IST)
    end = datetime.combine(session_date, time(15, 30), tzinfo=IST)
    return validate_historical_range(start, end, "day")


def _trend_from_15m(candles: list[Candle]) -> Direction | None:
    closes = [candle.close for candle in candles]
    if len(closes) < 3:
        return None
    ema_9 = ema(closes, 9)
    ema_21 = ema(closes, 21)
    if ema_9 > ema_21:
        return Direction.BULLISH
    if ema_9 < ema_21:
        return Direction.BEARISH
    return None


def build_market_context(raw: RawInstrumentData, india_vix: float, now: datetime) -> BuiltMarketContext:
    local_now = ensure_ist(now)
    candles_5m = completed_candles(raw.candles_5m, local_now, 5)
    candles_15m = completed_candles(raw.candles_15m, local_now, 15)
    if not candles_5m:
        raise ValueError(f"No completed 5-minute candles for {raw.instrument}.")
    if not candles_15m:
        raise ValueError(f"No completed 15-minute candles for {raw.instrument}.")

    current = latest_completed_candle(candles_5m, local_now, 5)
    if current is None:
        raise ValueError(f"No completed current candle for {raw.instrument}.")

    session = _session_candles(candles_5m)
    if not session:
        raise ValueError(f"No session candles for {raw.instrument}.")

    vwap = session_vwap(session)
    opening_high, opening_low = opening_range(session)
    prev_high, prev_low = previous_day_levels(raw.previous_day_candles)
    closes = [candle.close for candle in candles_5m]
    atr_value = atr(candles_5m, 14)
    rel_volume = relative_volume(current, candles_5m[:-1])
    shape = candle_shape(current)
    trend = _trend_from_15m(candles_15m)

    indicators = IndicatorSnapshot(
        vwap=vwap,
        ema_9=ema(closes, 9),
        ema_21=ema(closes, 21),
        ema_50=ema(closes, 50),
        opening_range_high=opening_high,
        opening_range_low=opening_low,
        previous_day_high=prev_high,
        previous_day_low=prev_low,
        atr=atr_value,
        relative_volume=rel_volume,
        india_vix=india_vix,
        trend_15m=trend,
    )
    context = MarketContext(
        instrument=raw.instrument,
        candle=current,
        previous_candles=tuple(candles_5m[:-1]),
        indicators=indicators,
    )

    return BuiltMarketContext(
        context=context,
        body_percentage=shape.body_percentage,
        upper_wick_percentage=shape.upper_wick_percentage,
        lower_wick_percentage=shape.lower_wick_percentage,
        distance_from_vwap=current.close - vwap,
        vwap_source="UNDERLYING_VOLUME",
        last_completed_5m=candles_5m[-1].timestamp,
        last_completed_15m=candles_15m[-1].timestamp,
    )


def build_market_context_with_futures(
    spot: RawInstrumentData,
    futures: RawFuturesData,
    india_vix: float,
    now: datetime,
) -> BuiltMarketContext:
    local_now = ensure_ist(now)
    spot_5m = completed_candles(spot.candles_5m, local_now, 5)
    spot_15m = completed_candles(spot.candles_15m, local_now, 15)
    futures_5m = completed_candles(futures.candles_5m, local_now, 5)
    if not spot_5m:
        raise ValueError(f"No completed 5-minute spot candles for {spot.instrument}.")
    if not spot_15m:
        raise ValueError(f"No completed 15-minute spot candles for {spot.instrument}.")
    if not futures_5m:
        raise ValueError(f"No completed 5-minute futures candles for {spot.instrument}.")

    current_spot = spot_5m[-1]
    current_futures = futures_5m[-1]
    alignment_difference = abs((current_spot.timestamp - current_futures.timestamp).total_seconds())
    aligned = alignment_difference == 0
    if not aligned:
        # Keep building a context for diagnostics, but use a neutral VWAP so VWAP-dependent
        # setups can be blocked by the scanner before becoming actionable.
        futures_session = []
        futures_vwap = None
        rel_volume = 0.0
    else:
        futures_session = _session_candles(futures_5m)
        futures_vwap = session_vwap(futures_session)
        rel_volume = relative_volume(current_futures, futures_5m[:-1])

    spot_session = _session_candles(spot_5m)
    if not spot_session:
        raise ValueError(f"No session spot candles for {spot.instrument}.")

    opening_high, opening_low = opening_range(spot_session)
    prev_high, prev_low = previous_day_levels(spot.previous_day_candles)
    closes = [candle.close for candle in spot_5m]
    atr_value = atr(spot_5m, 14)
    shape = candle_shape(current_spot)
    trend = _trend_from_15m(spot_15m)

    basis = futures.last_price - spot.last_price
    # Futures VWAP is transformed to a spot-equivalent comparison level by
    # subtracting the current futures-spot basis. The raw futures VWAP and
    # source symbol remain exposed in diagnostics.
    vwap_value = (futures_vwap - basis) if futures_vwap is not None else current_spot.close
    indicators = IndicatorSnapshot(
        vwap=vwap_value,
        ema_9=ema(closes, 9),
        ema_21=ema(closes, 21),
        ema_50=ema(closes, 50),
        opening_range_high=opening_high,
        opening_range_low=opening_low,
        previous_day_high=prev_high,
        previous_day_low=prev_low,
        atr=atr_value,
        relative_volume=rel_volume,
        india_vix=india_vix,
        trend_15m=trend,
    )
    context = MarketContext(
        instrument=spot.instrument,
        candle=current_spot,
        previous_candles=tuple(spot_5m[:-1]),
        indicators=indicators,
    )

    return BuiltMarketContext(
        context=context,
        body_percentage=shape.body_percentage,
        upper_wick_percentage=shape.upper_wick_percentage,
        lower_wick_percentage=shape.lower_wick_percentage,
        distance_from_vwap=current_spot.close - vwap_value,
        vwap_source=futures.tradingsymbol,
        last_completed_5m=current_spot.timestamp,
        last_completed_15m=spot_15m[-1].timestamp,
        futures_symbol=futures.tradingsymbol,
        futures_expiry=futures.expiry,
        futures_price=futures.last_price,
        futures_candle=current_futures,
        futures_vwap=futures_vwap,
        futures_spot_basis=basis,
        alignment_status="ALIGNED" if aligned else "MISALIGNED",
        alignment_difference_seconds=alignment_difference,
    )
def market_day_window(now: datetime) -> tuple[datetime, datetime]:
    local = ensure_ist(now)
    start = datetime.combine(local.date(), time(9, 15), tzinfo=IST)
    return start, local


def previous_day_window(now: datetime) -> tuple[datetime, datetime]:
    local = ensure_ist(now)
    previous = local.date() - timedelta(days=1)
    start = datetime.combine(previous, time(9, 15), tzinfo=IST)
    end = datetime.combine(previous, time(15, 30), tzinfo=IST)
    return start, end
