"""P1 B01/B04 回归：完整计算入口、内存 SQLite、外部数据全 mock。"""
import json
import os
import socket
import sys
from datetime import date, timedelta
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["FERNET_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["JWT_SECRET"] = "p1-market-tests-only-not-a-production-secret"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import ScreenerBase
from app.screener import industry_monitor as im
from app.screener import market_regime as mr
from app.screener.models import IndustryDaily, MarketState, StrategyCard


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("P1 regression must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    ScreenerBase.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("previous_days,current_days", [
    (None, None), (-1, None), (40, None), (5, None), (40, 50), (50, 40),
])
def test_crowd_spike_merges_valid_cooldowns(db, monkeypatch, previous_days, current_days):
    days = pd.bdate_range(end="2026-10-23", periods=130).date.tolist()
    target = days[-1]
    fresh = pd.bdate_range(start=target, periods=21).date[-1]
    hist = pd.DataFrame({
        "trade_date": days, "close": [100.0] * 130,
        "pct_chg": [0.0] * 130, "amount": [5.0] * 129 + [25.0],
    })
    monkeypatch.setattr(im.dg, "get_industry_board_list", lambda: pd.DataFrame([
        {"board_code": "B01", "board_name": "测试行业"},
    ]))
    monkeypatch.setattr(im.dg, "get_industry_hist", lambda *args: hist.copy())
    monkeypatch.setattr(im.dg, "get_market_total_amount", lambda *args: pd.DataFrame({
        "trade_date": days, "amount": [100.0] * 130,
    }))
    monkeypatch.setattr(im.dg, "get_industry_fund_flow", lambda: pd.DataFrame({
        "board_name": ["测试行业"], "fund_flow": [1.0],
    }))
    monkeypatch.setattr(im.dg, "get_trade_calendar", lambda *args:
                        pd.bdate_range(start=target, periods=30).date.tolist())
    inherited = []
    for day, offset in ((days[-2], previous_days), (target, current_days)):
        if offset is not None:
            until = target + timedelta(days=offset)
            db.add(IndustryDaily(industry_code="B01", industry_name="测试行业",
                                 trade_date=day, avoid_until=until))
            if until >= target:
                inherited.append(until)
    db.commit()
    expected = max([fresh, *inherited])

    for _ in range(3):
        assert im.sync_industry_daily(db, target) == 1
        row = db.scalars(select(IndustryDaily).where(
            IndustryDaily.trade_date == target)).one()
        assert row.ratio == 25.0
        assert row.ratio_ma120 == pytest.approx((119 * 5 + 25) / 120, abs=0.0001)
        assert row.ratio_score > im.CROWD_THRESHOLD
        assert row.avoid_until == expected
        assert im.crowd_avoid_industries(db, target) == ["测试行业"]
    card = db.scalars(select(StrategyCard)).one()
    assert json.loads(card.evidence)["current_avoid"] == ["测试行业"]


@pytest.fixture
def market_data(monkeypatch):
    days = pd.bdate_range(end="2026-10-23", periods=130).date.tolist()
    monkeypatch.setattr(mr, "_benchmark_daily", lambda start: pd.DataFrame({
        "trade_date": days, "close": [200.0 - i * 0.5 for i in range(130)],
    }))
    monkeypatch.setattr(mr.dg, "get_market_total_amount", lambda start: pd.DataFrame({
        "trade_date": days, "amount": [100.0] * 130,
    }))
    monkeypatch.setattr(mr, "_all_a_valuation_pct", lambda: 50.0)
    monkeypatch.setattr(mr, "_margin_dev", lambda start: (1.0, 0.0))
    return days[-1]


def _seed_hot_stage(db, end):
    days = pd.bdate_range(end=end, periods=65).date.tolist()
    for day in days:
        db.add(MarketState(trade_date=day, cycle_stage="过热", position_range="20%-30%"))
    db.commit()
    return days[0]


def _snapshot(row):
    return (row.cycle_stage, row.position_range, row.stage_pending,
            row.pending_count, row.score_json, row.breadth,
            row.amount_pct_60d, row.valuation_pct, row.margin_ratio)


def test_market_same_day_idempotent_next_trade_day_confirms(db, market_data):
    target = market_data
    _seed_hot_stage(db, date(2026, 10, 22))
    first = mr.compute_market_state(db, target)
    assert (first.cycle_stage, first.stage_pending, first.pending_count) == ("过热", "复苏", 1)
    assert json.loads(first.score_json)["raw_stage"] == "复苏"
    expected = _snapshot(first)
    for _ in range(3):
        assert _snapshot(mr.compute_market_state(db, target)) == expected
    assert db.scalar(select(func.count()).select_from(MarketState).where(
        MarketState.trade_date == target)) == 1

    next_day = date(2026, 10, 26)
    confirmed = mr.compute_market_state(db, next_day)
    assert (confirmed.cycle_stage, confirmed.position_range,
            confirmed.stage_pending, confirmed.pending_count) == ("复苏", "50%-70%", "", 0)
    expected = _snapshot(confirmed)
    for _ in range(3):
        assert _snapshot(mr.compute_market_state(db, next_day)) == expected


@pytest.mark.parametrize("with_history", [False, True])
def test_historical_recompute_ignores_future_states(db, market_data, with_history):
    target = market_data
    if with_history:
        _seed_hot_stage(db, date(2026, 10, 22))
    baseline = _snapshot(mr.compute_market_state(db, target))
    future = MarketState(trade_date=date(2026, 10, 26), cycle_stage="衰退",
                         position_range="80%-100%", stage_pending="复苏", pending_count=9)
    db.add(future)
    db.commit()
    future_before = _snapshot(future)
    for _ in range(3):
        assert _snapshot(mr.compute_market_state(db, target)) == baseline
    assert _snapshot(db.get(MarketState, future.trade_date)) == future_before


def test_dwell_start_excludes_same_day_and_future_rows(db, monkeypatch, market_data):
    target = market_data
    oldest = _seed_hot_stage(db, target - timedelta(days=14))
    db.add(MarketState(trade_date=target, cycle_stage="过热", position_range="20%-30%"))
    db.add(MarketState(trade_date=target + timedelta(days=3), cycle_stage="过热",
                       position_range="20%-30%"))
    db.commit()
    apply = mr._apply_hysteresis
    seen = []

    def capture(prev, stage, position_range, today, prev_stage_start=None):
        seen.append((prev.trade_date, prev_stage_start))
        return apply(prev, stage, position_range, today, prev_stage_start)

    monkeypatch.setattr(mr, "_apply_hysteresis", capture)
    for _ in range(2):
        row = mr.compute_market_state(db, target)
        assert (row.cycle_stage, row.stage_pending, row.pending_count) == ("过热", "复苏", 1)
    assert seen == [(target - timedelta(days=14), oldest)] * 2
