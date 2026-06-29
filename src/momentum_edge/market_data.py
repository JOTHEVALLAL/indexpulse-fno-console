from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

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


def _session_candles(candles: list[Candle]) -> list[Candle]:
    return [candle for candle in candles if candle.timestamp.time() >= time(9, 15)]


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
