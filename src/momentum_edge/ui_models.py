from __future__ import annotations

from collections.abc import Iterable

from .formatting import signal_to_table_row
from .rules import Signal, SignalStatus


STATUS_ORDER = [
    SignalStatus.READY,
    SignalStatus.PREPARE,
    SignalStatus.WAIT,
    SignalStatus.AVOID,
    SignalStatus.NO_TRADE,
]

STATUS_ICON = {
    SignalStatus.READY: "[READY]",
    SignalStatus.PREPARE: "[PREPARE]",
    SignalStatus.WAIT: "[WAIT]",
    SignalStatus.AVOID: "[AVOID]",
    SignalStatus.NO_TRADE: "[NO TRADE]",
}


def status_sort_key(signal: Signal) -> tuple[int, str]:
    return (STATUS_ORDER.index(signal.signal_status), signal.instrument)


def sorted_signals(signals: Iterable[Signal]) -> list[Signal]:
    return sorted(signals, key=status_sort_key)


def filtered_signals(
    signals: Iterable[Signal],
    instruments: set[str] | None = None,
    statuses: set[str] | None = None,
    setups: set[str] | None = None,
) -> list[Signal]:
    filtered = []
    for signal in signals:
        setup = signal.setup_name.value if signal.setup_name else "-"
        if instruments and signal.instrument not in instruments:
            continue
        if statuses and signal.signal_status.value not in statuses:
            continue
        if setups and setup not in setups:
            continue
        filtered.append(signal)
    return sorted_signals(filtered)


def signal_to_display_row(signal: Signal) -> dict[str, str]:
    row = signal_to_table_row(signal)
    row["Status"] = f"{STATUS_ICON[signal.signal_status]} {signal.signal_status.value}"
    return row
