"""交易日线写入及持仓巡检的隔离回归测试。"""
from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import task_runner
from app.db import PortfolioBase, ScreenerBase
from app.portfolio import holding_service, report_service
from app.portfolio.models import Holding, Watchlist
from app.screener.models import DailyQuote


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    PortfolioBase.metadata.create_all(engine)
    ScreenerBase.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def snapshot():
    return pd.DataFrame([
        {"code": code, "price": price, "open": price, "high": price, "low": price}
        for code, price in (("600001", 10), ("688001", 20), ("000002", 30), ("000003", 40))
    ])


@pytest.mark.parametrize("now,is_trade_day", [
    (datetime(2026, 10, 27, 10, 0), True),
    (datetime(2026, 10, 27, 14, 59), True),
    (datetime(2026, 10, 25, 16, 0), False),
])
def test_sync_rejects_snapshot_without_a_completed_current_session(db, monkeypatch, now, is_trade_day):
    prior = date(2026, 10, 26)
    db.add(DailyQuote(code="600001", trade_date=prior, open=10, high=10, low=10, close=10))
    db.commit()
    monkeypatch.setattr(task_runner.dg, "now_tz", lambda: now)
    monkeypatch.setattr(task_runner.dg, "is_trade_day", lambda d: is_trade_day)
    monkeypatch.setattr(task_runner.dg, "completed_trade_day", lambda: prior)
    monkeypatch.setattr(task_runner.dg, "validate_quote_date", lambda d: d)
    monkeypatch.setattr(task_runner.dg, "get_spot_snapshot_with_source", lambda: (snapshot(), "test"))
    monkeypatch.setattr(task_runner.universe_manager, "candidate_universe", lambda db: ["600001"])

    with pytest.raises(ValueError, match="收盘"):
        task_runner.task_sync_quotes_from_spot(db)
    assert db.execute(select(DailyQuote)).scalar_one().close == 10


def test_sync_includes_open_holdings_and_watchlist_outside_candidate_universe(db, monkeypatch):
    day = date(2026, 10, 27)
    db.add_all([
        Holding(user_id=1, code="688001", buy_price=10, buy_date=day, status="holding"),
        Holding(user_id=1, code="000003", buy_price=10, buy_date=day, status="sold"),
        Watchlist(user_id=1, code="000002"),
    ])
    db.commit()
    monkeypatch.setattr(task_runner.dg, "now_tz", lambda: datetime(2026, 10, 27, 15, 35))
    monkeypatch.setattr(task_runner.dg, "is_trade_day", lambda d: True)
    monkeypatch.setattr(task_runner.dg, "completed_trade_day", lambda: day)
    monkeypatch.setattr(task_runner.dg, "validate_quote_date", lambda d: d)
    monkeypatch.setattr(task_runner.dg, "get_spot_snapshot_with_source", lambda: (snapshot(), "test"))
    monkeypatch.setattr(task_runner.dg, "_fetch_sina_batch_quotes", lambda codes: (
        {r["code"]: {**r, "quote_at": "2026-10-27T15:00:00+08:00"} for r in snapshot().to_dict("records")},
        "2026-10-27T15:00:00+08:00"))
    monkeypatch.setattr(task_runner.dg, "get_daily_history", lambda code, *args, **kwargs: (
        pd.DataFrame([{**r, "close": r["price"], "trade_date": day} for r in snapshot().to_dict("records") if r["code"] == code]), "test"))
    monkeypatch.setattr(task_runner.universe_manager, "candidate_universe", lambda db: ["600001"])

    result = task_runner.task_sync_quotes_from_spot(db)
    assert result["quotes"] == 3
    assert {row.code for row in db.execute(select(DailyQuote)).scalars()} == {"600001", "688001", "000002"}


@pytest.mark.parametrize("close", [80, 120])
def test_patrol_does_not_use_quote_before_buy_date(db, close):
    db.add(DailyQuote(code="600001", trade_date=date(2026, 10, 26), open=close, high=close, low=close, close=close))
    holding = Holding(user_id=1, code="600001", buy_price=100, buy_date=date(2026, 10, 27),
                      status="holding", stop_stage="A", stop_price=90, initial_stop_price=90,
                      breakeven_price=100.1, risk_r=10, highest_price=100)
    db.add(holding)
    db.commit()

    alerts = holding_service.update_daily(db, user_id=1, trade_date=date(2026, 10, 27))
    db.refresh(holding)
    assert holding.stop_stage == "A"
    assert holding.stop_price == 90
    assert holding.highest_price == 100
    assert alerts == []


