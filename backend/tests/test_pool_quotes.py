"""选股池行情接口:实时快照优先、源失败/超时降级已落库日线、缺失明确标记。"""
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import ScreenerBase, get_db
from app.screener import api
from app.screener.models import DailyQuote, StockPool
from app.shared import data_gateway as dg
from app.shared.security import current_user_id

CN = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def reset_quote_cache():
    dg._SPOT_QUOTE_CACHE = None
    dg._SPOT_QUOTE_REFRESHING = False
    yield
    dg._SPOT_QUOTE_CACHE = None
    dg._SPOT_QUOTE_REFRESHING = False


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    ScreenerBase.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_id] = lambda: 1
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def pool(db):
    db.add_all([
        StockPool(trade_date=date(2026, 10, 28), pool_type="main",
                  code="600000", name="浦发银行", score=80, in_price=10.0),
        StockPool(trade_date=date(2026, 10, 28), pool_type="main",
                  code="000001", name="平安银行", score=70, in_price=9.8),
    ])
    db.add(DailyQuote(code="000001", trade_date=date(2026, 10, 28),
                      close=9.5, pct_chg=-1.2))
    db.commit()


SNAPSHOT_AT = "2026-10-29T10:21:30+08:00"


def _quotes(*, source="akshare", age=2.0):
    return ({"600000": {"price": 12.345, "pct_chg": 1.23, "high": 12.5,
                        "low": 11.9, "amount": 123456.0, "turnover": 2.1}},
            source, SNAPSHOT_AT, age)


def test_spot_quotes_preferred_and_missing_code_falls_back_to_stored(client, db, pool, monkeypatch):
    monkeypatch.setattr(api, "_quote_session", lambda now: "trading")
    monkeypatch.setattr(api.dg, "get_spot_quotes", lambda codes: _quotes(source="akshare"))

    data = client.get("/api/screener/pool/quotes").json()

    assert data["source"] == "akshare"
    assert data["session"] == "trading"
    assert data["realtime"] is True
    assert data["snapshot_at"] == SNAPSHOT_AT
    assert data["snapshot_age_seconds"] == 2.0
    assert data["counts"] == {"total": 2, "spot": 1, "stored": 1, "missing": 0}
    assert data["errors"] == []
    assert data["items"]["600000"]["quote_kind"] == "spot"
    assert data["items"]["600000"]["price"] == 12.345
    assert data["items"]["600000"]["pct_chg"] == 1.23
    # 快照里没有的代码降级为最近已落库日线,不伪装成实时
    assert data["items"]["000001"] == {
        "price": 9.5, "pct_chg": -1.2, "trade_date": "2026-10-28",
        "quote_source": "stored_daily", "quote_kind": "stored",
    }


def test_delayed_snapshot_is_not_labelled_realtime(client, pool, monkeypatch):
    monkeypatch.setattr(api, "_quote_session", lambda now: "trading")
    monkeypatch.setattr(api.dg, "get_spot_quotes",
                        lambda codes: _quotes(source="sina", age=420.0))

    data = client.get("/api/screener/pool/quotes").json()

    # 行情仍在(过期快照比昨日收盘有用),但不得标成实时,由前端提示延迟
    assert data["counts"]["spot"] == 1
    assert data["realtime"] is False
    assert data["snapshot_age_seconds"] == 420.0
    assert data["items"]["600000"]["quote_kind"] == "spot"


def test_spot_failure_degrades_to_stored_and_reports_error(client, pool, monkeypatch):
    def fail(codes):
        raise RuntimeError("all spot sources failed")

    monkeypatch.setattr(api.dg, "get_spot_quotes", fail)

    data = client.get("/api/screener/pool/quotes").json()

    assert data["errors"] == ["quote_failed"]
    assert data["source"] == "unavailable"
    assert data["realtime"] is False
    assert data["counts"] == {"total": 2, "spot": 0, "stored": 1, "missing": 1}
    assert "600000" not in data["items"]
    assert data["missing"] == ["600000"]
    assert data["items"]["000001"]["quote_kind"] == "stored"


