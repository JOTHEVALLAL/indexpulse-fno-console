from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from .rules import Candle


@dataclass(frozen=True)
class CandleShape:
    body_percentage: float
    upper_wick_percentage: float
    lower_wick_percentage: float


def ema(values: list[float], period: int) -> float:
    if not values:
        raise ValueError("EMA requires at least one value.")
    multiplier = 2 / (period + 1)
    current = values[0]
    for value in values[1:]:
        current = (value - current) * multiplier + current
    return current


def true_ranges(candles: list[Candle]) -> list[float]:
    if not candles:
        return []
    ranges = [candles[0].high - candles[0].low]
    for previous, current in zip(candles, candles[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return ranges


def atr(candles: list[Candle], period: int = 14) -> float:
    ranges = true_ranges(candles)
    if not ranges:
        raise ValueError("ATR requires candles.")
    return mean(ranges[-period:])


def session_vwap(candles: list[Candle]) -> float:
    total_volume = sum(candle.volume for candle in candles)
    if total_volume <= 0:
        raise ValueError("VWAP requires meaningful volume.")
    total_price_volume = sum(((candle.high + candle.low + candle.close) / 3) * candle.volume for candle in candles)
    return total_price_volume / total_volume


def opening_range(candles: list[Candle]) -> tuple[float, float]:
    opening = [candle for candle in candles if candle.timestamp.hour == 9 and 15 <= candle.timestamp.minute < 30]
    if not opening:
        raise ValueError("Opening range requires 09:15-09:30 candles.")
    return max(candle.high for candle in opening), min(candle.low for candle in opening)


def previous_day_levels(candles: list[Candle]) -> tuple[float, float]:
    if not candles:
        raise ValueError("Previous-day levels require candles.")
    return max(candle.high for candle in candles), min(candle.low for candle in candles)


def relative_volume(current_candle: Candle, prior_candles: list[Candle], lookback: int = 20) -> float:
    comparison = [candle.volume for candle in prior_candles[-lookback:] if candle.volume > 0]
    if not comparison:
        raise ValueError("Relative volume requires historical volume.")
    return current_candle.volume / mean(comparison)


def candle_shape(candle: Candle) -> CandleShape:
    full_range = candle.high - candle.low
    if full_range <= 0:
        return CandleShape(0.0, 0.0, 0.0)
    body_high = max(candle.open, candle.close)
    body_low = min(candle.open, candle.close)
    return CandleShape(
        body_percentage=abs(candle.close - candle.open) / full_range,
        upper_wick_percentage=(candle.high - body_high) / full_range,
        lower_wick_percentage=(body_low - candle.low) / full_range,
    )
