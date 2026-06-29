from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .rules import MarketContext, Signal
from .scanner_state import ScannerDiagnostics


@dataclass(frozen=True)
class DerivedMetrics:
    body_percentage: float
    upper_wick_percentage: float
    lower_wick_percentage: float
    distance_from_vwap: float
    distance_from_trigger: float | None
    futures_price: float | None = None
    spot_price: float | None = None
    futures_spot_basis: float | None = None
    futures_open: float | None = None
    futures_high: float | None = None
    futures_low: float | None = None
    futures_close: float | None = None
    futures_vwap: float | None = None
    futures_volume: float | None = None
    candle_alignment_status: str = "UNKNOWN"
    candle_alignment_difference_seconds: float | None = None


@dataclass(frozen=True)
class LiveInstrumentSnapshot:
    instrument: str
    context: MarketContext
    signal: Signal
    metrics: DerivedMetrics
    last_completed_5m: datetime
    last_completed_15m: datetime
    vwap_source: str
    futures_symbol: str | None = None
    futures_expiry: object | None = None
    is_cached: bool = False
    action_block_reason: str | None = None


@dataclass(frozen=True)
class LiveSnapshot:
    instruments: tuple[LiveInstrumentSnapshot, ...]
    diagnostics: ScannerDiagnostics
    evaluated_at: datetime
