"""指数日线:备源成交额口径、市场总量完整性、同步任务 NaN/覆盖保护。"""
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import task_runner
from app.db import ScreenerBase
from app.screener.models import IndexDaily
from app.shared import data_gateway as dg


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    ScreenerBase.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_tx_source_does_not_treat_volume_as_turnover(monkeypatch):
    """腾讯第 6 列是成交量(手);不能当成交额写库(会少 3 个数量级)。"""
    import akshare as ak

    frame = pd.DataFrame({
        "date": ["2026-10-23", "2026-10-26"],
        "open": [3623.57, 3671.0],
        "close": [3690.48, 3697.52],
        "high": [3691.4, 3699.01],
        "low": [3574.27, 3664.82],
        "amount": [552842912.0, 432794318.0],  # 实为成交量(手)
    })
    monkeypatch.setattr(ak, "stock_zh_index_daily_tx", lambda symbol: frame)

    got = dg._index_daily_tx("sh000001", "20261001", "20261031")
    assert list(got.columns) == ["trade_date", "open", "high", "low", "close", "amount"]
    assert got["amount"].sum() == 0
    assert got["close"].iloc[-1] == pytest.approx(3697.52)


def test_market_total_amount_requires_shanghai_and_shenzhen(monkeypatch):
    """只拿到单个市场时不能冒充全市场成交额(否则行业占比放大约 2 倍)。"""
    def fake_index_daily(code, start, end=None):
        if code == "sh000001":
            raise RuntimeError("eastmoney down")
        return pd.DataFrame({"trade_date": [date(2026, 10, 23)], "amount": [5e10]})

    monkeypatch.setattr(dg, "get_index_daily", fake_index_daily)
    with pytest.raises(RuntimeError, match="missing sh000001"):
        dg.get_market_total_amount("20260901")


def test_market_total_amount_sums_real_turnover_and_skips_bse(monkeypatch):
    amounts = {"sh000001": 5e11, "sz399001": 6e11}
    monkeypatch.setattr(dg, "get_index_daily", lambda code, start, end=None: (
        pd.DataFrame({"trade_date": [date(2026, 10, 23)], "amount": [amounts[code]]})
        if code in amounts else
        pd.DataFrame({"trade_date": [date(2026, 10, 23)], "amount": [0.0]})))

    got = dg.get_market_total_amount("20260901")
    assert got["amount"].iloc[0] == pytest.approx(1.1e12)


def test_sync_indexes_keeps_existing_turnover_when_fallback_has_none(db, monkeypatch):
    """同日重跑遇到腾讯/新浪备源(amount=0)时,保留已有东财成交额。"""
    monkeypatch.setattr(task_runner.dg, "INDEX_DISPLAY", [("sh000001", "上证指数")])
    monkeypatch.setattr(task_runner.dg, "get_index_daily_with_source",
                        lambda code, start: (pd.DataFrame([
                            {"trade_date": date(2026, 10, 23), "open": 3671.0,
                             "high": 3699.01, "low": 3664.82, "close": 3690.0,
                             "amount": 0.0},
                            {"trade_date": date(2026, 10, 27), "open": 3698.44,
                             "high": 3708.2, "low": 3648.3, "close": 3648.3,
                             "amount": 0.0},
                        ]), "tx"))
    db.add(IndexDaily(code="sh000001", name="上证指数", trade_date=date(2026, 10, 27),
                      close=3600.0, pct_chg=0.5, amount=7.4e11, source="em"))
    db.commit()

    assert task_runner.task_sync_indexes(db) == {"indexes": {"sh000001": 1}}
    row = db.execute(select(IndexDaily).where(
        IndexDaily.code == "sh000001", IndexDaily.trade_date == date(2026, 10, 27)
    )).scalar_one()
    assert row.source == "tx"
    assert row.close == pytest.approx(3648.3)
    assert row.amount == pytest.approx(7.4e11)
    assert row.pct_chg == pytest.approx((3648.3 / 3690.0 - 1) * 100)


