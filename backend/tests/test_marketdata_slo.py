from datetime import datetime, timedelta, timezone

from app.marketdata.health import evaluate_slo

UTC = timezone.utc


def provider(updated_at: str, *, age=0.2, state="connected"):
    return {
        "provider": "futu",
        "enabled": True,
        "authorized": True,
        "single_tenant": True,
        "state": state,
        "message_age_seconds": age,
        "heartbeat_timeout_seconds": 15,
        "updated_at": updated_at,
    }


def base(now):
    stamp = now.isoformat()
    return {
        "status": "ok",
        "shared_storage": {
            "connected": True,
            "provider_health": {"futu": provider(stamp)},
        },
    }, {
        "items": {
            "600000": {
                "quality": "realtime",
                "age_seconds": 0.4,
                "sequence_gap": False,
            }
        }
    }


def test_application_slo_requires_every_production_guard():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub, latest = base(now)
    report = evaluate_slo(
        hub, latest, provider(now.isoformat()), stream_enabled=True,
        storage_mode="redis", now=now,
    )
    assert report["ready"] is True
    assert report["provider_source"] == "redis"
    assert report["checks"]["provider_health_update"] is True


def test_dead_worker_heartbeat_cannot_remain_ready():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub, latest = base(now)
    old = (now - timedelta(seconds=16)).isoformat()
    hub["shared_storage"]["provider_health"]["futu"] = provider(old)
    report = evaluate_slo(
        hub, latest, provider(now.isoformat()), stream_enabled=True,
        storage_mode="redis", now=now,
    )
    assert report["ready"] is False
    assert "provider_health_update_stale" in report["reasons"]


def test_gap_or_degraded_latest_is_not_slo_ready():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub, latest = base(now)
    latest["items"]["600000"]["sequence_gap"] = True
    report = evaluate_slo(
        hub, latest, provider(now.isoformat()), stream_enabled=True,
        storage_mode="redis", now=now,
    )
    assert report["ready"] is False
    assert "sequence_gap_pending" in report["reasons"]


def test_disabled_memory_mode_is_explicitly_not_ready():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    report = evaluate_slo({}, {}, {}, stream_enabled=False,
                         storage_mode="memory", now=now)
    assert report["ready"] is False
    assert "stream_disabled" in report["reasons"]
    assert "shared_storage_unavailable" in report["reasons"]


def test_redis_mode_without_worker_heartbeat_never_falls_back_to_local_provider():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    _hub, latest = base(now)
    hub = {"shared_storage": {"connected": True, "provider_health": {}}}
    report = evaluate_slo(
        hub, latest, provider(now.isoformat()), stream_enabled=True,
        storage_mode="redis", now=now,
    )
    assert report["provider_source"] == "redis"
    assert report["ready"] is False
    assert "provider_disabled" in report["reasons"]


def test_expected_symbols_and_future_age_fail_closed():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub, latest = base(now)
    latest["items"]["600000"]["age_seconds"] = -1
    report = evaluate_slo(
        hub, latest, provider(now.isoformat()), stream_enabled=True,
        storage_mode="redis", now=now, expected_symbols=["600000", "SZ.000001"],
    )
    assert report["ready"] is False
    assert "latest_symbols_missing" in report["reasons"]
    assert "latest_stale" in report["reasons"]
