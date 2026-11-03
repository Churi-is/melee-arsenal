#!/usr/bin/env python3
"""Generate the modular Inkblade kit through the staged pipeline (`tools/inkblade_gen`)."""
from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.inkblade_gen.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
