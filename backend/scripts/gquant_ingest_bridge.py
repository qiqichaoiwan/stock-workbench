#!/usr/bin/env python3
"""Compatibility launcher for the GQuant bridge.

Run from the repository root with ``python backend/scripts/gquant_ingest_bridge.py``
when ``gm`` is installed in the active environment.
"""
from pathlib import Path
import sys

_backend = str(Path(__file__).resolve().parents[1])
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from app.marketdata.gquant import run_gquant  # noqa: E402


if __name__ == "__main__":
    run_gquant()