def test_sync_skips_unverifiable_and_stale_rows_without_overwriting_other_columns(db, monkeypatch):
    day = date(2026, 10, 27)
    db.add(DailyQuote(code="600001", trade_date=day, open=9, high=11, low=8, close=10,
                      volume=1234, amount=5678, turnover=3, total_mv=500))
    db.commit()
    monkeypatch.setattr(task_runner.dg, "now_tz", lambda: datetime(2026, 10, 27, 15, 35))
    monkeypatch.setattr(task_runner.dg, "is_trade_day", lambda d: True)
    monkeypatch.setattr(task_runner.dg, "completed_trade_day", lambda: day)
    monkeypatch.setattr(task_runner.dg, "validate_quote_date", lambda d: d)
    monkeypatch.setattr(task_runner.universe_manager, "candidate_universe", lambda db: ["600001", "600002", "600003", "600004"])
    monkeypatch.setattr(task_runner.dg, "get_spot_snapshot_with_source", lambda: (snapshot(), "test"))
    monkeypatch.setattr(task_runner.dg, "_fetch_sina_batch_quotes", lambda codes: ({
        "600001": {"open": 11, "high": 13, "low": 10, "price": 12, "quote_at": "2026-10-27T15:00:00+08:00"},
        "600002": {"open": 11, "high": 13, "low": 10, "price": 12, "quote_at": "2026-10-26T15:00:00+08:00"},
        "600003": {"open": 11, "high": 13, "low": 10, "price": 12, "quote_at": None},
        "600004": {"open": None, "high": 13, "low": 10, "price": 12, "quote_at": "2026-10-27T15:00:00+08:00"},
    }, "2026-10-27T15:00:00+08:00"))

    monkeypatch.setattr(task_runner.dg, "get_daily_history", lambda *args, **kwargs: (pd.DataFrame([
        {"trade_date": day, "open": 11, "high": 13, "low": 10, "close": 12}
    ]), "test"))
    result = task_runner.task_sync_quotes_from_spot(db)
    rows = db.execute(select(DailyQuote)).scalars().all()
    assert len(rows) == 1 and rows[0].close == 12
    assert (rows[0].volume, rows[0].amount, rows[0].turnover, rows[0].total_mv) == (1234, 5678, 3, 500)
    assert result["quotes"] == 1 and result["errors"]
    assert task_runner._is_degraded(result)


@pytest.mark.parametrize("quote_day,buy_day", [
    (date(2026, 10, 26), date(2026, 10, 27)),
    (date(2026, 10, 10), date(2026, 10, 1)),
    (date(2026, 10, 26), date(2026, 10, 1)),
])
def test_daily_report_does_not_alert_from_pre_buy_or_old_quote(db, monkeypatch, quote_day, buy_day):
    db.add(DailyQuote(code="600001", trade_date=quote_day, open=80, high=80, low=80, close=80))
    db.add(Holding(user_id=1, code="600001", buy_price=100, buy_date=buy_day,
                   status="holding", stop_stage="A", stop_price=90, initial_stop_price=90))
    db.commit()
    monkeypatch.setattr(report_service.stock_ref.dg, "completed_trade_day", lambda: date(2026, 10, 27))
    assert report_service._alerts(db, 1) == []


def test_daily_report_fails_closed_when_calendar_is_unavailable(db, monkeypatch):
    db.add(Holding(user_id=1, code="600001", buy_price=100, buy_date=date(2026, 10, 1),
                   status="holding", stop_stage="A", stop_price=90, initial_stop_price=90))
    db.commit()
    monkeypatch.setattr(report_service.stock_ref.dg, "completed_trade_day",
                        lambda: (_ for _ in ()).throw(RuntimeError("calendar down")))
    assert report_service._alerts(db, 1) == []


def test_sina_batch_includes_open_and_share_volume(monkeypatch):
    fields = ["示例", "10.000", "9.990", "10.200", "10.300", "9.800",
              "10.190", "10.200", "1000", "1020000.000"] + ["0"] * 20 + ["2026-10-27", "15:00:00", "00"]
    class Response:
        text = f'var hq_str_sh600001="{",".join(fields)}";'
        def raise_for_status(self):
            pass
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    quotes, _ = task_runner.dg._fetch_sina_batch_quotes({"600001"})
    assert quotes["600001"]["open"] == 10
    assert quotes["600001"]["volume"] == 1000
    assert quotes["600001"]["quote_at"] == "2026-10-27T15:00:00+08:00"


