#!/usr/bin/env python3
"""Run the standalone Futu OpenD market-data worker.

Usage from the repository root::

    python backend/scripts/marketdata_worker.py --dry-run

The launcher adds ``backend/`` to ``sys.path`` and delegates to the module
entry point.  It does not import FastAPI or start the application scheduler.
"""
from pathlib import Path
import sys

_backend = str(Path(__file__).resolve().parents[1])
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from app.marketdata.worker import main  # noqa: E402


if __name__ == "__main__":  # pragma: no cover - process wrapper
    raise SystemExit(main())
