"""Application-level market-data SLO evaluation.

The exchange feed contract and the application's availability SLO are separate
things.  This module deliberately reports ``ready=False`` unless every local
guard is green: the stream is enabled, an authorized provider is connected,
Redis is reachable, and the latest snapshot is fresh, in-session, and free of
pending sequence gaps.  A public snapshot or an in-process memory cache can
still be useful for research, but never satisfies this readiness contract.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable

UTC = timezone.utc


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _age_seconds(value: Any, now: datetime) -> float | None:
    number = _number(value)
    if number is not None and number >= 0:
        return number
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    age = (now - stamp.astimezone(UTC)).total_seconds()
    # A future heartbeat is not fresh.  Returning ``None`` makes the readiness
    # gate fail closed instead of allowing a clock-skewed/malformed producer to
    # claim zero latency.
    return age if age >= 0 else None


def _provider_for_slo(
    adapter: dict[str, Any] | None,
    shared_storage: dict[str, Any] | None,
    *,
    storage_mode: str,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    """Prefer a worker heartbeat when the API is split from the adapter."""
    adapter = adapter if isinstance(adapter, dict) else {}
    shared_storage = shared_storage if isinstance(shared_storage, dict) else {}
    shared = shared_storage.get("provider_health")
    if isinstance(shared, dict):
        # RedisQuoteStore exposes {provider: health}; tolerate a direct health
        # object as well for lightweight adapters and test doubles.
        if isinstance(shared.get("futu"), dict):
            return dict(shared["futu"]), "redis"
        if shared.get("provider"):
            return dict(shared), "redis"
    if storage_mode == "redis":
        # In split mode a local adapter is not authoritative.  Falling back to
        # it would let an accidentally embedded provider bypass the worker
        # heartbeat and make a dead worker look ready.
        return {}, "redis"
    return dict(adapter), "local"


def _expected_codes(values: Iterable[Any] | None) -> set[str]:
    codes: set[str] = set()
    for raw in values or ():
        text = str(raw or "").strip().upper()
        if "." in text:
            text = text.rsplit(".", 1)[-1]
        if text.isdigit() and len(text) <= 6:
            codes.add(text.zfill(6))
    return codes


def evaluate_slo(
    hub_health: dict[str, Any] | None,
    latest_snapshot: dict[str, Any] | None,
    adapter_health: dict[str, Any] | None,
    *,
    stream_enabled: bool,
    storage_mode: str,
    now: datetime | None = None,
    max_age_seconds: float = 3.0,
    delayed_max_age_seconds: float = 15.0,
    expected_symbols: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Return a JSON-safe readiness report for the application data plane."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    hub_health = hub_health if isinstance(hub_health, dict) else {}
    latest_snapshot = latest_snapshot if isinstance(latest_snapshot, dict) else {}
    items = latest_snapshot.get("items")
    items = items if isinstance(items, dict) else {}
    shared_storage = hub_health.get("shared_storage")
    shared_storage = shared_storage if isinstance(shared_storage, dict) else {}
    provider, provider_source = _provider_for_slo(
        adapter_health, shared_storage, storage_mode=storage_mode, now=now
    )

    reasons: list[str] = []
    checks: dict[str, bool] = {}
    checks["stream_enabled"] = bool(stream_enabled)
    if not checks["stream_enabled"]:
        reasons.append("stream_disabled")

    checks["single_tenant"] = bool(provider.get("single_tenant"))
    if not checks["single_tenant"]:
        reasons.append("single_tenant_required")

    checks["provider_enabled"] = bool(provider.get("enabled"))
    if not checks["provider_enabled"]:
        reasons.append("provider_disabled")

    checks["provider_authorized"] = bool(provider.get("authorized"))
    if not checks["provider_authorized"]:
        reasons.append("provider_not_authorized")

    state = str(provider.get("state") or "unknown").lower()
    checks["provider_connected"] = state in {"connected", "ready"}
    if not checks["provider_connected"]:
        reasons.append(f"provider_{state}")

    message_age = _number(provider.get("message_age_seconds"))
    if message_age is not None and message_age < 0:
        message_age = None
    heartbeat_limit = _number(provider.get("heartbeat_timeout_seconds"))
    if heartbeat_limit is None or heartbeat_limit <= 0:
        heartbeat_limit = max(delayed_max_age_seconds * 2.0, 15.0)
    checks["provider_heartbeat"] = message_age is not None and message_age <= heartbeat_limit
    if message_age is None:
        reasons.append("provider_heartbeat_missing")
    elif message_age > heartbeat_limit:
        reasons.append("provider_heartbeat_stale")

    # In split mode the Redis heartbeat itself must be fresh.  Otherwise a
    # worker that died after publishing ``state=connected`` would leave a
    # permanently green value in Redis while no new ticks arrive.
    heartbeat_update_age = _age_seconds(provider.get("updated_at"), now)
    if provider_source == "redis":
        checks["provider_health_update"] = (
            heartbeat_update_age is not None and heartbeat_update_age <= heartbeat_limit
        )
        if heartbeat_update_age is None:
            reasons.append("provider_health_update_missing")
        elif heartbeat_update_age > heartbeat_limit:
            reasons.append("provider_health_update_stale")
    else:
        checks["provider_health_update"] = True

    # A Redis-backed latest snapshot is required for a worker/API split.  The
    # memory mode is intentionally never reported as production SLO-ready.
    checks["shared_storage"] = storage_mode == "redis" and bool(shared_storage.get("connected"))
    if not checks["shared_storage"]:
        reasons.append("shared_storage_unavailable")

    ages = [_number(item.get("age_seconds")) for item in items.values()
            if isinstance(item, dict)]
    ages = [age for age in ages if age is not None and age >= 0]
    latest_max_age = max(ages) if ages else None
    realtime_items = [item for item in items.values()
                      if isinstance(item, dict) and item.get("quality") == "realtime"]
    gap_count = sum(1 for item in items.values()
                    if isinstance(item, dict) and bool(item.get("sequence_gap")))
    checks["latest_present"] = bool(items)
    checks["latest_realtime"] = bool(items) and len(realtime_items) == len(items)
    checks["latest_fresh"] = bool(items) and all(
        _number(item.get("age_seconds")) is not None and
        float(item.get("age_seconds")) >= 0 and
        float(item.get("age_seconds")) <= max_age_seconds
        for item in items.values() if isinstance(item, dict)
    )
    expected = _expected_codes(expected_symbols)
    missing_expected = sorted(expected - set(items))
    checks["latest_coverage"] = not expected or not missing_expected
    if missing_expected:
        reasons.append("latest_symbols_missing")
    checks["sequence_gap_clear"] = gap_count == 0
    if not checks["latest_present"]:
        reasons.append("latest_missing")
    if not checks["latest_realtime"]:
        reasons.append("latest_not_realtime")
    if not checks["latest_fresh"]:
        reasons.append("latest_stale")
    if not checks["sequence_gap_clear"]:
        reasons.append("sequence_gap_pending")

    # Deduplicate while preserving diagnostic order.
    reasons = list(dict.fromkeys(reasons))
    ready = bool(checks) and all(checks.values()) and not reasons
    return {
        "ready": ready,
        "scope": "application_slo",
        "provider": provider.get("provider") or "futu",
        "provider_source": provider_source,
        "provider_state": state,
        "message_age_seconds": message_age,
        "provider_health_age_seconds": (round(heartbeat_update_age, 3)
                                        if heartbeat_update_age is not None else None),
        "heartbeat_timeout_seconds": heartbeat_limit,
        "latest_count": len(items),
        "expected_symbols": sorted(expected),
        "missing_symbols": missing_expected,
        "latest_max_age_seconds": round(latest_max_age, 3) if latest_max_age is not None else None,
        "realtime_count": len(realtime_items),
        "sequence_gap_count": gap_count,
        "redis_connected": bool(shared_storage.get("connected")),
        "storage_mode": storage_mode,
        "checks": checks,
        "reasons": reasons,
        "as_of": now.isoformat(),
    }
