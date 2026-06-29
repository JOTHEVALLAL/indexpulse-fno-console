from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from .rules import Direction, SetupName, Signal, SignalEvaluator


def format_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}"


def format_ratio(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def vwap_position(signal: Signal) -> str:
    vwap = format_price(signal.vwap_value)
    if signal.setup_name == SetupName.VWAP_RECLAIM:
        return f"VWAP Reclaim ({vwap})"
    if signal.setup_name == SetupName.VWAP_REJECTION:
        return f"VWAP Rejection ({vwap})"
    if signal.spot_price >= signal.vwap_value:
        return f"Above VWAP ({vwap})"
    return f"Below VWAP ({vwap})"


def signal_to_table_row(signal: Signal) -> dict[str, Any]:
    return {
        "Instrument": signal.instrument,
        "Spot": format_price(signal.spot_price),
        "Direction": signal.signal_direction.value if signal.signal_direction else "-",
        "Setup": signal.setup_name.value if signal.setup_name else "-",
        "VWAP Position": vwap_position(signal),
        "Entry": format_price(signal.entry_trigger),
        "Stop Loss": format_price(signal.stop_loss),
        "Target 1": format_price(signal.target_1),
        "Target 2": format_price(signal.target_2),
        "Risk-reward": format_ratio(signal.risk_reward_ratio),
        "Confidence": signal.confidence_level,
        "Status": signal.signal_status.value,
    }


def signal_detail(signal: Signal) -> dict[str, Any]:
    return {
        "Instrument": signal.instrument,
        "Signal direction": signal.signal_direction.value if signal.signal_direction else "-",
        "Setup name": signal.setup_name.value if signal.setup_name else "-",
        "Spot price": format_price(signal.spot_price),
        "VWAP value": format_price(signal.vwap_value),
        "Entry trigger": format_price(signal.entry_trigger),
        "Stop loss": format_price(signal.stop_loss),
        "Target 1": format_price(signal.target_1),
        "Target 2": format_price(signal.target_2),
        "Risk-reward ratio": format_ratio(signal.risk_reward_ratio),
        "Confidence level": signal.confidence_level,
        "Signal status": signal.signal_status.value,
        "Invalidation condition": signal.invalidation_condition,
        "Suggested option side": signal.suggested_option_side or "-",
        "Alert timestamp": signal.alert_timestamp.isoformat(sep=" ", timespec="minutes"),
        "Reason": signal.reason,
        "Duplicate-alert key": SignalEvaluator.alert_key(signal) or "-",
    }


def signal_to_record(signal: Signal) -> dict[str, Any]:
    record = asdict(signal)
    record["signal_direction"] = signal.signal_direction.value if signal.signal_direction else None
    record["setup_name"] = signal.setup_name.value if signal.setup_name else None
    record["signal_status"] = signal.signal_status.value
    record["alert_timestamp"] = signal.alert_timestamp.isoformat()
    record["duplicate_alert_key"] = SignalEvaluator.alert_key(signal)
    record["outcome"] = signal.signal_status.value
    return record


def timestamp_label(timestamp: datetime) -> str:
    return timestamp.strftime("%d %b %Y, %H:%M")


def direction_label(direction: Direction | None) -> str:
    return direction.value if direction else "-"
