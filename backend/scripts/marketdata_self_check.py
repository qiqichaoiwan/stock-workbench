"""Run the local market-data SLO gate from a deployment shell.

Usage::

    PYTHONPATH=backend python backend/scripts/marketdata_self_check.py

The command emits JSON and exits 0 only when the application-level SLO gate is
green.  It never claims that a free broker/public feed has an exchange SLA.
"""
from __future__ import annotations

import json
import sys

from app.config import settings
from app.marketdata.health import evaluate_slo
from app.marketdata.hub import get_hub


def main() -> int:
    hub = get_hub()
    hub_health = hub.health()
    snapshot = hub.latest()
    from app.marketdata.futu import get_provider

    adapter = get_provider().health()
    report = evaluate_slo(
        hub_health,
        snapshot,
        adapter,
        stream_enabled=settings.marketdata_stream_enabled,
        storage_mode=settings.marketdata_storage_mode,
        max_age_seconds=settings.marketdata_max_age_seconds,
        delayed_max_age_seconds=settings.marketdata_delayed_max_age_seconds,
        expected_symbols=settings.marketdata_symbols.split(","),
    )
    output = {"slo": report, "hub": hub_health, "adapter": adapter}
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
