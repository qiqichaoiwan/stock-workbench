"""Focused regression checks for audit fixes that protect API/data quality."""
import math

import pytest
from pydantic import ValidationError

from app.portfolio.api import HoldingIn, WatchIn
from app.scheduler import build_scheduler
from app.screener.api import _json_or_default


def test_marketdata_storage_mode_rejects_unknown_values():
    from app.config import Settings

    assert Settings(marketdata_storage_mode="REDIS").marketdata_storage_mode == "redis"
    for invalid in ("redsi", "", None):
        with pytest.raises(ValidationError):
            Settings(marketdata_storage_mode=invalid)


def test_marketdata_process_role_is_explicit():
    from app.config import Settings

    assert Settings(marketdata_process_role="WORKER").marketdata_process_role == "worker"
    with pytest.raises(ValidationError):
        Settings(marketdata_process_role="api-worker")


def test_redis_store_construction_never_falls_back_to_memory(monkeypatch):
    from app.config import settings
    from app.marketdata import hub, redis_store

    monkeypatch.setattr(settings, "marketdata_storage_mode", "redis")

    def fail(*args, **kwargs):
        raise RuntimeError("invalid redis configuration")

    monkeypatch.setattr(redis_store, "RedisQuoteStore", fail)
    with pytest.raises(RuntimeError, match="invalid redis configuration"):
        hub._configured_store()


def test_production_cors_rejects_wildcard_with_credentials():
    from app.main import _validate_cors_origins

    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        _validate_cors_origins(["https://app.example.com", "*"], production=True)
    _validate_cors_origins(["https://app.example.com"], production=True)


def test_public_readiness_distinguishes_database_failure(monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main

    client = TestClient(main.app)
    assert client.get('/api/health').json()['status'] == 'ok'
    response = client.get('/api/ready')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'app': main.settings.app_name, 'database': 'ok'}

    class BrokenEngine:
        def connect(self):
            raise RuntimeError('database password must not leak')

    monkeypatch.setattr(db, 'engine', BrokenEngine())
    response = client.get('/api/ready')
    assert response.status_code == 503
    assert 'password' not in response.text
    assert client.get('/api/health').status_code == 200


@pytest.mark.parametrize("field", ["cost_price", "take_profit", "stop_loss", "alert_pct"])
@pytest.mark.parametrize("value", [-1, math.nan, math.inf, -math.inf])
def test_watch_thresholds_reject_negative_and_nonfinite(field, value):
    with pytest.raises(ValidationError):
        WatchIn(code="600000", **{field: value})


def test_holding_take_profit_rejects_negative_and_nonfinite():
    for value in (-1, math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            HoldingIn(code="600000", buy_price=10, quantity=100,
                      buy_date="2026-11-06", take_profit_price=value)


def test_dirty_json_fields_fail_soft_per_row():
    assert _json_or_default("{broken", {}) == {}
    assert _json_or_default("[]", {}) == {}
    assert _json_or_default("[1]", []) == [1]


def test_intraday_watch_jobs_cover_final_half_hour():
    scheduler = build_scheduler()
    ids = {job.id for job in scheduler.get_jobs()}
    assert {"intraday_fund_flow_1435", "intraday_market_stat_1435",
            "intraday_watch_alert_1435"} <= ids


def test_ai_fallback_survives_calendar_and_malformed_llm_response(monkeypatch):
    from app.portfolio import ai_advisor

    class DB:
        def add(self, value): self.value = value
        def commit(self): pass
        def refresh(self, value): value.id = 1

    monkeypatch.setattr(ai_advisor.stock_ref, "quote_history",
                        lambda *args, **kwargs: [])
    monkeypatch.setattr(ai_advisor.stock_ref, "pool_rating", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai_advisor.stock_ref, "latest_close",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("calendar unavailable")))
    monkeypatch.setattr(ai_advisor.llm_gateway, "chat",
                        lambda *args, **kwargs: ("[]", "test-model"))
    monkeypatch.setattr(ai_advisor, "holdings_view", lambda *args, **kwargs: [])

    result = ai_advisor.analyze_stock(DB(), 1, "600000")
    assert result["model"] == "rule-fallback"
    assert result["sell_points"] == []