def test_slow_spot_source_times_out_instead_of_blocking(client, pool, monkeypatch):
    def slow(codes):
        time.sleep(0.3)
        return _quotes()

    monkeypatch.setattr(api.dg, "get_spot_quotes", slow)
    monkeypatch.setattr(api, "POOL_QUOTE_WAIT_SECONDS", 0.02)

    started = time.monotonic()
    data = client.get("/api/screener/pool/quotes").json()
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert data["errors"] == ["quote_timeout"]
    assert data["counts"]["spot"] == 0
    assert data["items"]["000001"]["quote_kind"] == "stored"


def test_empty_pool_skips_source(client, monkeypatch):
    def unexpected(codes):
        raise AssertionError("empty pool must not call spot source")

    monkeypatch.setattr(api.dg, "get_spot_quotes", unexpected)

    data = client.get("/api/screener/pool/quotes").json()

    assert data["counts"] == {"total": 0, "spot": 0, "stored": 0, "missing": 0}
    assert data["items"] == {}
    assert data["errors"] == []


@pytest.mark.parametrize("at,expected", [
    (datetime(2026, 10, 29, 9, 29, tzinfo=CN), "closed"),
    (datetime(2026, 10, 29, 9, 30, tzinfo=CN), "trading"),
    (datetime(2026, 10, 29, 11, 30, tzinfo=CN), "trading"),
    (datetime(2026, 10, 29, 12, 0, tzinfo=CN), "closed"),
    (datetime(2026, 10, 29, 13, 0, tzinfo=CN), "trading"),
    (datetime(2026, 10, 29, 15, 0, tzinfo=CN), "trading"),
    (datetime(2026, 10, 29, 15, 1, tzinfo=CN), "closed"),
    (datetime(2026, 10, 31, 10, 0, tzinfo=CN), "closed"),  # 周六
])
def test_quote_session_boundaries(at, expected):
    assert api._quote_session(at) == expected


# ---------- 网关快照缓存 ----------

def _snapshot():
    return pd.DataFrame([{"code": "600000", "price": 12.345, "pct_chg": 1.234,
                          "high": 12.5, "low": 11.9, "amount": 123456.0,
                          "turnover": 2.1}])


def _batch_down(codes):
    raise RuntimeError("sina batch unavailable")


def test_gateway_prefers_lightweight_batch_quotes(monkeypatch):
    at = (datetime.now(CN) - timedelta(seconds=3)).replace(microsecond=0).isoformat()
    monkeypatch.setattr(dg, "_fetch_sina_batch_quotes",
                        lambda codes: ({"600000": {"price": 12.345}}, at))

    def unexpected():
        raise AssertionError("batch success must not hit full-market snapshot")

    monkeypatch.setattr(dg, "get_spot_snapshot_with_source", unexpected)

    quotes, source, snapshot_at, age = dg.get_spot_quotes(["600000"])

    assert quotes == {"600000": {"price": 12.345}}
    assert source == "sina"
    assert snapshot_at == at
    assert age is not None and 2 <= age <= 5


def test_sina_batch_parses_price_pct_and_market_timestamp(monkeypatch):
    fields = ["浦发银行", "10.000", "9.990", "10.200", "10.300", "9.800",
              "10.190", "10.200", "1000", "1020000.000"] + ["0"] * 20 + \
             ["2026-10-29", "14:30:00", "00"]
    text = (f'var hq_str_sh600000="{",".join(fields)}";\n'
            'var hq_str_sz000001="";')

    class Resp:
        encoding = None
        text = ""

        def raise_for_status(self):
            pass

    resp = Resp()
    resp.text = text
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: resp)

    quotes, snapshot_at = dg._fetch_sina_batch_quotes({"600000", "000001"})

    assert snapshot_at == "2026-10-29T14:30:00+08:00"
    assert set(quotes) == {"600000"}
    assert quotes["600000"]["price"] == 10.2
    assert quotes["600000"]["pct_chg"] == 2.1
    assert quotes["600000"]["high"] == 10.3
    assert quotes["600000"]["amount"] == 1020000.0
    assert quotes["600000"]["turnover"] is None


def test_sina_symbol_maps_a_share_boards():
    assert dg._sina_symbol("600000") == "sh600000"
    assert dg._sina_symbol("688981") == "sh688981"
    assert dg._sina_symbol("000001") == "sz000001"
    assert dg._sina_symbol("300033") == "sz300033"
    assert dg._sina_symbol("830799") == "bj830799"
    assert dg._sina_symbol("920001") == "bj920001"


