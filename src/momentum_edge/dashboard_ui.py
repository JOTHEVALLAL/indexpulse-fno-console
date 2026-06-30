from __future__ import annotations

from datetime import datetime
import re


AUTO_REFRESH_INTERVAL_SECONDS = 30
SCANNER_CARD_CLASS = "indexpulse-status-card"
SCANNER_CARD_LABEL_CLASS = "indexpulse-status-label"
SUMMARY_CARD_CLASS = "indexpulse-summary-card"
SUMMARY_CARD_LABEL_CLASS = "indexpulse-summary-label"
SUMMARY_CARD_VALUE_CLASS = "indexpulse-summary-value"
SUMMARY_CARD_TIMESTAMP_CLASS = "indexpulse-summary-value timestamp"


def format_dashboard_timestamp(value: datetime | None, include_seconds: bool = False) -> str:
    if value is None:
        return "-"
    return value.strftime("%d-%b %H:%M:%S" if include_seconds else "%d-%b %H:%M")


def compact_vwap_source(source: str | None) -> str:
    if not source:
        return "-"
    values = [item.strip() for item in source.split(",") if item.strip()]
    compacted = [_compact_single_source(item) for item in values]
    return ", ".join(dict.fromkeys(compacted)) or "-"


def _compact_single_source(source: str) -> str:
    normalized = source.upper()
    if normalized in {"SAMPLE_DATA", "UNDERLYING_VOLUME", "UNAVAILABLE"}:
        return normalized.replace("_", " ")
    match = re.match(r"^(BANKNIFTY|NIFTY)(\d{2})([A-Z]{3})(FUT)$", normalized)
    if match:
        underlying, _year, month, instrument_type = match.groups()
        return f"{underlying} {month} {instrument_type}"
    if normalized.endswith("FUT"):
        return normalized[:-3].strip() + " FUT"
    return normalized


def normalize_status_display(value: str | None) -> str:
    if not value:
        return "-"
    text = value.strip()
    match = re.match(r"^\[([A-Z_]+)\]\s*([A-Z_]+)?$", text)
    if match:
        return match.group(2) or match.group(1)
    return text
