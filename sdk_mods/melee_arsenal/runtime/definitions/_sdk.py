"""Single guarded `unrealsdk` import for the definitions package."""
from __future__ import annotations

try:
    import unrealsdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    unrealsdk = None  # type: ignore[assignment]
