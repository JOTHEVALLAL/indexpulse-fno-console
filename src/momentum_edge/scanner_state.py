from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DataMode(str, Enum):
    SAMPLE = "SAMPLE"
    LIVE = "LIVE"


class ScannerState(str, Enum):
    SAMPLE_READY = "SAMPLE READY"
    LIVE_READY = "LIVE READY"
    LIVE_CACHED = "LIVE CACHED"
    LIVE_DATA_UNAVAILABLE = "LIVE DATA UNAVAILABLE"


class FreshnessState(str, Enum):
    FRESH = "FRESH"
    DELAYED = "DELAYED"
    STALE = "STALE"


def freshness_state(age_seconds: float) -> FreshnessState:
    if age_seconds <= 90:
        return FreshnessState.FRESH
    if age_seconds <= 180:
        return FreshnessState.DELAYED
    return FreshnessState.STALE


@dataclass(frozen=True)
class ScannerDiagnostics:
    data_mode: DataMode
    scanner_state: ScannerState
    last_successful_fetch: datetime | None
    last_completed_5m_candle: datetime | None
    last_completed_15m_candle: datetime | None
    data_freshness: FreshnessState | None
    data_age_seconds: float | None
    vwap_source: str
    last_evaluation: datetime | None
    next_expected_evaluation: datetime | None
    current_error: str | None = None
    historical_range_from: datetime | None = None
    historical_range_to: datetime | None = None
    timezone: str = "Asia/Kolkata"
    current_ist_time: datetime | None = None
    selected_trading_session: str | None = None


@dataclass
class ScannerCache:
    last_snapshot: object | None = None
    last_diagnostics: ScannerDiagnostics | None = None
    last_fetch_attempt: datetime | None = None


def should_fetch(now: datetime, last_fetch_attempt: datetime | None, min_interval_seconds: int = 30) -> bool:
    if last_fetch_attempt is None:
        return True
    return (now - last_fetch_attempt).total_seconds() >= min_interval_seconds
