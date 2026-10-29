"""服务端实时适配器:只验证字段契约、时间戳与降级,不代表真实行情回测。"""
from datetime import date, datetime

import pandas as pd
import pytest

from tests.test_dual_core_v2 import NOW, fixture
from app.screener import dual_core_feed as feed
from app.screener.dual_core_v2 import CN, evaluate


def frame_ending(last: str, periods: int = 320) -> pd.DataFrame:
    """构造稳定上升的合成日线;只用于验证计算/契约,不是行情数据。"""
    dates = pd.bdate_range(end=last, periods=periods)
    close = [100.0 + i * 0.2 for i in range(periods)]
    return pd.DataFrame({
        "trade_date": dates,
        "open": [c - 0.5 for c in close],
        "high": [c + 1 for c in close],
        "low": [c - 1 for c in close],
        "close": close,
        "amount": [1e11] * periods,
    })


@pytest.fixture(autouse=True)
def clear_feed_cache():
    feed.reset_cache()
    yield
    feed.reset_cache()


def test_core_evidence_has_v2_contract_and_fresh_stamp():
    evidence = feed._core_evidence(frame_ending("2026-10-28"), NOW)
    assert evidence is not None
    assert evidence["source"] == feed.SOURCE_CORE
    assert evidence["timestamp"] == NOW.isoformat()
    for key in feed._REQUIRED_CORE:
        assert evidence.get(key) is not None, key
    assert evidence["wma20_up4"] is True
    assert evidence["below_ma5_days"] == 0
    assert evidence["high60"] is not None
    assert isinstance(evidence["macd_hist"], list) and len(evidence["macd_hist"]) == 3
    assert isinstance(evidence["macd_dead_cross"], bool)
    assert evidence["price"] == round(frame_ending("2026-10-28")["close"].iloc[-1], 3)
    assert evidence["pct_chg_high"] is not None


def test_fetch_cores_rejects_bars_that_are_not_today(monkeypatch):
    stale = frame_ending("2026-10-27")
    fresh = frame_ending("2026-10-28")
    monkeypatch.setattr(feed.dg, "get_ths_index_daily",
                        lambda code, bars=1200, use_cache=True:
                        fresh.copy() if code == feed.CORE_A else stale.copy())
    monkeypatch.setattr(feed.dg, "get_ths_index_quote",
                        lambda code, use_cache=True: None)
    cores, frame_a = feed._fetch_cores(NOW)
    assert set(cores) == {feed.CORE_A}
    assert frame_a is not None

    monkeypatch.setattr(feed.dg, "get_ths_index_daily",
                        lambda code, bars=1200, use_cache=True: stale.copy())
    cores, frame_a = feed._fetch_cores(NOW)
    assert cores == {} and frame_a is None


def test_apply_quote_appends_today_only_with_matching_prev_close():
    frame = frame_ending("2026-10-27")
    prev = float(frame["close"].iloc[-1])
    quote = {"price": prev + 5, "prev_close": prev, "open": prev + 1,
             "high": prev + 6, "low": prev - 1, "amount": 1e11}
    merged = feed._apply_quote(frame, quote, NOW)
    assert merged is not None
    assert len(merged) == len(frame) + 1
    assert str(merged.iloc[-1]["trade_date"])[:10] == "2026-10-28"
    assert merged.iloc[-1]["close"] == quote["price"]

    mismatch = dict(quote, prev_close=prev + 10)
    assert feed._apply_quote(frame, mismatch, NOW) is None


def test_apply_quote_refreshes_existing_today_bar():
    frame = frame_ending("2026-10-28")
    prev = float(frame["close"].iloc[-2])
    price = float(frame["close"].iloc[-1]) + 2
    quote = {"price": price, "prev_close": prev, "open": prev + 1,
             "high": price + 3, "low": prev - 2, "amount": 1.5e11}
    merged = feed._apply_quote(frame, quote, NOW)
    assert merged is not None
    assert len(merged) == len(frame)
    assert merged.iloc[-1]["close"] == price
    assert merged.iloc[-1]["amount"] == 1.5e11
    assert merged.iloc[-1]["high"] == price + 3


def test_apply_quote_never_invents_today_bar_without_traded_amount():
    """集合竞价前 realhead 只有昨收价、没有成交额,不能拼成今日 bar。"""
    frame = frame_ending("2026-10-27")
    prev = float(frame["close"].iloc[-1])
    quote = {"price": prev, "prev_close": prev, "open": prev,
             "high": prev, "low": prev, "amount": None}
    assert feed._apply_quote(frame, quote, NOW) is None


