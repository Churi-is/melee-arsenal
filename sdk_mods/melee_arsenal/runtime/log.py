"""Logging utilities for Melee Arsenal SDK runtime."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import unrealsdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    unrealsdk = None  # type: ignore[assignment]

try:
    from mods_base import MODS_DIR  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    MODS_DIR = Path(__file__).resolve().parents[2]


LOG_FILE: Path = Path(MODS_DIR) / "melee_arsenal" / "melee_arsenal.log"


def log_info(msg: str) -> None:
    line = f"[MeleeArsenal] {msg}"
    if unrealsdk is not None:
        try:
            unrealsdk.logging.info(line)
        except Exception:
            pass
    _append_file(line)


def log_warn(msg: str) -> None:
    line = f"[MeleeArsenal][WARN] {msg}"
    if unrealsdk is not None:
        try:
            unrealsdk.logging.warning(line)
        except Exception:
            pass
    _append_file(line)


def log_error(msg: str) -> None:
    line = f"[MeleeArsenal][ERROR] {msg}"
    if unrealsdk is not None:
        try:
            unrealsdk.logging.error(line)
        except Exception:
            pass
    _append_file(line)


def _append_file(line: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with LOG_FILE.open("a", encoding="utf-8") as fp:
            fp.write(f"[{stamp}] {line}\n")
    except Exception:
        pass


def obj_path(obj: Any) -> str:
    if obj is None:
        return ""
    try:
        return str(obj._path_name())
    except Exception:
        return ""
