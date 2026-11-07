from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.marketdata import hub as hub_module
from app.marketdata import api as api_module
from app.marketdata.api import router
from app.shared.security import current_user_id

UTC = timezone.utc


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def event(at, *, code="600000", provider="public-snapshot", sequence=1, **extra):
    return {
        "code": code,
        "price": 10.5,
        "pct_chg": 1.2,
        "provider": provider,
        "provider_time": at.isoformat(),
        "received_at": at.isoformat(),
        "sequence": sequence,
        **extra,
    }


def test_authorized_event_is_realtime_and_snapshot_contract_is_explicit():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now), max_age_seconds=3)
    result = hub.ingest(event(now, provider="futu"), authorized=True)

    assert result["accepted"] is True
    item = hub.snapshot(["600000"])["items"]["600000"]
    assert item["quality"] == "realtime"
    assert item["authorization"] is True
    assert item["provider_time"] == now.isoformat()
    assert item["received_at"] == now.isoformat()
    assert item["age_seconds"] == 0
    health = hub.health()
    assert health["mode"] == "memory"
    assert health["single_process"] is True
    assert health["sla_ready"] is False


def test_unlicensed_event_is_best_effort_even_when_fresh():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    hub.ingest(event(now), authorized=False)
    item = hub.latest()["items"]["600000"]
    assert item["quality"] == "best_effort"
    assert item["authorization"] is False


