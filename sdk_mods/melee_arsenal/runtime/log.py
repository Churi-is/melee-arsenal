"""Logging utilities for Melee Arsenal SDK runtime."""
from __future__ import annotations
from .sdk import unrealsdk

from datetime import datetime
from pathlib import Path
from typing import Any


try:
    from mods_base import MODS_DIR  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    MODS_DIR = Path(__file__).resolve().parents[2]


LOG_FILE: Path = Path(MODS_DIR) / "melee_arsenal" / "melee_arsenal.log"
LOG_MAX_BYTES: int = 1_000_000
LOG_BACKUP_COUNT: int = 2

_DEBUG_ONCE_KEYS: set[str] = set()


def _should_log_debug() -> bool:
    """Return True if debug logging is enabled, or if STATE is unavailable (offline tests)."""
    try:
        from .state import STATE  # local import to avoid cycle

        return bool(getattr(STATE, "debug_attack_log", False))
    except Exception:
        return True


def log_debug(msg: str) -> None:
    """Debug-level log — gated by `STATE.debug_attack_log` when inside BL2."""
    if not _should_log_debug():
        return
    line = f"[MeleeArsenal][DEBUG] {msg}"
    if unrealsdk is not None:
        try:
            unrealsdk.logging.info(line)
        except Exception:
            pass
    _append_file(line)


def log_debug_once(key: str, msg: str) -> None:
    """Log a debug message once per unique `key` while debug logging is enabled."""
    if key in _DEBUG_ONCE_KEYS or not _should_log_debug():
        return
    _DEBUG_ONCE_KEYS.add(key)
    log_debug(msg)


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


def clear_debug_once_cache() -> None:
    """Test helper — reset the once-per-site cache."""
    _DEBUG_ONCE_KEYS.clear()


def _rotate_log_if_needed() -> None:
    try:
        if not LOG_FILE.exists() or LOG_FILE.stat().st_size < LOG_MAX_BYTES:
            return
        for idx in range(LOG_BACKUP_COUNT - 1, 0, -1):
            src = LOG_FILE.with_suffix(f".{idx}.log")
            dst = LOG_FILE.with_suffix(f"{idx + 1}.log")
            if src.exists():
                src.replace(dst)
        LOG_FILE.replace(LOG_FILE.with_suffix(".1.log"))
    except Exception:
        pass


def _append_file(line: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_needed()
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
