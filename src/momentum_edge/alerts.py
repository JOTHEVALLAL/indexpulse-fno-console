from __future__ import annotations

from .formatting import format_price, format_ratio
from .rules import Signal, SignalEvaluator


def format_telegram_alert(signal: Signal) -> str:
    direction = signal.signal_direction.value if signal.signal_direction else "-"
    setup = signal.setup_name.value if signal.setup_name else "-"
    option_side = signal.suggested_option_side or "-"
    alert_key = SignalEvaluator.alert_key(signal) or "-"
    timestamp = signal.alert_timestamp.isoformat(sep=" ", timespec="minutes")

    return "\n".join(
        [
            "Momentum Edge F&O Console - SAMPLE DATA",
            f"{signal.signal_status.value}: {signal.instrument} {direction}",
            f"Setup: {setup}",
            f"Spot: {format_price(signal.spot_price)} | VWAP: {format_price(signal.vwap_value)}",
            f"Entry: {format_price(signal.entry_trigger)}",
            f"SL: {format_price(signal.stop_loss)}",
            f"T1: {format_price(signal.target_1)} | T2: {format_price(signal.target_2)}",
            f"RR: {format_ratio(signal.risk_reward_ratio)} | Confidence: {signal.confidence_level}",
            f"Option side: {option_side}",
            f"Invalidation: {signal.invalidation_condition}",
            f"Reason: {signal.reason}",
            f"Alert key: {alert_key}",
            f"Timestamp: {timestamp}",
            "Preview only. No Telegram message sent. No order placed.",
        ]
    )