def test_apply_quote_keeps_existing_today_bar_when_amount_missing():
    frame = frame_ending("2026-10-28")
    prev = float(frame["close"].iloc[-2])
    close = float(frame["close"].iloc[-1])
    quote = {"price": close + 2, "prev_close": prev, "open": prev,
             "high": close + 3, "low": prev - 2, "amount": None}
    merged = feed._apply_quote(frame, quote, NOW)
    assert merged is not None
    assert len(merged) == len(frame)
    assert merged.iloc[-1]["close"] == close


def test_market_precheck_matches_v2_market_rules():
    payload = fixture()
    snapshot = {"cores": payload["cores"], "emotion": payload["emotion"]}
    precheck = feed.market_precheck(snapshot)
    assert precheck is not None
    result = evaluate(payload, now=NOW)
    expected = {check["key"]: check["pass"] for check in result["checks"]}
    for check in precheck["checks"]:
        assert check["key"] in expected, check["key"]
        assert check["pass"] is expected[check["key"]], check["key"]
    assert precheck["cap"] == result["position"]["tier_cap"]
    assert precheck["divergence"] is False
    assert precheck["daily_ok"] is True


def test_market_precheck_flags_divergence_identically():
    payload = fixture()
    payload["cores"]["883994"]["macd_hist"] = [5, 4, 3]
    snapshot = {"cores": payload["cores"], "emotion": payload["emotion"]}
    precheck = feed.market_precheck(snapshot)
    result = evaluate(payload, now=NOW)
    assert precheck["divergence"] is True
    assert result["divergence"] is True


def _patch_success(monkeypatch, *, pools=None):
    frame = frame_ending("2026-10-28")
    cores = {code: feed._core_evidence(frame, NOW) for code in (feed.CORE_A, feed.CORE_B)}
    monkeypatch.setattr(feed, "_fetch_calendar", lambda scan_at: {
        "verified": True, "source": feed.SOURCE_CALENDAR,
        "trade_date": scan_at.date().isoformat(), "is_trade_day": True})
    monkeypatch.setattr(feed, "_fetch_cores",
                        lambda scan_at, **kwargs: (cores, frame))
    monkeypatch.setattr(feed, "_fetch_pools", lambda scan_at: dict(
        pools if pools is not None else
        {"limit_up": 60, "broken": 10, "limit_down": 5, "height": 5}))
    return cores


def test_collect_returns_verified_snapshot_and_market_precheck(monkeypatch):
    _patch_success(monkeypatch)
    payload = feed.collect(NOW)
    assert payload["source_verified"] is True
    assert payload["now"] >= NOW
    assert payload["reference"].startswith("dual_core_feed:")
    assert payload["market_precheck"] is not None
    snapshot = payload["snapshot"]
    assert set(snapshot["cores"]) == {feed.CORE_A, feed.CORE_B}
    assert snapshot["emotion"]["amount"] == 1e11
    assert snapshot["emotion"]["amount_ma5"] == 1e11

    result = evaluate(snapshot, now=payload["now"], calendar=payload["calendar"])
    server_missing = [key for key in result["source_missing"]
                      if key.startswith("cores.") or key.startswith("emotion")]
    assert server_missing == []
    assert payload["server_missing"] == []
    assert result["calendar_status"] == "open"


def test_collect_keeps_emotion_missing_when_pools_are_not_published(monkeypatch):
    _patch_success(monkeypatch, pools={"limit_up": None, "broken": None,
                                       "limit_down": None, "height": None})
    payload = feed.collect(NOW)
    assert payload["source_verified"] is True
    assert payload["market_precheck"] is None
    emotion = payload["snapshot"]["emotion"]
    for key in ("limit_up", "broken", "limit_down", "height"):
        assert emotion[key] is None
    assert "emotion.limit_up" in payload["server_missing"]
    assert "emotion.source" not in payload["server_missing"]
    assert "emotion.timestamp" not in payload["server_missing"]