def test_sync_rejects_adjustment_mismatch_in_historical_quotes(db, monkeypatch):
    day = date(2026, 10, 27)
    previous = date(2026, 10, 26)
    db.add(DailyQuote(code="600001", trade_date=previous, open=20, high=20, low=20, close=20))
    db.commit()
    monkeypatch.setattr(task_runner.dg, "now_tz", lambda: datetime(2026, 10, 27, 15, 35))
    monkeypatch.setattr(task_runner.dg, "is_trade_day", lambda d: True)
    monkeypatch.setattr(task_runner.dg, "completed_trade_day", lambda: day)
    monkeypatch.setattr(task_runner.dg, "validate_quote_date", lambda d: d)
    monkeypatch.setattr(task_runner.universe_manager, "candidate_universe", lambda db: ["600001"])
    monkeypatch.setattr(task_runner.dg, "_fetch_sina_batch_quotes", lambda codes: ({
        "600001": {"open": 10, "high": 11, "low": 9, "price": 10, "quote_at": "2026-10-27T15:00:00+08:00"}
    }, "2026-10-27T15:00:00+08:00"))
    monkeypatch.setattr(task_runner.dg, "get_spot_snapshot_with_source", lambda: (snapshot(), "test"))
    monkeypatch.setattr(task_runner.dg, "get_daily_history", lambda *args, **kwargs: (pd.DataFrame([
        {"trade_date": previous, "open": 10, "high": 10, "low": 10, "close": 10},
        {"trade_date": day, "open": 10, "high": 11, "low": 9, "close": 10},
    ]), "test"))
    result = task_runner.task_sync_quotes_from_spot(db)
    assert result["quotes"] == 0 and result["errors"]
    assert db.execute(select(DailyQuote)).scalar_one().trade_date == previous


def test_hard_filter_does_not_admit_unknown_turnover():
    from app.screener.strategy_engine import PARAM_DEFAULTS, hard_filter
    row = {"code": "600001", "is_st": False, "board": "主板", "close": 10.0,
           "total_mv": 300.0, "list_date": None, "roe": 5.0, "profit_yoy": 10.0,
           "ocf_to_profit": 1.0, "debt_ratio": 40.0, "gross_margin": 30.0,
           "turnover": 0.0, "mom20": 5.0, "crowd_avoid": False,
           "roe_ind_pct": float("nan"), "ma60": float("nan"), "ma120": float("nan")}
    for track_mode in (False, True):
        assert hard_filter(pd.DataFrame([row]), PARAM_DEFAULTS, as_of=date(2026, 10, 27), track_mode=track_mode).empty


@pytest.mark.parametrize("old_new,expect_insert", [(10.0, True), (5.0, False)])
def test_backfill_refuses_mixed_adjustment_baselines(db, monkeypatch, old_new, expect_insert):
    previous, day = date(2026, 10, 26), date(2026, 10, 27)
    db.add(DailyQuote(code="600001", trade_date=previous, open=10, high=10, low=10, close=10))
    db.commit()
    monkeypatch.setattr(task_runner.dg, "today", lambda: day)
    monkeypatch.setattr(task_runner.dg, "completed_trade_day", lambda: day)
    monkeypatch.setattr(task_runner.universe_manager, "candidate_universe", lambda db: ["600001"])
    def history(code, start, end, source):
        rows = [{"trade_date": previous, "open": old_new, "high": old_new, "low": old_new,
                 "close": old_new},
                {"trade_date": day, "open": 11, "high": 11, "low": 11, "close": 11}]
        return pd.DataFrame([r for r in rows if r["trade_date"] >= datetime.strptime(start, "%Y%m%d").date()]), "test"
    monkeypatch.setattr(task_runner.dg, "get_daily_history", history)
    result = task_runner.task_backfill_history(db, years=1)
    rows = db.execute(select(DailyQuote).order_by(DailyQuote.trade_date)).scalars().all()
    assert len(rows) == (2 if expect_insert else 1)
    assert bool(result["codes_failed"]) != expect_insert
    assert rows[0].close == 10