def test_sina_batch_rejects_empty_payload(monkeypatch):
    class Resp:
        encoding = None
        text = 'var hq_str_sh600000="";'

        def raise_for_status(self):
            pass

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Resp())

    with pytest.raises(RuntimeError, match="no valid rows"):
        dg._fetch_sina_batch_quotes({"600000"})


def test_gateway_quotes_returns_subset_with_snapshot_metadata(monkeypatch):
    calls = []

    def fake():
        calls.append(1)
        return _snapshot(), "sina"

    monkeypatch.setattr(dg, "_fetch_sina_batch_quotes", _batch_down)
    monkeypatch.setattr(dg, "get_spot_snapshot_with_source", fake)

    quotes, source, snapshot_at, age = dg.get_spot_quotes(["600000", "000001"])

    assert source == "sina"
    assert snapshot_at and "T" in snapshot_at
    assert age is not None and age < 5
    assert quotes["600000"]["price"] == 12.345
    assert quotes["600000"]["pct_chg"] == 1.23
    assert "000001" not in quotes
    assert calls == [1]

    # TTL 内复用缓存,不再打外部源
    dg.get_spot_quotes(["600000"])
    assert calls == [1]


def test_gateway_serves_expired_snapshot_and_refreshes_in_background(monkeypatch):
    old_snapshot_at = "2026-10-29T10:00:00+08:00"
    dg._SPOT_QUOTE_CACHE = (time.monotonic() - 30, old_snapshot_at, _snapshot(), "sina")
    calls = []

    def fake():
        calls.append(1)
        return _snapshot(), "sina"

    monkeypatch.setattr(dg, "_fetch_sina_batch_quotes", _batch_down)
    monkeypatch.setattr(dg, "get_spot_snapshot_with_source", fake)

    quotes, source, snapshot_at, age = dg.get_spot_quotes(["600000"])

    # 过期缓存立即返回,不把请求卡在外部拉取上;刷新交给后台线程
    assert quotes["600000"]["price"] == 12.345
    assert source == "sina"
    assert snapshot_at == old_snapshot_at
    assert age >= 29
    deadline = time.monotonic() + 2
    while (not calls or dg._SPOT_QUOTE_REFRESHING) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == [1]
    assert dg._SPOT_QUOTE_REFRESHING is False


def test_gateway_without_cache_fails_when_source_fails(monkeypatch):
    def fail():
        raise RuntimeError("all sources down")

    monkeypatch.setattr(dg, "_fetch_sina_batch_quotes", _batch_down)
    monkeypatch.setattr(dg, "get_spot_snapshot_with_source", fail)

    with pytest.raises(RuntimeError, match="all sources down"):
        dg.get_spot_quotes(["600000"])
    assert dg._SPOT_QUOTE_REFRESHING is False


def test_gateway_rejects_stale_snapshot_and_concurrent_first_fetch(monkeypatch):
    dg._SPOT_QUOTE_CACHE = (time.monotonic() - dg._SPOT_QUOTE_STALE_MAX - 10,
                            "2026-10-28T15:00:00+08:00", _snapshot(), "sina")
    monkeypatch.setattr(dg, "_fetch_sina_batch_quotes", _batch_down)
    monkeypatch.setattr(dg, "get_spot_snapshot_with_source",
                        lambda: (_snapshot(), "sina"))

    with pytest.raises(RuntimeError, match="stale"):
        dg.get_spot_quotes(["600000"])

    deadline = time.monotonic() + 2
    while dg._SPOT_QUOTE_REFRESHING and time.monotonic() < deadline:
        time.sleep(0.01)


def test_gateway_fails_fast_when_another_refresh_is_running(monkeypatch):
    monkeypatch.setattr(dg, "_fetch_sina_batch_quotes", _batch_down)
    dg._SPOT_QUOTE_CACHE = None
    dg._SPOT_QUOTE_REFRESHING = True
    with pytest.raises(RuntimeError, match="refresh in progress"):
        dg.get_spot_quotes(["600000"])