def test_collect_does_not_call_sources_outside_session(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("network should not run")
    monkeypatch.setattr(feed, "_fetch_calendar", boom)
    monkeypatch.setattr(feed, "_fetch_cores", boom)
    monkeypatch.setattr(feed, "_fetch_pools", boom)
    payload = feed.collect(NOW.replace(hour=8))
    assert payload["source_verified"] is False
    assert payload["snapshot"] is None
    assert payload["errors"] == ["outside_session"]


def test_collect_does_not_call_sources_during_preopen_auction(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("network should not run before 09:30")
    monkeypatch.setattr(feed, "_fetch_calendar", boom)
    monkeypatch.setattr(feed, "_fetch_cores", boom)
    monkeypatch.setattr(feed, "_fetch_pools", boom)
    payload = feed.collect(NOW.replace(hour=9, minute=15))
    assert payload["source_verified"] is False
    assert payload["errors"] == ["outside_session"]


def test_collect_skips_market_calls_on_holiday(monkeypatch):
    monkeypatch.setattr(feed, "_fetch_calendar", lambda scan_at: {
        "verified": True, "source": feed.SOURCE_CALENDAR,
        "trade_date": scan_at.date().isoformat(), "is_trade_day": False})
    monkeypatch.setattr(feed, "_fetch_cores",
                        lambda scan_at, **kwargs: (_ for _ in ()).throw(AssertionError("no cores")))
    payload = feed.collect(NOW)
    assert payload["source_verified"] is False
    assert "not_trade_day" in payload["errors"]


def test_last_completed_trade_day_uses_previous_close_before_1500(monkeypatch):
    days = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)]
    monkeypatch.setattr(feed.dg, "get_trade_calendar", lambda start, end: days)
    assert feed._last_completed_trade_day(NOW.replace(hour=0, minute=1)) == date(2026, 10, 27)
    assert feed._last_completed_trade_day(NOW.replace(hour=16)) == date(2026, 10, 28)


def test_collect_review_marks_close_snapshot_non_realtime(monkeypatch):
    _patch_success(monkeypatch)
    monkeypatch.setattr(feed.dg, "get_trade_calendar",
                        lambda start, end: [date(2026, 10, 27), date(2026, 10, 28)])
    now = NOW.replace(hour=16)
    payload = feed.collect_review(now)
    assert payload["mode"] == "closed"
    assert payload["data_trade_date"] == "2026-10-28"
    assert payload["review_at"] == "2026-10-28T15:00:00+08:00"
    assert payload["now"] == now
    assert payload["source_verified"] is True
    assert payload["snapshot"]["cores"]
    assert payload["server_missing"] == []


def test_collect_review_reuses_cached_snapshot(monkeypatch):
    """同一收盘时段重复请求只采集一次,不重复打源、也不会瞬间回退成缺失。"""
    _patch_success(monkeypatch)
    monkeypatch.setattr(feed.dg, "get_trade_calendar",
                        lambda start, end: [date(2026, 10, 28)])
    calls = {"n": 0}
    original = feed._collect_uncached

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(feed, "_collect_uncached", counting)
    first = feed.collect_review(NOW.replace(hour=16))
    second = feed.collect_review(NOW.replace(hour=16, minute=30))
    assert calls["n"] == 1
    assert first["data_trade_date"] == "2026-10-28"
    assert second["data_trade_date"] == "2026-10-28"
    assert second["now"] == NOW.replace(hour=16, minute=30)


def test_evaluation_context_disabled_never_touches_feed(monkeypatch):
    from app.screener import dual_core_context as context
    monkeypatch.setattr(context.settings, "dual_core_feed_enabled", False)
    monkeypatch.setattr(context.dual_core_feed, "collect",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("feed")))
    ctx = context.evaluation_context()
    assert ctx["source_verified"] is False
    assert ctx["feed_status"] == "disabled"
    assert ctx["verified_snapshot"] is None


def test_evaluation_context_uses_review_snapshot_outside_session(monkeypatch):
    from app.screener import dual_core_context as context
    monkeypatch.setattr(context.settings, "dual_core_feed_enabled", True)
    actual_now = NOW.replace(hour=16)
    monkeypatch.setattr(context, "evaluation_clock", lambda: actual_now)
    payload = fixture()
    snapshot = {"cores": payload["cores"], "emotion": payload["emotion"]}
    monkeypatch.setattr(context.dual_core_feed, "collect", lambda now: {
        "now": now, "calendar": None, "snapshot": None, "source_verified": False,
        "reference": None, "market_precheck": None, "errors": ["outside_session"]})
    monkeypatch.setattr(context.dual_core_feed, "collect_review", lambda now: {
        "now": now,
        "calendar": {"verified": True, "trade_date": "2026-10-28",
                     "is_trade_day": True, "source": "test_exchange"},
        "snapshot": snapshot, "source_verified": True,
        "reference": "dual_core_feed:review",
        "market_precheck": feed.market_precheck(snapshot),
        "server_missing": [], "errors": [],
        "review_at": "2026-10-28T15:00:00+08:00",
        "data_trade_date": "2026-10-28"})
    ctx = context.evaluation_context()
    assert ctx["feed_status"] == "closed"
    assert ctx["review_only"] is True
    assert ctx["now"] == actual_now
    assert ctx["evaluation_at"] == "2026-10-28T15:00:00+08:00"
    assert ctx["server_missing"] == []
    assert ctx["verified_snapshot"] == snapshot


@pytest.fixture
def api_env():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from app.db import PortfolioBase, ScreenerBase, get_db
    from app.screener import api
    from app.shared.security import current_user_id
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    PortfolioBase.metadata.create_all(engine)
    ScreenerBase.metadata.create_all(engine)
    db = Session(engine)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_id] = lambda: 101
    with TestClient(app) as client:
        yield client, db
    db.close()
    engine.dispose()


