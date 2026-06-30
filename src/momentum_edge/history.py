from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .formatting import signal_to_record
from .rules import Signal, SignalEvaluator
from .storage import runtime_data_dir


DEFAULT_HISTORY_PATH = runtime_data_dir() / "alert_history.json"


def backup_corrupt_history(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = path.with_suffix(f"{path.suffix}.corrupt-{timestamp}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def load_alert_history(path: Path | str = DEFAULT_HISTORY_PATH) -> list[dict[str, Any]]:
    history_path = Path(path)
    if not history_path.exists() or history_path.stat().st_size == 0:
        return []

    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup_corrupt_history(history_path)
        save_alert_history([], history_path)
        return []

    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def save_alert_history(records: list[dict[str, Any]], path: Path | str = DEFAULT_HISTORY_PATH) -> None:
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def append_alert(signal: Signal, path: Path | str = DEFAULT_HISTORY_PATH) -> tuple[bool, list[dict[str, Any]]]:
    records = load_alert_history(path)
    alert_key = SignalEvaluator.alert_key(signal)
    if alert_key and any(record.get("duplicate_alert_key") == alert_key for record in records):
        return False, records
    records.append(signal_to_record(signal))
    save_alert_history(records, path)
    return True, records


def clear_alert_history(path: Path | str = DEFAULT_HISTORY_PATH) -> None:
    save_alert_history([], path)
