"""The one place the mod imports `unrealsdk`, so the rest of the runtime can assume it exists.

Under BL2 the SDK is always importable. Outside the game (the offline tooling, a syntax check)
it is not, and the mod's modules still have to import cleanly so they can be inspected.
"""
from __future__ import annotations

try:
    import unrealsdk  # type: ignore[import-not-found]
    from unrealsdk.hooks import Block, Type, prevent_hooking_direct_calls  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - only outside the game
    unrealsdk = None  # type: ignore[assignment]
    Block = object()  # type: ignore[assignment]
    Type = None  # type: ignore[assignment]
    prevent_hooking_direct_calls = None  # type: ignore[assignment]