def test_user_domain_gaps_never_freeze_the_day(api_env, monkeypatch):
    """服务端 cores/emotion 齐全时,缺少 industry/stock/account 只阻断、不冻结。"""
    client, db = api_env
    payload = fixture()
    snapshot = {"cores": payload["cores"], "emotion": payload["emotion"]}
    from app.screener import dual_core_context as context
    state = {"now": NOW,
             "calendar": {"verified": True, "trade_date": NOW.date().isoformat(),
                          "is_trade_day": True, "source": "test_exchange"},
             "verified_snapshot": snapshot, "source_verified": True,
             "reference": "dual_core_feed:test", "market_precheck": feed.market_precheck(snapshot),
             "errors": []}
    monkeypatch.setattr(context, "evaluation_context", lambda: state)
    body = client.get("/api/screener/dual-core").json()
    assert body["feed_status"] == "verified"
    assert body["day_guard"]["frozen"] is False
    assert body["day_guard"]["events"] == []
    assert "industry" in body["source_missing"]
    assert not any(key.startswith("cores.") or key.startswith("emotion")
                   for key in body["source_missing"])
    assert any(check["key"] == "gun_a" for check in body["checks"])
    assert body["market_precheck"]["daily_ok"] is True
    assert body["legacy_analysis"]["status"] == "historical_reference_only"


def test_server_domain_gap_still_freezes_the_day(api_env, monkeypatch):
    client, db = api_env
    payload = fixture()
    del payload["emotion"]
    from app.screener import dual_core_context as context
    state = {"now": NOW,
             "calendar": {"verified": True, "trade_date": NOW.date().isoformat(),
                          "is_trade_day": True, "source": "test_exchange"},
             "verified_snapshot": {"cores": payload["cores"]},
             "source_verified": True, "reference": "dual_core_feed:test",
             "market_precheck": None, "errors": ["pools:timeout"]}
    monkeypatch.setattr(context, "evaluation_context", lambda: state)
    body = client.get("/api/screener/dual-core").json()
    assert body["day_guard"]["frozen"] is True
    assert body["day_guard"]["reasons"] == ["data_missing"]


def test_closed_review_snapshot_never_authorizes_opening(api_env, monkeypatch):
    """非交易时段的收盘复核快照:可看、可复核,但永不授权开仓/冻结。"""
    client, db = api_env
    payload = fixture()
    review_at = "2026-10-28T15:00:00+08:00"
    for code in payload["cores"]:
        payload["cores"][code]["timestamp"] = review_at
    payload["emotion"]["timestamp"] = review_at
    snapshot = {"cores": payload["cores"], "emotion": payload["emotion"]}
    actual_now = datetime(2026, 10, 29, 0, 5, tzinfo=CN)
    from app.screener import dual_core_context as context
    state = {"now": actual_now,
             "evaluation_at": review_at,
             "calendar": {"verified": True, "trade_date": "2026-10-28",
                          "is_trade_day": True, "source": "test_exchange"},
             "verified_snapshot": snapshot, "source_verified": True,
             "reference": "dual_core_feed:20261028-150000",
             "market_precheck": feed.market_precheck(snapshot),
             "server_missing": [], "feed_status": "closed",
             "feed_trade_date": "2026-10-28", "review_only": True, "errors": []}
    monkeypatch.setattr(context, "evaluation_context", lambda: state)
    body = client.get("/api/screener/dual-core").json()
    assert body["feed_status"] == "closed"
    assert body["review_only"] is True
    assert body["feed_trade_date"] == "2026-10-28"
    # 计算/核验时间必须是本次查询时间,不能把收盘时刻伪装成实时。
    assert body["as_of"] == actual_now.isoformat()
    assert body["decision"]["can_open"] is False
    # 复核快照只能给出只读结论:不能是 open/add/observe,也不产生任何下单指令。
    assert body["decision"]["action"] in ("block", "manual_reference_only")
    assert body["decision"]["orders"] == []
    # 手工域仍缺失时保持 fail-closed 的 source_missing 阻断原因。
    assert body["decision"]["reason"].startswith("source_missing")
    assert body["day_guard"]["frozen"] is False
    assert body["day_guard"]["events"] == []
    # 服务端域齐全:缺口只剩人工台账域,不再出现 5 类 7 项服务端缺口。
    assert not any(key.startswith("cores.") or key.startswith("emotion")
                   for key in body["source_missing"])
    assert set(body["source_missing"]) <= {"industry", "stock", "account", "holdings"}
    assert "industry" in body["source_missing"]
