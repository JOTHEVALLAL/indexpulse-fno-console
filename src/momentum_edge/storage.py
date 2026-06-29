from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DATA_DIR = Path("data")


@dataclass(frozen=True)
class DataDirectoryStatus:
    path: Path
    exists: bool
    writable: bool
    error: str | None = None


def ensure_data_directory(path: Path | str = DATA_DIR) -> DataDirectoryStatus:
    data_path = Path(path)
    try:
        data_path.mkdir(parents=True, exist_ok=True)
        probe = data_path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return DataDirectoryStatus(path=data_path, exists=True, writable=True)
    except OSError as exc:
        return DataDirectoryStatus(path=data_path, exists=data_path.exists(), writable=False, error=str(exc))
