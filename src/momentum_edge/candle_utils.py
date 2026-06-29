from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .rules import Candle


IST = ZoneInfo("Asia/Kolkata")


def ensure_ist(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=IST)
    return timestamp.astimezone(IST)


def floor_to_interval(timestamp: datetime, minutes: int) -> datetime:
    local = ensure_ist(timestamp)
    floored_minute = local.minute - (local.minute % minutes)
    return local.replace(minute=floored_minute, second=0, microsecond=0)


def last_completed_candle_end(now: datetime, minutes: int) -> datetime:
    local = ensure_ist(now)
    current_bucket = floor_to_interval(local, minutes)
    if local == current_bucket:
        return current_bucket
    return current_bucket


def is_completed_candle(candle: Candle, now: datetime, minutes: int) -> bool:
    candle_start = floor_to_interval(candle.timestamp, minutes)
    candle_end = candle_start + timedelta(minutes=minutes)
    return candle_end <= ensure_ist(now)


def completed_candles(candles: list[Candle], now: datetime, minutes: int) -> list[Candle]:
    deduped: dict[datetime, Candle] = {}
    for candle in candles:
        local_candle = Candle(
            timestamp=ensure_ist(candle.timestamp),
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )
        if is_completed_candle(local_candle, now, minutes):
            deduped[floor_to_interval(local_candle.timestamp, minutes)] = local_candle
    return [deduped[key] for key in sorted(deduped)]


def latest_completed_candle(candles: list[Candle], now: datetime, minutes: int) -> Candle | None:
    filtered = completed_candles(candles, now, minutes)
    if not filtered:
        return None
    return filtered[-1]


def next_expected_evaluation(now: datetime, minutes: int = 5, delay_seconds: int = 5) -> datetime:
    current = floor_to_interval(now, minutes)
    if ensure_ist(now) <= current + timedelta(seconds=delay_seconds):
        return current + timedelta(seconds=delay_seconds)
    return current + timedelta(minutes=minutes, seconds=delay_seconds)
