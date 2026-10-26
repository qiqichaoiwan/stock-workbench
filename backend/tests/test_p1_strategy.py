"""P1 B03/B05 回归:隔离 SQLite 与 mock 行情,禁止联网。"""
import json
import socket
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import ScreenerBase  # noqa: E402
from app.screener import strategy_engine as se  # noqa: E402
from app.screener.models import (  # noqa: E402
    DailyQuote, FinancialIndicator, PoolPerformance, StockBasic, StockPool,
    StrategyParams, TechnicalSignal, Track, TrackMembership, Valuation,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("回归测试禁止外网访问")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(se.dg, "get_index_daily", Mock(side_effect=RuntimeError("offline")))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    ScreenerBase.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("pe,weak,expected", [
    (90, False, ["最新报告期业绩转负", "估值升至5年80%分位以上"]),
    (20, True, ["最新报告期业绩转负", "跑输所属行业(相对强弱弱势)"]),
    (90, True, ["最新报告期业绩转负", "估值升至5年80%分位以上", "跑输所属行业(相对强弱弱势)"]),
    (20, False, ["最新报告期业绩转负"]),
])
def test_rating_aggregates_sell_reasons(pe, weak, expected):
    row = pd.Series(dict(profit_yoy=-10, pe_pct_5y=pe, rs_weak=weak,
                         buy_setup=True, crowd_avoid=False))
    assert se._rating(row) == ("sell", expected)
    row["crowd_avoid"] = True
    assert se._rating(row) == ("avoid", ["SC-U08拥挤度回避行业"])


@pytest.mark.parametrize("pe,weak", [(90, False), (20, True), (90, True), (20, False)])
@pytest.mark.parametrize("incumbent", [False, True])
def test_run_strategy_only_downgrades_single_profit_risk(db, monkeypatch, pe, weak, incumbent):
    day = date(2026, 10, 23)
    code = "600001"
    db.add_all([
        Track(id=1, name="测试赛道", enabled=True),
        TrackMembership(track_id=1, stock_code=code),
        StockBasic(code=code, name="测试股", industry="测试行业", board="主板",
                   total_mv=300, list_date=date(2020, 1, 1)),
        DailyQuote(code=code, trade_date=day, close=20, turnover=3),
        FinancialIndicator(code=code, period="20260630", ann_date=day,
                           roe=10, gross_margin=30, profit_yoy=-10,
                           ocf_to_profit=1, debt_ratio=40),
        Valuation(code=code, trade_date=day, pe_ttm=20, pe_pct_5y=pe),
        TechnicalSignal(code=code, trade_date=day, signal_type="buy_setup"),
        StrategyParams(key="track_score_min", value="0"),
    ])
    if weak:
        db.add(TechnicalSignal(code=code, trade_date=day, signal_type="rs_weak"))
    if incumbent:
        db.add(StockPool(code=code, trade_date=day - timedelta(days=1),
                         pool_type="track", track_id=1, in_price=18, rating="hold"))
    db.commit()
    monkeypatch.setattr("app.screener.market_regime.latest_state", lambda d: None)
    monkeypatch.setattr("app.screener.market_regime.risk_guard_active", lambda d: False)
    monkeypatch.setattr("app.screener.industry_monitor.market_wide_crowded", lambda *a: False)
    monkeypatch.setattr(se, "track_news_heat", lambda d: {})

    result = se.run_strategy(db, day)
    rows = db.execute(select(StockPool)).scalars().all()
    multi_risk = pe > 80 or weak
    if multi_risk and incumbent:
        assert len(rows) == 1
        assert rows[0].status == "out"
        assert rows[0].out_date == day
        assert "最新报告期业绩转负" in rows[0].out_reason
        if pe > 80:
            assert "估值升至5年80%分位以上" in rows[0].out_reason
        if weak:
            assert "跑输所属行业" in rows[0].out_reason
        assert result["outs"] == 1
    elif multi_risk:
        assert rows == []
        assert result["new_entries"] == 0
    else:
        assert len(rows) == 1
        assert rows[0].pool_type == "track"
        assert rows[0].status == "in" and rows[0].rating == "hold"
        assert "业绩转负(赛道观察,降级为持有)" in json.loads(rows[0].reasons)
        assert result["outs"] == 0


@pytest.fixture
def batches(db):
    d1, d2, target = date(2024, 1, 2), date(2026, 10, 21), date(2026, 10, 23)
    for pool_type, entry in [("main", d1), ("track", d1), ("track", d2)]:
        db.add(StockPool(code="600001", trade_date=entry, pool_type=pool_type, in_price=999))
    db.add(StockPool(code="600001", trade_date=target + timedelta(days=1), pool_type="track"))
    for day, close in [(d1, 10), (d2, 20), (target, 30), (target + timedelta(days=1), 999)]:
        db.add(DailyQuote(code="600001", trade_date=day, close=close))
    db.commit()
    return d1, d2, target


def test_performance_separate_batches_asof_and_idempotent(db, monkeypatch, batches):
    d1, d2, target = batches
    benchmark = Mock(return_value=pd.DataFrame([
        {"trade_date": target + timedelta(days=1), "close": 9999},
        {"trade_date": target, "close": 150},
        {"trade_date": d1, "close": 100},
        {"trade_date": d2, "close": 120},
    ]))
    monkeypatch.setattr(se.dg, "get_index_daily", benchmark)
    ids = None
    for _ in range(2):
        assert se.update_pool_performance(db, target) == 2
        rows = db.execute(select(PoolPerformance).order_by(PoolPerformance.pool_entry_date)).scalars().all()
        assert len(rows) == 2
        assert [r.pool_entry_date for r in rows] == [d1, d2]
        assert [r.cum_pct for r in rows] == [200, 50]
        assert [r.benchmark_pct for r in rows] == [50, 25]
        assert all(r.close == 30 and r.trade_date == target for r in rows)
        if ids is not None:
            assert [r.id for r in rows] == ids
        ids = [r.id for r in rows]
    benchmark.assert_called_with("sh000985", d1.strftime("%Y%m%d"), target.strftime("%Y%m%d"))


@pytest.mark.parametrize("mode", ["future_only", "before_entry", "empty", "unavailable"])
def test_performance_unavailable_benchmark_never_uses_future(db, monkeypatch, batches, mode):
    d1, d2, target = batches
    if mode == "unavailable":
        benchmark = Mock(side_effect=RuntimeError("offline"))
    else:
        data = {
            "future_only": [(target + timedelta(days=1), 999), (target + timedelta(days=2), 1999)],
            "before_entry": [(d1 - timedelta(days=1), 100)],
            "empty": [],
        }[mode]
        benchmark = Mock(return_value=pd.DataFrame(data, columns=["trade_date", "close"]))
    monkeypatch.setattr(se.dg, "get_index_daily", benchmark)
    assert se.update_pool_performance(db, target) == 2
    rows = db.execute(select(PoolPerformance)).scalars().all()
    assert len(rows) == 2
    assert sorted(r.cum_pct for r in rows) == [50, 200]
    assert all(r.benchmark_pct == 0 for r in rows)


def test_signals_and_hard_filter_exclude_star_market(db):
    """科创板在信号读取与策略硬过滤两处均被排除(兜住历史数据)。"""
    import datetime

    from app.screener import technical_signal as ts
    day = datetime.date(2026, 10, 23)
    db.add_all([
        TechnicalSignal(code="600001", trade_date=day, signal_type="bottom_fractal"),
        TechnicalSignal(code="688001", trade_date=day, signal_type="bottom_fractal"),
    ])
    db.commit()
    assert [r.code for r in ts.latest_signals(db, limit=10)] == ["600001"]

    df = pd.DataFrame([
        {"code": "600001", "is_st": False, "board": "主板", "close": 10.0,
         "total_mv": 0.0, "list_date": None, "roe": 5.0, "profit_yoy": 10.0,
         "ocf_to_profit": float("nan"), "debt_ratio": float("nan"),
         "gross_margin": 30.0, "turnover": 3.0, "mom20": 5.0,
         "crowd_avoid": False, "roe_ind_pct": float("nan"),
         "ma60": float("nan"), "ma120": float("nan")},
        {"code": "688001", "is_st": False, "board": "科创板", "close": 10.0,
         "total_mv": 0.0, "list_date": None, "roe": 5.0, "profit_yoy": 10.0,
         "ocf_to_profit": float("nan"), "debt_ratio": float("nan"),
         "gross_margin": 30.0, "turnover": 3.0, "mom20": 5.0,
         "crowd_avoid": False, "roe_ind_pct": float("nan"),
         "ma60": float("nan"), "ma120": float("nan")},
    ])
    params = dict(se.PARAM_DEFAULTS)
    params["board_allow"] = "主板,创业板,科创板"  # 旧配置也不能放行
    assert list(se.hard_filter(df, params, as_of=day)["code"]) == ["600001"]


def test_performance_requires_target_day_quote(db, monkeypatch, batches):
    _, _, target = batches
    target_quote = db.execute(select(DailyQuote).where(DailyQuote.trade_date == target)).scalar_one()
    db.delete(target_quote)
    db.commit()
    assert se.update_pool_performance(db, target) == 0
    assert db.execute(select(PoolPerformance)).scalars().all() == []