def test_sync_indexes_cleans_nan_values(db, monkeypatch):
    """NaN 写 SQLite 会变 NULL 触发非空约束;应清洗为 0 而不是整条失败。"""
    monkeypatch.setattr(task_runner.dg, "INDEX_DISPLAY", [("sh000001", "上证指数")])
    monkeypatch.setattr(task_runner.dg, "get_index_spot_amount", lambda codes=None: {})
    monkeypatch.setattr(task_runner.dg, "get_index_daily_with_source",
                        lambda code, start: (pd.DataFrame([
                            {"trade_date": date(2026, 10, 23), "open": 100.0,
                             "high": 100.0, "low": 100.0, "close": 100.0,
                             "amount": float("nan")},
                            {"trade_date": date(2026, 10, 27), "open": 101.0,
                             "high": 101.0, "low": 101.0, "close": 101.0,
                             "amount": float("nan")},
                        ]), "sina"))

    assert task_runner.task_sync_indexes(db) == {"indexes": {"sh000001": 1}}
    row = db.execute(select(IndexDaily)).scalar_one()
    assert row.close == pytest.approx(101.0)
    assert row.amount == 0
    assert row.pct_chg == pytest.approx(1.0)


def test_sync_indexes_fills_today_turnover_from_spot_snapshot(db, monkeypatch):
    """当日走备源(amount=0)时,用指数快照成交额补当日值(收盘后即全额)。"""
    monkeypatch.setattr(task_runner.dg, "INDEX_DISPLAY", [("sh000001", "上证指数")])
    monkeypatch.setattr(task_runner.dg, "today", lambda: date(2026, 10, 27))
    monkeypatch.setattr(task_runner.dg, "get_index_daily_with_source",
                        lambda code, start: (pd.DataFrame([
                            {"trade_date": date(2026, 10, 26), "open": 3698.44,
                             "high": 3708.2, "low": 3664.82, "close": 3697.52,
                             "amount": 0.0},
                            {"trade_date": date(2026, 10, 27), "open": 3698.44,
                             "high": 3708.2, "low": 3648.3, "close": 3648.3,
                             "amount": 0.0},
                        ]), "tx"))
    calls = []

    def fake_spot(codes=None):
        calls.append(codes)
        return {"sh000001": 6.72414398244e11}

    monkeypatch.setattr(task_runner.dg, "get_index_spot_amount", fake_spot)

    assert task_runner.task_sync_indexes(db) == {"indexes": {"sh000001": 1}}
    row = db.execute(select(IndexDaily).where(
        IndexDaily.code == "sh000001", IndexDaily.trade_date == date(2026, 10, 27)
    )).scalar_one()
    assert row.amount == pytest.approx(6.72414398244e11)
    assert row.source == "tx"  # 点位仍来自备源,只有成交额来自快照
    assert len(calls) == 1  # 快照全表一次,不逐指数重复拉


def test_sync_indexes_skips_spot_fill_for_past_dates(db, monkeypatch):
    """补值只针对当日:补数据日跑(如非交易日)时不能把今日快照成交额挂到旧日期。"""
    monkeypatch.setattr(task_runner.dg, "INDEX_DISPLAY", [("sh000001", "上证指数")])
    monkeypatch.setattr(task_runner.dg, "today", lambda: date(2026, 10, 27))
    monkeypatch.setattr(task_runner.dg, "get_index_daily_with_source",
                        lambda code, start: (pd.DataFrame([
                            {"trade_date": date(2026, 10, 26), "open": 3698.44,
                             "high": 3708.2, "low": 3664.82, "close": 3697.52,
                             "amount": 0.0},
                        ]), "tx"))
    calls = []
    monkeypatch.setattr(task_runner.dg, "get_index_spot_amount",
                        lambda codes=None: calls.append(codes) or {"sh000001": 6.7e11})

    task_runner.task_sync_indexes(db)
    row = db.execute(select(IndexDaily)).scalar_one()
    assert row.amount == 0
    assert calls == []