def test_close_sync_rotates_past_failure_after_time_budget(db, monkeypatch):
    import types

    day = date(2026, 10, 27)
    monkeypatch.setattr(task_runner.dg, "now_tz", lambda: datetime(2026, 10, 27, 15, 35))
    monkeypatch.setattr(task_runner.dg, "is_trade_day", lambda d: True)
    monkeypatch.setattr(task_runner.dg, "completed_trade_day", lambda: day)
    monkeypatch.setattr(task_runner.dg, "validate_quote_date", lambda d: d)
    monkeypatch.setattr(task_runner.universe_manager, "candidate_universe", lambda db: ["600001", "600002", "600003"])
    monkeypatch.setattr(task_runner, "_sync_quote_cursor", None, raising=False)
    clock = {"time": 0}
    monkeypatch.setattr(task_runner, "time", types.SimpleNamespace(monotonic=lambda: clock["time"]))
    monkeypatch.setattr(task_runner.dg, "_fetch_sina_batch_quotes", lambda codes: ({
        code: {"open": 10, "high": 10, "low": 10, "price": 10,
               "quote_at": "2026-10-27T15:00:00+08:00"} for code in codes
    }, "2026-10-27T15:00:00+08:00"))
    calls = []
    def unavailable(code, *args, **kwargs):
        calls.append(code)
        clock["time"] = 121
        raise RuntimeError("unavailable")
    monkeypatch.setattr(task_runner.dg, "get_daily_history", unavailable)
    monkeypatch.setattr(task_runner.dg, "_run_with_timeout", lambda fn, timeout, *a, **kw: fn(*a, **kw))
    first = task_runner.task_sync_quotes_from_spot(db)
    assert first["errors"] and first["unprocessed"] == 2
    clock["time"] = 0
    second = task_runner.task_sync_quotes_from_spot(db)
    assert second["errors"] and second["unprocessed"] == 2
    assert calls == ["600001", "600002"]


def test_sina_daily_keeps_volume_in_shares_and_converts_turnover_ratio(monkeypatch):
    import akshare
    data = pd.DataFrame([{"date": date(2026, 10, 27), "open": 10, "high": 11, "low": 9,
                          "close": 10.5, "volume": 64270729, "amount": 673942654,
                          "turnover": 0.001930}])
    monkeypatch.setattr(akshare, "stock_zh_a_daily", lambda **kwargs: data.copy())
    result = task_runner.dg._daily_hist_sina("600001", "20261027", "20261027")
    assert result.iloc[0]["volume"] == 64270729
    assert result.iloc[0]["turnover"] == pytest.approx(0.1930)


def test_close_sync_batches_and_prioritizes_user_stocks(db, monkeypatch):
    day = date(2026, 10, 27)
    db.add(Holding(user_id=1, code="688001", buy_price=10, buy_date=day, status="holding"))
    db.commit()
    universe = [f"{600000 + i:06d}" for i in range(50)]
    monkeypatch.setattr(task_runner.dg, "now_tz", lambda: datetime(2026, 10, 27, 15, 35))
    monkeypatch.setattr(task_runner.dg, "is_trade_day", lambda d: True)
    monkeypatch.setattr(task_runner.dg, "completed_trade_day", lambda: day)
    monkeypatch.setattr(task_runner.dg, "validate_quote_date", lambda d: d)
    monkeypatch.setattr(task_runner.universe_manager, "candidate_universe", lambda db: universe)
    monkeypatch.setattr(task_runner, "_sync_quote_cursor", None)
    calls = []
    def batch(codes):
        calls.append(set(codes))
        if len(calls) == 2:
            raise RuntimeError("batch unavailable")
        return ({code: {"open": 10, "high": 10, "low": 10, "price": 10,
                        "quote_at": "2026-10-27T15:00:00+08:00"} for code in codes},
                "2026-10-27T15:00:00+08:00")
    monkeypatch.setattr(task_runner.dg, "_fetch_sina_batch_quotes", batch)
    monkeypatch.setattr(task_runner.dg, "get_daily_history", lambda *a, **k: (
        pd.DataFrame([{"trade_date": day, "open": 10, "high": 10, "low": 10, "close": 10}]), "test"))
    monkeypatch.setattr(task_runner.dg, "_run_with_timeout", lambda fn, timeout, *a, **kw: fn(*a, **kw))
    result = task_runner.task_sync_quotes_from_spot(db)
    assert len(calls) == 2 and max(map(len, calls)) <= 50
    assert "688001" in calls[0]
    assert result["quotes"] == 50 and result["errors"]
    assert db.execute(select(DailyQuote).where(DailyQuote.code == "688001")).scalar_one()
