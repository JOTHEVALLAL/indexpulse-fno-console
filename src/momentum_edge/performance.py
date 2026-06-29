from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from .outcomes import OutcomeConfig, performance_summary, time_bucket


def filter_outcome_records(
    records: list[dict[str, Any]],
    start_date: date | None = None,
    end_date: date | None = None,
    instruments: set[str] | None = None,
    setups: set[str] | None = None,
    directions: set[str] | None = None,
    confidences: set[str] | None = None,
    outcomes: set[str] | None = None,
    data_modes: set[str] | None = None,
) -> list[dict[str, Any]]:
    filtered = []
    for record in records:
        session_date = date.fromisoformat(record["session_date"])
        if start_date and session_date < start_date:
            continue
        if end_date and session_date > end_date:
            continue
        if instruments and record.get("instrument") not in instruments:
            continue
        if setups and (record.get("setup") or "-") not in setups:
            continue
        if directions and (record.get("direction") or "-") not in directions:
            continue
        if confidences and record.get("confidence") not in confidences:
            continue
        if outcomes and (record.get("execution_state") or "-") not in outcomes:
            continue
        if data_modes and record.get("data_mode") not in data_modes:
            continue
        filtered.append(record)
    return filtered


def prepare_to_ready_conversion(lifecycle_records: list[dict[str, Any]]) -> float:
    prepare_count = sum(1 for record in lifecycle_records if record.get("current_status") == "PREPARE" or int(record.get("candles_in_prepare") or 0) > 0)
    ready_count = sum(1 for record in lifecycle_records if record.get("current_status") == "READY")
    total = prepare_count + ready_count
    return 0.0 if total == 0 else ready_count / total


def breakdown(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if field == "hour_bucket":
            try:
                key = time_bucket(datetime.fromisoformat(record["ready_time"]))
            except (KeyError, ValueError):
                key = "UNKNOWN"
        elif field == "weekday":
            try:
                key = datetime.fromisoformat(record["ready_time"]).strftime("%A")
            except (KeyError, ValueError):
                key = "UNKNOWN"
        else:
            key = str(record.get(field) or "-")
        groups[key].append(record)
    rows = []
    for key, group in sorted(groups.items()):
        summary = performance_summary(group)
        rows.append({"group": key, **summary})
    return rows


def performance_dashboard_summary(outcomes: list[dict[str, Any]], lifecycle_records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = performance_summary(outcomes)
    total_prepare = sum(1 for record in lifecycle_records if record.get("current_status") == "PREPARE" or int(record.get("candles_in_prepare") or 0) > 0)
    summary["total_prepare_signals"] = total_prepare
    summary["prepare_to_ready_conversion_rate"] = prepare_to_ready_conversion(lifecycle_records)
    return summary
