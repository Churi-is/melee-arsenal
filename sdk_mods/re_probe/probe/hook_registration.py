"""Register SDK hooks without losing track of a successfully installed callback.

Some unrealsdk versions can return False when appending a new identifier to an
existing hook collection, even though they inserted it. Verify the registry.
Never claim ownership of an identifier that existed before this call.
"""
from __future__ import annotations

from typing import Any, Callable


def install(add: Callable[..., Any], has: Callable[..., bool], remove: Callable[..., Any],
            path: str, kind: Any, identifier: str, callback: Callable[..., Any]) -> bool:
    if has(path, kind, identifier):
        return False
    try:
        added = add(path, kind, identifier, callback)
        return bool(added) or bool(has(path, kind, identifier))
    except Exception:
        # If add raised after inserting, do not leave an untracked callback behind.
        if has(path, kind, identifier):
            remove(path, kind, identifier)
        raise
