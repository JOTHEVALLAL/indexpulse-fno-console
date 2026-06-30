from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .candle_utils import ensure_ist
from .live_snapshot import DerivedMetrics, LiveInstrumentSnapshot, LiveSnapshot
from .market_session import build_session_status
from .rules import Candle, Direction, IndicatorSnapshot, MarketContext, Signal, SignalStatus, SetupName
from .scanner_state import DataMode, FreshnessState, ScannerDiagnostics, ScannerState
from .storage import runtime_data_dir


DEFAULT_LIVE_CACHE_PATH = runtime_data_dir() / "latest_live_snapshot.json"


def _dt(value: datetime | None) -> str | None:
    return None if value is None else ensure_ist(value).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    return None if not value else ensure_ist(datetime.fromisoformat(value))


def _candle_payload(candle: Candle) -> dict[str, Any]:
    return {**asdict(candle), "timestamp": _dt(candle.timestamp)}


def _candle_from_payload(payload: dict[str, Any]) -> Candle:
    timestamp = _parse_dt(payload.get("timestamp"))
    if timestamp is None:
        raise ValueError("Candle timestamp is missing.")
    return Candle(
        timestamp=timestamp,
        open=float(payload["open"]),
        high=float(payload["high"]),
        low=float(payload["low"]),
        close=float(payload["close"]),
        volume=float(payload.get("volume", 0) or 0),
    )


def _signal_payload(signal: Signal) -> dict[str, Any]:
    return {
        **asdict(signal),
        "signal_direction": signal.signal_direction.value if signal.signal_direction else None,
        "setup_name": signal.setup_name.value if signal.setup_name else None,
        "signal_status": signal.signal_status.value,
        "alert_timestamp": _dt(signal.alert_timestamp),
    }


def _signal_from_payload(payload: dict[str, Any]) -> Signal:
    timestamp = _parse_dt(payload.get("alert_timestamp"))
    if timestamp is None:
        raise ValueError("Signal timestamp is missing.")
    setup_value = payload.get("setup_name")
    return Signal(
        instrument=str(payload["instrument"]),
        signal_direction=Direction(payload["signal_direction"]) if payload.get("signal_direction") else None,
        setup_name=SetupName(setup_value) if setup_value else None,
        spot_price=float(payload["spot_price"]),
        vwap_value=float(payload["vwap_value"]),
        entry_trigger=payload.get("entry_trigger"),
        stop_loss=payload.get("stop_loss"),
        target_1=payload.get("target_1"),
        target_2=payload.get("target_2"),
        risk_reward_ratio=payload.get("risk_reward_ratio"),
        confidence_level=str(payload.get("confidence_level", "")),
        signal_status=SignalStatus(payload["signal_status"]),
        invalidation_condition=str(payload.get("invalidation_condition", "")),
        suggested_option_side=payload.get("suggested_option_side"),
        alert_timestamp=timestamp,
        reason=str(payload.get("reason", "")),
    )


def _context_payload(context: MarketContext) -> dict[str, Any]:
    indicators = asdict(context.indicators)
    indicators["trend_15m"] = context.indicators.trend_15m.value if context.indicators.trend_15m else None
    return {
        "instrument": context.instrument,
        "candle": _candle_payload(context.candle),
        "previous_candles": [_candle_payload(candle) for candle in context.previous_candles],
        "indicators": indicators,
    }


def _context_from_payload(payload: dict[str, Any]) -> MarketContext:
    indicators_payload = dict(payload["indicators"])
    trend = indicators_payload.get("trend_15m")
    indicators_payload["trend_15m"] = Direction(trend) if trend else None
    return MarketContext(
        instrument=str(payload["instrument"]),
        candle=_candle_from_payload(payload["candle"]),
        previous_candles=tuple(_candle_from_payload(item) for item in payload.get("previous_candles", [])),
        indicators=IndicatorSnapshot(**indicators_payload),
    )