def test_fresh_event_outside_trading_window_is_not_realtime():
    # The timestamp is 11:30 Asia/Shanghai, the half-open morning session end.
    now = datetime(2026, 11, 9, 3, 30, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    hub.ingest(event(now, provider="futu"), authorized=True)
    item = hub.latest()["items"]["600000"]
    assert item["session"] == "lunch"
    assert item["quality"] == "delayed"


def test_quote_downgrades_when_current_clock_leaves_trading_window():
    received_at = datetime(2026, 11, 9, 3, 29, 59, tzinfo=UTC)
    clock = Clock(received_at)
    hub = hub_module.QuoteHub(now_fn=clock)
    hub.ingest(event(received_at, provider="futu"), authorized=True)
    clock.value = datetime(2026, 11, 9, 3, 30, tzinfo=UTC)
    assert hub.latest()["items"]["600000"]["quality"] == "delayed"


def test_visible_snapshot_prefers_authorized_feed_over_fallback_provider():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    hub.ingest(event(now, provider="public-snapshot", sequence=1, price=9), authorized=False)
    hub.ingest(event(now, provider="licensed-feed", sequence=1, price=10), authorized=True)
    item = hub.latest(["600000"])["items"]["600000"]
    assert item["provider"] == "licensed-feed"
    assert item["quality"] == "realtime"
    assert item["price"] == 10


def test_delayed_and_stale_are_derived_from_provider_time():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    clock = Clock(now)
    hub = hub_module.QuoteHub(now_fn=clock, max_age_seconds=3, delayed_max_age_seconds=15)
    hub.ingest(event(now - timedelta(seconds=8)), authorized=True)
    assert hub.latest()["items"]["600000"]["quality"] == "delayed"
    clock.value = now + timedelta(seconds=30)
    assert hub.latest()["items"]["600000"]["quality"] == "stale"


def test_invalid_time_price_future_and_order_are_rejected():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    assert hub.ingest({**event(now), "provider_time": now.replace(tzinfo=None).isoformat()})["reason"] == "provider_time_timezone_required"
    assert hub.ingest({**event(now, code="600001"), "price": 0})["reason"] == "price_invalid"
    assert hub.ingest({**event(now, code="600002"), "provider_time": (now + timedelta(seconds=5)).isoformat()})["reason"] == "provider_time_future"
    assert hub.ingest(event(now, code="600003", sequence=5))["accepted"] is True
    duplicate = hub.ingest(event(now + timedelta(seconds=1), code="600003", sequence=5))
    assert duplicate["reason"] == "sequence_out_of_order"
    old = hub.ingest(event(now, code="600003", sequence=4))
    assert old["reason"] == "sequence_out_of_order"
    assert hub.health()["rejected"] == 5


def test_timestamp_order_is_used_when_provider_has_no_sequence():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    assert hub.ingest(event(now, code="600004", sequence=None))["accepted"] is True
    result = hub.ingest(event(now, code="600004", sequence=None))
    assert result["reason"] == "provider_time_out_of_order"


def test_sequenced_event_cannot_replace_a_newer_unsequenced_quote():
    now = datetime(2026, 11, 9, 2, 0, 2, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    assert hub.ingest(event(now, sequence=None, price=10), authorized=True)["accepted"] is True
    result = hub.ingest(event(now - timedelta(seconds=1), sequence=1, price=9), authorized=True)
    assert result["reason"] == "provider_time_out_of_order"
    assert hub.latest()["items"]["600000"]["price"] == 10
    # Coarse timestamp precision is allowed when the sequence advances.
    assert hub.ingest(event(now, sequence=1, price=11), authorized=True)["accepted"] is True


def test_sequence_gap_is_accepted_but_forced_to_delayed_quality():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    assert hub.ingest(event(now, code="600007", sequence=10), authorized=True)["accepted"] is True
    assert hub.ingest(event(now + timedelta(seconds=1), code="600007", sequence=12),
                      authorized=True)["accepted"] is True
    item = hub.latest(["600007"])["items"]["600007"]
    assert item["sequence_gap"] is True
    assert item["quality"] == "delayed"
    assert hub.health()["sequence_gaps"] == 1


def test_sequence_gap_event_becomes_stale_after_delayed_window():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    clock = Clock(now)
    hub = hub_module.QuoteHub(now_fn=clock, max_age_seconds=3, delayed_max_age_seconds=15)
    assert hub.ingest(event(now, sequence=10), authorized=True)["accepted"] is True
    assert hub.ingest(event(now, sequence=12), authorized=True)["accepted"] is True
    assert hub.latest()["items"]["600000"]["quality"] == "delayed"
    clock.value = now + timedelta(seconds=16)
    assert hub.latest()["items"]["600000"]["quality"] == "stale"


def test_sequence_gap_remains_delayed_until_explicit_resync_proof():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now), max_age_seconds=3, delayed_max_age_seconds=15)
    assert hub.ingest(event(now, sequence=10), authorized=True)["accepted"] is True
    assert hub.ingest(event(now, sequence=12), authorized=True)["accepted"] is True
    # A later contiguous sequence does not prove that sequence 11 was recovered.
    assert hub.ingest(event(now, sequence=13), authorized=True)["accepted"] is True
    item = hub.latest()["items"]["600000"]
    assert item["sequence_gap"] is True
    assert item["quality"] == "delayed"
    # The adapter may explicitly prove a resync/contiguous baseline.
    assert hub.ingest(event(now, sequence=14, sequence_contiguous=True), authorized=True)["accepted"] is True
    item = hub.latest()["items"]["600000"]
    assert item["sequence_gap"] is False
    assert item["quality"] == "realtime"


def test_sequence_gap_latch_clears_on_hub_reset():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    hub.ingest(event(now, sequence=1), authorized=True)
    hub.ingest(event(now, sequence=3), authorized=True)
    hub.reset()
    hub.ingest(event(now, sequence=1), authorized=True)
    assert hub.latest()["items"]["600000"]["sequence_gap"] is False


def test_session_window_boundaries_are_server_derived():
    # 11:30 belongs to lunch, 15:00 belongs to closed; provider labels cannot
    # override the server-derived gate.
    at_1130 = datetime(2026, 11, 9, 3, 30, tzinfo=UTC)
    at_1500 = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)
    assert hub_module._session("trading", at_1130) == "lunch"
    assert hub_module._session("trading", at_1500) == "closed"
    assert hub_module._session("lunch", datetime(2026, 11, 9, 3, 0, tzinfo=UTC)) == "trading"


def test_batch_ingest_supports_provider_adapter_defaults():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    result = hub.ingest([{"code": "600005", "price": 8,
                          "provider_time": now.isoformat(), "received_at": now.isoformat()}],
                        provider="futu", authorized=True)
    assert result["accepted"] == 1
    assert hub.snapshot()["items"]["600005"]["provider"] == "futu"


