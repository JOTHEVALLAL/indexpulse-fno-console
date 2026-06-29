from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any

from .formatting import format_price
from .rules import Signal, SignalEvaluator, SignalStatus


class TradeStatus(str, Enum):
    SIGNAL_READY = "SIGNAL READY"
    ENTRY_TRIGGERED = "ENTRY TRIGGERED"
    TARGET_1_REACHED = "TARGET 1 REACHED"
    TARGET_2_REACHED = "TARGET 2 REACHED"
    STOP_LOSS_HIT = "STOP LOSS HIT"
    INVALIDATED = "INVALIDATED"
    CLOSED_MANUALLY = "CLOSED MANUALLY"


@dataclass(frozen=True)
class ActiveTrade:
    key: str
    instrument: str
    direction: str
    setup: str
    entry: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    option_side: str | None
    status: TradeStatus
    created_at: datetime
    updated_at: datetime


def create_active_trade(signal: Signal, status: TradeStatus = TradeStatus.SIGNAL_READY) -> ActiveTrade:
    if signal.signal_status != SignalStatus.READY:
        raise ValueError("Only READY signals can be added to active trades.")

    direction = signal.signal_direction.value if signal.signal_direction else "-"
    setup = signal.setup_name.value if signal.setup_name else "-"
    now = datetime.now()
    return ActiveTrade(
        key=SignalEvaluator.alert_key(signal) or f"{signal.instrument}|{setup}",
        instrument=signal.instrument,
        direction=direction,
        setup=setup,
        entry=signal.entry_trigger,
        stop_loss=signal.stop_loss,
        target_1=signal.target_1,
        target_2=signal.target_2,
        option_side=signal.suggested_option_side,
        status=status,
        created_at=now,
        updated_at=now,
    )


def add_active_trade(active_trades: dict[str, ActiveTrade], signal: Signal) -> tuple[bool, str]:
    trade = create_active_trade(signal)
    if trade.key in active_trades:
        return False, "Active trade already exists for this signal."
    active_trades[trade.key] = trade
    return True, "Added to active trades."


def update_trade_status(trade: ActiveTrade, status: TradeStatus) -> ActiveTrade:
    return replace(trade, status=status, updated_at=datetime.now())


def trade_to_row(trade: ActiveTrade) -> dict[str, Any]:
    return {
        "Instrument": trade.instrument,
        "Direction": trade.direction,
        "Setup": trade.setup,
        "Entry": format_price(trade.entry),
        "Stop Loss": format_price(trade.stop_loss),
        "Target 1": format_price(trade.target_1),
        "Target 2": format_price(trade.target_2),
        "Option Side": trade.option_side or "-",
        "Status": trade.status.value,
        "Created": trade.created_at.isoformat(sep=" ", timespec="minutes"),
        "Last Updated": trade.updated_at.isoformat(sep=" ", timespec="minutes"),
        "Key": trade.key,
    }
