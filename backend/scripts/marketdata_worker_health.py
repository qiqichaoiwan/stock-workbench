#!/usr/bin/env python3
"""Container liveness check for the standalone market-data worker.

This check validates the worker heartbeat and Redis connection, not the quote
freshness SLO. A-share markets are closed for long periods, so an old/empty
tick stream outside trading hours must not make a healthy worker look dead.
The API ``/api/marketdata/health`` endpoint remains the source of truth for
quote freshness and application readiness.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

_BACKEND = str(Path(__file__).resolve().parents[1])
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.config import settings  # noqa: E402
from app.marketdata.redis_store import RedisQuoteStore  # noqa: E402

UTC = timezone.utc


def _age_seconds(value: object) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    age = (datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds()
    return age if age >= 0 else None


def main() -> int:
    if settings.marketdata_process_role != "worker":
        return 1
    if settings.marketdata_storage_mode != "redis" or not settings.marketdata_redis_url.strip():
        return 1

    store = RedisQuoteStore(
        settings.marketdata_redis_url,
        prefix=settings.marketdata_redis_prefix,
        stream_maxlen=settings.marketdata_redis_stream_maxlen,
    )
    health = store.health()
    if not health.get("connected"):
        return 1
    provider = (health.get("provider_health") or {}).get("futu")
    if not isinstance(provider, dict):
        return 1

    # The worker writes this timestamp on every supervision interval. Keep the
    # budget wider than the default 10s interval to tolerate one delayed
    # heartbeat while still detecting a wedged process promptly.
    updated_age = _age_seconds(provider.get("updated_at"))
    timeout = provider.get("heartbeat_timeout_seconds")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 30.0
    timeout = max(15.0, timeout)
    if updated_age is None or updated_age > timeout * 2:
        return 1

    # ``stale`` is allowed here because the exchange is closed for much of the
    # day and no quote callback is expected. The API SLO gate still rejects
    # stale/realtime-missing snapshots during an active session.
    state = str(provider.get("state") or "").lower()
    if state not in {"connected", "ready", "stale"}:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - container entry point
    raise SystemExit(main())
