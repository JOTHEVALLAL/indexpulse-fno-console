from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .formatting import format_price
from .rules import Signal, SignalEvaluator, SignalStatus


@dataclass(frozen=True)
class WatchlistEntry:
    key: str
    instrument: str
    direction: str
    setup: str
    entry: float | None
    status: str
    reason: str
    created_at: datetime


def create_watchlist_entry(signal: Signal) -> WatchlistEntry:
    if signal.signal_status != SignalStatus.PREPARE:
        raise ValueError("Only PREPARE signals can be added to the watchlist.")

    return WatchlistEntry(
        key=SignalEvaluator.alert_key(signal) or signal.instrument,
        instrument=signal.instrument,
        direction=signal.signal_direction.value if signal.signal_direction else "-",
        setup=signal.setup_name.value if signal.setup_name else "-",
        entry=signal.entry_trigger,
        status=signal.signal_status.value,
        reason=signal.reason,
        created_at=datetime.now(),
    )


def add_watchlist_entry(watchlist: dict[str, WatchlistEntry], signal: Signal) -> tuple[bool, str]:
    entry = create_watchlist_entry(signal)
    if entry.key in watchlist:
        return False, "Watchlist entry already exists for this signal."
    watchlist[entry.key] = entry
    return True, "Added to watchlist."


def watchlist_to_row(entry: WatchlistEntry) -> dict[str, Any]:
    return {
        "Instrument": entry.instrument,
        "Direction": entry.direction,
        "Setup": entry.setup,
        "Entry": format_price(entry.entry),
        "Status": entry.status,
        "Reason": entry.reason,
        "Created": entry.created_at.isoformat(sep=" ", timespec="minutes"),
        "Key": entry.key,
    }
