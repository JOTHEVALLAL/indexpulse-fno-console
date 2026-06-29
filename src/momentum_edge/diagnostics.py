from __future__ import annotations

import csv
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .live_snapshot import LiveInstrumentSnapshot
from .rules import SignalEvaluator
from .scanner_state import DataMode, FreshnessState


DEFAULT_DIAGNOSTIC_PATH = Path("data") / "market_diagnostics.csv"


DIAGNOSTIC_COLUMNS = [
    "diagnostic_key",
    "evaluation_timestamp",
    "instrument",
    "data_mode",
    "spot_symbol",
    "spot_candle_timestamp",
    "spot_open",
    "spot_high",
    "spot_low",
    "spot_close",
    "futures_symbol",
    "futures_expiry",
    "futures_candle_timestamp",
    "futures_open",
    "futures_high",
    "futures_low",
    "futures_close",
    "futures_volume",
    "futures_vwap",
    "vwap_source",
    "spot_price",
    "futures_price",
    "futures_spot_basis",
    "ema_9",
    "ema_21",
    "ema_50",
    "atr",
    "opening_range_high",
    "opening_range_low",
    "previous_day_high",
    "previous_day_low",
    "relative_volume",
    "india_vix",
    "data_age",
    "freshness_state",
    "candle_alignment_status",
    "generated_setup",
    "generated_status",
    "rule_reasons",
    "signal_key",
]


def diagnostic_key(instrument: str, candle_timestamp: datetime) -> str:
    return f"{instrument}|{candle_timestamp.isoformat()}"


def backup_corrupt_diagnostics(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_suffix(f"{path.suffix}.corrupt-{timestamp}.bak")
    shutil.copy2(path, backup)
    return backup


def load_diagnostic_records(path: Path | str = DEFAULT_DIAGNOSTIC_PATH) -> list[dict[str, Any]]:
    diagnostic_path = Path(path)
    if not diagnostic_path.exists() or diagnostic_path.stat().st_size == 0:
        return []
    try:
        with diagnostic_path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except csv.Error:
        backup_corrupt_diagnostics(diagnostic_path)
        save_diagnostic_records([], diagnostic_path)
        return []


def save_diagnostic_records(records: list[dict[str, Any]], path: Path | str = DEFAULT_DIAGNOSTIC_PATH) -> None:
    diagnostic_path = Path(path)
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    with diagnostic_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DIAGNOSTIC_COLUMNS)
        writer.writeheader()
        writer.writerows(records)


def diagnostic_record(
    item: LiveInstrumentSnapshot,
    evaluation_timestamp: datetime,
    data_mode: DataMode,
    data_age: float | None,
    freshness: FreshnessState | None,
) -> dict[str, Any]:
    context = item.context
    candle = context.candle
    futures_candle = item.metrics
    signal = item.signal
    signal_key = SignalEvaluator.alert_key(signal)
    return {
        "diagnostic_key": diagnostic_key(item.instrument, item.last_completed_5m),
        "evaluation_timestamp": evaluation_timestamp.isoformat(),
        "instrument": item.instrument,
        "data_mode": data_mode.value,
        "spot_symbol": item.instrument,
        "spot_candle_timestamp": candle.timestamp.isoformat(),
        "spot_open": candle.open,
        "spot_high": candle.high,
        "spot_low": candle.low,
        "spot_close": candle.close,
        "futures_symbol": item.futures_symbol or "",
        "futures_expiry": "" if item.futures_expiry is None else str(item.futures_expiry),
        "futures_candle_timestamp": item.last_completed_5m.isoformat(),
        "futures_open": futures_candle.futures_open or "",
        "futures_high": futures_candle.futures_high or "",
        "futures_low": futures_candle.futures_low or "",
        "futures_close": futures_candle.futures_close or "",
        "futures_volume": futures_candle.futures_volume or "",
        "futures_vwap": futures_candle.futures_vwap or "",
        "vwap_source": item.vwap_source,
        "spot_price": futures_candle.spot_price or candle.close,
        "futures_price": futures_candle.futures_price or "",
        "futures_spot_basis": futures_candle.futures_spot_basis or "",
        "ema_9": context.indicators.ema_9,
        "ema_21": context.indicators.ema_21,
        "ema_50": context.indicators.ema_50,
        "atr": context.indicators.atr,
        "opening_range_high": context.indicators.opening_range_high,
        "opening_range_low": context.indicators.opening_range_low,
        "previous_day_high": context.indicators.previous_day_high,
        "previous_day_low": context.indicators.previous_day_low,
        "relative_volume": context.indicators.relative_volume,
        "india_vix": context.indicators.india_vix,
        "data_age": "" if data_age is None else data_age,
        "freshness_state": "" if freshness is None else freshness.value,
        "candle_alignment_status": futures_candle.candle_alignment_status,
        "generated_setup": signal.setup_name.value if signal.setup_name else "",
        "generated_status": signal.signal_status.value,
        "rule_reasons": signal.reason,
        "signal_key": signal_key or "",
    }


def append_diagnostic_record(record: dict[str, Any], path: Path | str = DEFAULT_DIAGNOSTIC_PATH) -> tuple[bool, list[dict[str, Any]]]:
    records = load_diagnostic_records(path)
    key = record.get("diagnostic_key")
    if key and any(existing.get("diagnostic_key") == key for existing in records):
        return False, records
    records.append(record)
    save_diagnostic_records(records, path)
    return True, records


def append_snapshot_diagnostics(
    items: tuple[LiveInstrumentSnapshot, ...],
    evaluation_timestamp: datetime,
    data_age: float | None,
    freshness: FreshnessState | None,
    path: Path | str = DEFAULT_DIAGNOSTIC_PATH,
) -> None:
    for item in items:
        append_diagnostic_record(diagnostic_record(item, evaluation_timestamp, DataMode.LIVE, data_age, freshness), path)