def test_missing_codes_are_unavailable_and_metrics_keep_rejection_reasons():
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    hub = hub_module.QuoteHub(now_fn=Clock(now))
    hub.ingest({**event(now), "code": "bad"})
    item = hub.latest(["600006"])["items"]["600006"]
    assert item["quality"] == "unavailable"
    assert hub.health()["reject_reasons"]["code_invalid"] == 1


def test_http_ingest_requires_token_and_server_allowlist(monkeypatch):
    # Keep the contract test independent of the host weekday/time.  The
    # event is inside the verified 09:30-11:30 session so an authorized feed
    # is expected to be realtime.
    now = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    test_hub = hub_module.QuoteHub(now_fn=Clock(now))
    monkeypatch.setattr(hub_module, "_HUB", test_hub)
    monkeypatch.setenv("MARKETDATA_INGEST_TOKEN", "test-token")
    monkeypatch.setenv("MARKETDATA_AUTHORIZED_PROVIDERS", "futu")
    monkeypatch.setattr(api_module.settings, "marketdata_single_tenant", True)
    monkeypatch.setattr(api_module.settings, "marketdata_stream_enabled", True)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user_id] = lambda: 1
    client = TestClient(app)
    body = {"events": [{**event(now, provider="futu"), "authorization": False}]}

    assert client.post("/api/marketdata/ingest", json=body,
                       headers={"X-Marketdata-Token": "wrong"}).status_code == 401
    response = client.post("/api/marketdata/ingest", json=body,
                           headers={"X-Marketdata-Token": "test-token"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] == 1
    assert payload["health"]["sla_ready"] is False
    latest = client.get("/api/marketdata/latest?codes=600000").json()
    assert latest["items"]["600000"]["quality"] == "realtime"

    gap = client.post(
        "/api/marketdata/ingest",
        json={"events": [{**event(now, provider="futu", sequence=3)}]},
        headers={"X-Marketdata-Token": "test-token"},
    )
    assert gap.status_code == 200
    assert client.get("/api/marketdata/latest?codes=600000").json()["items"]["600000"]["quality"] == "delayed"

    resync = client.post(
        "/api/marketdata/ingest",
        json={"events": [{**event(now, provider="futu", sequence=4, sequence_resync=True)}]},
        headers={"X-Marketdata-Token": "test-token"},
    )
    assert resync.status_code == 200
    assert client.get("/api/marketdata/latest?codes=600000").json()["items"]["600000"]["quality"] == "realtime"


def test_http_ingest_blocks_personal_futu_feed_without_single_tenant(monkeypatch):
    monkeypatch.setenv("MARKETDATA_INGEST_TOKEN", "test-token")
    monkeypatch.setattr(api_module.settings, "marketdata_single_tenant", False)
    monkeypatch.setattr(api_module.settings, "marketdata_stream_enabled", True)
    app = FastAPI()
    app.include_router(router)
    now = datetime.now(UTC).replace(microsecond=0)
    response = TestClient(app).post(
        "/api/marketdata/ingest", json={"events": [event(now, provider="futu")]},
        headers={"X-Marketdata-Token": "test-token"})
    assert response.status_code == 403


def test_http_ingest_is_closed_when_token_is_not_configured(monkeypatch):
    monkeypatch.delenv("MARKETDATA_INGEST_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    now = datetime.now(UTC).replace(microsecond=0)
    response = client.post("/api/marketdata/ingest",
                           json={"events": [event(now)]})
    assert response.status_code == 503


def test_http_ingest_is_closed_when_stream_is_disabled(monkeypatch):
    monkeypatch.setenv("MARKETDATA_INGEST_TOKEN", "test-token")
    monkeypatch.setattr(api_module.settings, "marketdata_stream_enabled", False)
    app = FastAPI()
    app.include_router(router)
    now = datetime.now(UTC).replace(microsecond=0)
    response = TestClient(app).post(
        "/api/marketdata/ingest", json={"events": [event(now)]},
        headers={"X-Marketdata-Token": "test-token"})
    assert response.status_code == 503