def _diagnostics_payload(diagnostics: ScannerDiagnostics) -> dict[str, Any]:
    payload = asdict(diagnostics)
    payload["data_mode"] = diagnostics.data_mode.value
    payload["scanner_state"] = diagnostics.scanner_state.value
    payload["data_freshness"] = diagnostics.data_freshness.value if diagnostics.data_freshness else None
    for key in (
        "last_successful_fetch",
        "last_completed_5m_candle",
        "last_completed_15m_candle",
        "last_evaluation",
        "next_expected_evaluation",
        "historical_range_from",
        "historical_range_to",
        "current_ist_time",
        "latest_futures_candle",
        "cached_snapshot_timestamp",
    ):
        payload[key] = _dt(payload.get(key))
    return payload


def _diagnostics_from_payload(payload: dict[str, Any]) -> ScannerDiagnostics:
    if payload.get("data_mode") != DataMode.LIVE.value:
        raise ValueError("Cached snapshot is not LIVE data.")
    values = dict(payload)
    values["data_mode"] = DataMode(values["data_mode"])
    values["scanner_state"] = ScannerState(values["scanner_state"])
    values["data_freshness"] = FreshnessState(values["data_freshness"]) if values.get("data_freshness") else None
    for key in (
        "last_successful_fetch",
        "last_completed_5m_candle",
        "last_completed_15m_candle",
        "last_evaluation",
        "next_expected_evaluation",
        "historical_range_from",
        "historical_range_to",
        "current_ist_time",
        "latest_futures_candle",
        "cached_snapshot_timestamp",
    ):
        values[key] = _parse_dt(values.get(key))
    return ScannerDiagnostics(**values)


def snapshot_payload(snapshot: LiveSnapshot) -> dict[str, Any]:
    return {
        "cache_version": 1,
        "mode": snapshot.diagnostics.data_mode.value,
        "snapshot_timestamp": _dt(snapshot.evaluated_at),
        "diagnostics": _diagnostics_payload(snapshot.diagnostics),
        "instruments": [
            {
                "instrument": item.instrument,
                "context": _context_payload(item.context),
                "signal": _signal_payload(item.signal),
                "metrics": asdict(item.metrics),
                "last_completed_5m": _dt(item.last_completed_5m),
                "last_completed_15m": _dt(item.last_completed_15m),
                "vwap_source": item.vwap_source,
                "futures_symbol": item.futures_symbol,
                "futures_expiry": None if item.futures_expiry is None else str(item.futures_expiry),
                "is_cached": item.is_cached,
                "action_block_reason": item.action_block_reason,
            }
            for item in snapshot.instruments
        ],
    }


def snapshot_from_payload(payload: dict[str, Any], cache_source: str | None = None) -> LiveSnapshot:
    if payload.get("mode") != DataMode.LIVE.value:
        raise ValueError("Cached snapshot is not LIVE data.")
    evaluated_at = _parse_dt(payload.get("snapshot_timestamp"))
    diagnostics = _diagnostics_from_payload(payload["diagnostics"])
    instruments = []
    for item in payload.get("instruments", []):
        last_5m = _parse_dt(item.get("last_completed_5m"))
        last_15m = _parse_dt(item.get("last_completed_15m"))
        if last_5m is None or last_15m is None:
            raise ValueError("Cached instrument candle timestamps are missing.")
        instruments.append(
            LiveInstrumentSnapshot(
                instrument=str(item["instrument"]),
                context=_context_from_payload(item["context"]),
                signal=_signal_from_payload(item["signal"]),
                metrics=DerivedMetrics(**item["metrics"]),
                last_completed_5m=last_5m,
                last_completed_15m=last_15m,
                vwap_source=str(item["vwap_source"]),
                futures_symbol=item.get("futures_symbol"),
                futures_expiry=item.get("futures_expiry"),
                is_cached=True,
                action_block_reason="Cached LIVE session reference.",
            )
        )
    if not instruments:
        raise ValueError("Cached LIVE snapshot has no instruments.")
    source = cache_source or str(DEFAULT_LIVE_CACHE_PATH)
    diagnostics = ScannerDiagnostics(
        **{
            **asdict(diagnostics),
            "data_mode": diagnostics.data_mode,
            "scanner_state": diagnostics.scanner_state,
            "data_freshness": diagnostics.data_freshness,
            "cached_live_snapshot_available": True,
            "cached_snapshot_timestamp": evaluated_at,
            "cached_session_date": max(item.last_completed_5m.date().isoformat() for item in instruments),
            "cache_source": source,
        }
    )
    return LiveSnapshot(tuple(instruments), diagnostics, evaluated_at or instruments[0].last_completed_5m)


