from __future__ import annotations

from pathlib import Path

_REAL_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "momentum_edge"
__path__ = [str(_REAL_PACKAGE)]

_real_init = _REAL_PACKAGE / "__init__.py"
exec(compile(_real_init.read_text(encoding="utf-8"), str(_real_init), "exec"), globals())
