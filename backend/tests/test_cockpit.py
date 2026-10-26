"""Cockpit tests use only mocks and the isolated pytest database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
import os
import threading
import time as clock

from cryptography.fernet import Fernet
import pandas as pd
import pytest

# conftest configures database/JWT isolation before application imports.
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

from app.screener import cockpit as c


def wait_idle(feed):
    deadline = clock.monotonic() + 3
    while feed.inflight and clock.monotonic() < deadline:
        threading.Event().wait(0.005)
    assert not feed.inflight


def seed(feed, value):
    feed.value = value
    feed.attempt = clock.monotonic()
    return feed


def test_environment_is_isolated():
    from app.config import settings
    assert "smtest_" in settings.database_url
    assert settings.jwt_secret.startswith("test-")
    Fernet(settings.fernet_key.encode())


def test_singleflight_quick_return_and_stuck_source():
    feed = c.Feed("mock")
    release, entered = threading.Event(), threading.Event()
    calls = []

    def load():
        calls.append(1)
        entered.set()
        release.wait(3)
        return {"n": 1}

    try:
        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(lambda _: feed.get(load), range(80)))
        assert entered.wait(1)
        assert all(r["status"] == "loading" for r in results)
        assert len(calls) == 1
        feed.started -= 20
        feed.attempt -= 100
        assert feed.get(load)["status"] == "unavailable"
        assert feed.inflight
        assert len(calls) == 1
    finally:
        release.set()
        wait_idle(feed)
    assert feed.get(load)["value"] == {"n": 1}
    assert len(calls) == 1


def test_last_good_survives_failure_and_copies():
    feed = seed(c.Feed("mock"), {"points": [1]})
    feed.attempt -= 31

    def fail():
        raise RuntimeError("secret should not leak")

    feed.get(fail)
    wait_idle(feed)
    result = feed.get(fail)
    assert result["status"] == "stale"
    assert result["value"] == {"points": [1]}
    assert "secret" not in result["error"]
    result["value"]["points"].append(2)
    assert feed.value == {"points": [1]}


def test_offhours_reduce_fetches_but_initial_fetch_allowed():
    feed = seed(c.Feed("mock"), [1])
    feed.attempt -= 60
    assert feed.get(lambda: pytest.fail("must not refresh"), active=False)["value"] == [1]
    fresh = c.Feed("mock")
    fresh.get(lambda: [2], active=False)
    wait_idle(fresh)
    assert fresh.value == [2]


def test_minutes_sort_dedupe_latest_day_and_no_daily_fill():
    df = pd.DataFrame({"day": ["2026-10-23 15:00:00", "2026-10-26 09:32:00",
                               "2026-10-26 09:31:00", "2026-10-26 09:32:00",
                               "2026-10-26 12:00:00"], "close": [1, 2, 3, 4, 5]})
    assert c._points(df) == [{"time": "2026-10-26 09:31:00", "value": 3.0},
                             {"time": "2026-10-26 09:32:00", "value": 4.0}]
    with pytest.raises((ValueError, KeyError)):
        c._points(pd.DataFrame({"日期": ["2026-10-26"], "收盘": [10]}))


def test_news_combines_dates_sorts_and_deduplicates():
    df = pd.DataFrame([{"发布日期": date(2026, 10, 25), "发布时间": time(23, 59), "标题": "旧", "内容": "a"},
                       {"发布日期": date(2026, 10, 26), "发布时间": time(0, 1), "标题": "新", "内容": "b"},
                       {"发布日期": date(2026, 10, 26), "发布时间": time(0, 1), "标题": "新", "内容": "b"}])
    data = c._parse_news(df, True)
    assert len(data["items"]) == 2
    assert data["data_time"] == "2026-10-26 00:01:00"
    assert data["items"][0]["title"] == "新"
    assert all(i["important"] for i in data["items"])
    fallback = c._parse_news(pd.DataFrame([{"发布时间": "2026-10-26 10:00:00", "标题": "普通", "摘要": "消息", "链接": "https://example.com"}]), False)
    assert not fallback["items"][0]["important"]
    assert fallback["items"][0]["source"] == "东方财富"


@pytest.mark.parametrize("kind,code", [("industry", "m:90+s:4"), ("concept", "m:90+t:3")])
def test_real_flow_endpoint_units_and_scope(monkeypatch, kind, code):
    import requests
    from app.shared import data_gateway as dg
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"rc": 0, "data": {"total": 3, "diff": [
                {"f12": "BK1", "f14": "甲", "f62": -340000000},
                {"f12": "BK2", "f14": "乙", "f62": 120000000},
                {"f12": "BK3", "f14": "丙", "f62": 0}]}}

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(requests, "get", get)
    loader = dg.get_industry_fund_flow if kind == "industry" else dg.get_concept_fund_flow
    frame = loader.__wrapped__()
    assert frame["fund_flow"].tolist() == [1.2, 0.0, -3.4]
    assert frame["board_code"].tolist() == ["BK2", "BK3", "BK1"]
    assert calls[0][0] == "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
    assert calls[0][1]["params"] == {"key": "f62", "code": code}
    assert calls[0][1]["timeout"] == (3, 8)


@pytest.mark.parametrize("payload", [
    {"rc": 102, "data": None},
    {"rc": 0, "data": {"total": 2, "diff": [{"f12": "BK1", "f14": "甲", "f62": 1}]}},
    {"rc": 0, "data": {"total": 1, "diff": [{"f12": "BK1", "f14": "甲", "f62": "-"}]}},
    {"rc": 0, "data": {"total": 1, "diff": [{"f12": "BK1", "f14": "甲", "f62": float("inf")}]}},
    {"rc": 0, "data": {"total": 1, "diff": [{"f12": "BK1", "f14": "甲"}]}}
])
def test_flow_rejects_empty_partial_and_invalid_payload(monkeypatch, payload):
    import requests
    from app.shared import data_gateway as dg

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr(requests, "get", lambda *a, **kw: Response())
    with pytest.raises(ValueError):
        dg._board_fund_flow("industry")


def test_flow_unwraps_retry_and_keeps_yi_units(monkeypatch):
    from app.shared import data_gateway as dg

    def wrapped():
        pytest.fail("retry wrapper must not be used")

    wrapped.__wrapped__ = lambda: pd.DataFrame({"board_name": ["甲", "乙"], "fund_flow": [1.2, -3.4]})
    monkeypatch.setattr(dg, "get_industry_fund_flow", wrapped)
    assert c._load_flow("industry") == {"boards": [{"name": "甲", "net_inflow": 1.2}, {"name": "乙", "net_inflow": -3.4}], "data_time": None}


@pytest.mark.parametrize("hour,minute,state", [(9, 0, "preopen"), (10, 0, "trading"), (12, 0, "lunch"), (15, 0, "closed")])
def test_session_states(monkeypatch, hour, minute, state):
    monkeypatch.setattr(c, "_calendar", seed(c.Feed("calendar", 86400), {date(2026, 10, 26), date(2026, 12, 31)}))
    result = c._session(datetime(2026, 10, 26, hour, minute, tzinfo=c.TZ))
    assert result["state"] == state
    assert result["calendar_verified"]


def test_unverified_calendar(monkeypatch):
    calendar = c.Feed("calendar")
    calendar.error = "failed"
    calendar.attempt = clock.monotonic()
    monkeypatch.setattr(c, "_calendar", calendar)
    assert not c._session(datetime(2026, 10, 26, 10, tzinfo=c.TZ))["calendar_verified"]


def test_market_four_index_contract_and_independent_failure(monkeypatch):
    monkeypatch.setattr(c, "_now", lambda: datetime(2026, 10, 26, 10, tzinfo=c.TZ))
    monkeypatch.setattr(c, "_calendar", seed(c.Feed("calendar", 86400), {date(2026, 10, 26)}))
    monkeypatch.setattr(c, "_snapshot", seed(c.Feed("snapshot"), {code: {"last": 101, "pct_chg": 1.0, "prev_close": 100} for code, _ in c.INDEXES}))
    minutes = {code: (seed(c.Feed("em"), [{"time": "2026-10-26 09:59:00", "value": 101.0}]), c.Feed("sina")) for code, _ in c.INDEXES}
    monkeypatch.setattr(c, "_minutes", minutes)
    bad = c.Feed("flow")
    bad.error, bad.attempt = "failed", clock.monotonic()
    monkeypatch.setattr(c, "_flows", {"industry": bad, "concept": seed(c.Feed("flow"), {"boards": [{"name": "测试", "net_inflow": 2.0}], "data_time": None})})
    result = c.market()
    assert [r["code"] for r in result["indexes"]] == [r[0] for r in c.INDEXES]
    fields = {"code", "name", "last", "pct_chg", "prev_close", "points", "data_time", "source", "status", "error"}
    assert all(set(row) == fields and row["status"] == "ok" for row in result["indexes"])
    assert result["fund_flow"]["industry"]["status"] == "unavailable"
    assert result["fund_flow"]["concept"]["status"] == "ok"


def test_minute_provider_arguments(monkeypatch):
    import akshare as ak
    calls = []
    frame = pd.DataFrame({"时间": ["2026-10-26 09:31:00"], "收盘": [123]})
    monkeypatch.setattr(ak, "index_zh_a_hist_min_em", lambda **kw: (calls.append(kw), frame)[1])
    monkeypatch.setattr(ak, "stock_zh_a_minute", lambda **kw: (calls.append(kw), frame)[1])
    assert c._load_minutes("sh000688", "em")[0]["value"] == 123
    assert c._load_minutes("sh000688", "sina")[0]["value"] == 123
    assert calls == [{"symbol": "000688", "period": "1"},
                     {"symbol": "sh000688", "period": "1", "adjust": ""}]


def test_hanging_primary_can_use_independent_fallback():
    primary = c.Feed("em")
    primary.inflight = True
    primary.attempt = primary.started = clock.monotonic() - 20
    secondary = seed(c.Feed("sina"), [{"time": "2026-10-26 10:00:00", "value": 1}])
    result = c._choose(primary, secondary, lambda: pytest.fail("no new primary thread"),
                       lambda: pytest.fail("fallback already cached"))
    assert result["source"] == "sina"
    assert result["status"] == "ok"
    assert primary.inflight


def test_news_ttl_and_fallback(monkeypatch):
    primary = c.Feed("em", 60)
    primary.error, primary.attempt = "failed", clock.monotonic()
    fallback = seed(c.Feed("cls", 60), {"items": [], "data_time": None})
    fallback.attempt -= 31
    monkeypatch.setattr(c, "_news", (primary, fallback))
    monkeypatch.setattr(c, "_load_news", lambda _: pytest.fail("60s cache should be reused"))
    assert c.news()["source"] == "cls"


@pytest.mark.parametrize("color,expected", [(0, False), (1, False), (2, True), (3, True),
                                            ("3", True), (None, False), (9, False)])
def test_eastmoney_official_red_flags(color, expected):
    data = c._parse_news(pd.DataFrame([{"发布时间": "2026-10-26 10:00:00",
                                        "标题": "测试", "titleColor": color}]), False)
    assert data["items"][0]["important"] is expected


def test_eastmoney_request_preserves_importance(monkeypatch):
    import requests
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": "1", "data": {"fastNewsList": [
                {"code": "20261026123", "title": "重点", "summary": "内容",
                 "showTime": "2026-10-26 10:00:00", "titleColor": 3},
                {"code": "20261026124", "title": "普通", "summary": "内容",
                 "showTime": "2026-10-26 10:01:00", "titleColor": 0}]}}

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(requests, "get", get)
    result = c._load_news(False)
    assert [i["important"] for i in result["items"]] == [False, True]
    assert result["items"][1]["url"] == "https://finance.eastmoney.com/a/20261026123.html"
    assert calls[0][0] == "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    assert calls[0][1]["timeout"] == (3, 8)
    assert calls[0][1]["params"]["fastColumn"] == "102"
    assert calls[0][1]["params"]["pageSize"] == "200"
    assert calls[0][1]["params"]["req_trace"].isdigit()


@pytest.mark.parametrize("payload", [{"code": 0, "data": None},
    {"code": "1", "data": {}}, {"code": "1", "data": {"fastNewsList": []}}])
def test_eastmoney_invalid_response_does_not_become_success(monkeypatch, payload):
    import requests

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr(requests, "get", lambda *a, **kw: Response())
    with pytest.raises(ValueError):
        c._load_news(False)


def test_news_no_red_items_is_valid_not_fallback(monkeypatch):
    value = c._parse_news(pd.DataFrame([{"发布时间": "2026-10-26 10:00:00", "标题": "普通", "titleColor": 0}]), False)
    monkeypatch.setattr(c, "_news", (seed(c.Feed("em", 60), value), c.Feed("cls", 60)))
    monkeypatch.setattr(c, "_load_news", lambda _: pytest.fail("valid primary should not fall back"))
    result = c.news()
    assert result["status"] == "ok"
    assert not any(i["important"] for i in result["items"])


def test_routes_require_auth_without_starting_app(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.screener.api import router
    from app.shared.security import current_user_id
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    for path in ("market", "news"):
        assert client.get(f"/api/screener/cockpit/{path}").status_code in (401, 403)
    app.dependency_overrides[current_user_id] = lambda: 1
    monkeypatch.setattr(c, "market", lambda: {"market": "mock"})
    monkeypatch.setattr(c, "news", lambda: {"news": "mock"})
    assert client.get("/api/screener/cockpit/market").json() == {"market": "mock"}
    assert client.get("/api/screener/cockpit/news").json() == {"news": "mock"}