def save_live_snapshot(snapshot: LiveSnapshot, path: Path | str = DEFAULT_LIVE_CACHE_PATH) -> None:
    if snapshot.diagnostics.data_mode != DataMode.LIVE or not snapshot.instruments:
        return
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(snapshot_payload(snapshot), indent=2), encoding="utf-8")


def backup_malformed_cache(path: Path) -> None:
    if not path.exists():
        return
    backup = path.with_suffix(f"{path.suffix}.malformed.bak")
    shutil.copy2(path, backup)


def load_live_snapshot(path: Path | str = DEFAULT_LIVE_CACHE_PATH) -> LiveSnapshot | None:
    cache_path = Path(path)
    if not cache_path.exists() or cache_path.stat().st_size == 0:
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return snapshot_from_payload(payload, str(cache_path))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        try:
            backup_malformed_cache(cache_path)
        except OSError:
            pass
        return None


def cached_snapshot_for_display(snapshot: LiveSnapshot, now: datetime, reason: str | None = None, cache_source: str | None = None) -> LiveSnapshot:
    latest_5m = max(item.last_completed_5m for item in snapshot.instruments)
    latest_15m = max(item.last_completed_15m for item in snapshot.instruments)
    session_status = build_session_status(now, latest_5m)
    diagnostics = ScannerDiagnostics(
        **{
            **asdict(snapshot.diagnostics),
            "data_mode": snapshot.diagnostics.data_mode,
            "scanner_state": ScannerState.CACHED_SESSION,
            "data_freshness": None,
            "data_age_seconds": session_status.candle_age_seconds,
            "last_completed_5m_candle": latest_5m,
            "last_completed_15m_candle": latest_15m,
            "session_state": session_status.session_state.value,
            "display_freshness": session_status.freshness_state.value,
            "freshness_message": session_status.message,
            "signals_actionable": False,
            "new_ready_allowed": False,
            "new_alerts_allowed": False,
            "action_block_reason": session_status.block_reason or "Blocked: cached LIVE session reference",
            "current_ist_time": ensure_ist(now),
            "current_calendar_date": ensure_ist(now).date().isoformat(),
            "cached_live_snapshot_available": True,
            "cached_snapshot_timestamp": snapshot.evaluated_at,
            "cached_session_date": latest_5m.date().isoformat(),
            "cache_source": cache_source or str(DEFAULT_LIVE_CACHE_PATH),
            "display_source": "CACHED LIVE",
            "live_fetch_available": False,
            "current_error": reason,
        }
    )
    return LiveSnapshot(
        tuple(replace_cached_item(item) for item in snapshot.instruments),
        diagnostics,
        ensure_ist(now),
    )


def replace_cached_item(item: LiveInstrumentSnapshot) -> LiveInstrumentSnapshot:
    return LiveInstrumentSnapshot(
        instrument=item.instrument,
        context=item.context,
        signal=item.signal,
        metrics=item.metrics,
        last_completed_5m=item.last_completed_5m,
        last_completed_15m=item.last_completed_15m,
        vwap_source=item.vwap_source,
        futures_symbol=item.futures_symbol,
        futures_expiry=item.futures_expiry,
        is_cached=True,
        action_block_reason="Cached LIVE session reference.",
    )
