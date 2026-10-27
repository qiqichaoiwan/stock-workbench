"""历史日线回填回归:增量抓取、重复日期忽略、失败重试(内存库,不联网)。

覆盖用户反馈的"回填一直成功不了":
- 回填不再逐股全量重复下载,已覆盖到的股票直接跳过;
- 单股源失败会换 auto 源重试一次,不因一只股票把整单判失败;
- 回填源使用 backfill(跳过被限流的东财)。
"""
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import task_runner
from app.db import ScreenerBase
from app.screener.models import DailyQuote

END = date(2026, 10, 23)
TODAY = date(2026, 10, 27)
START_5Y = date(2021, 10, 28)  # TODAY - 5*365


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
def calls(db, monkeypatch):
    """记录每次抓取;(code, source);000001 首轮回填源故意失败。"""
    recorded: list[tuple[str, str, str, str]] = []

    def fake_candidates(_db):
        return ["600001", "000001"]

    def fake_hist(code, start, end=None, source="auto"):
        recorded.append((code, start, end or "", source))
        if code == "000001" and source == "backfill":
            raise RuntimeError("network boom")
        start_day = date(int(start[:4]), int(start[4:6]), int(start[6:]))
        first = max(start_day, START_5Y)
        dates = [first, END] if first < END else [END]
        return pd.DataFrame([{
            "trade_date": d, "open": 10.0, "high": 11.0, "low": 9.0,
            "close": 10.5, "volume": 1000.0, "amount": 1e7,
            "turnover": 1.5, "pct_chg": 0.5,
        } for d in dates]), "sina"

    monkeypatch.setattr(task_runner.universe_manager, "candidate_universe", fake_candidates)
    monkeypatch.setattr(task_runner.dg, "today", lambda: TODAY)
    monkeypatch.setattr(task_runner.dg, "completed_trade_day", lambda at=None: END)
    monkeypatch.setattr(task_runner.dg, "get_daily_history", fake_hist)
    return recorded


def test_backfill_retries_failure_and_skips_covered_stocks(db, calls):
    result = task_runner.task_backfill_history(db, years=5)

    assert result["codes_done"] == 2
    assert result["codes_failed"] == 0
    assert result["codes_skipped"] == 0
    assert result["rows_added"] == 4
    # 两只股票各 2 个交易日
    rows = db.execute(select(DailyQuote)).scalars().all()
    assert len(rows) == 4
    # 000001 首轮 backfill 失败后,第二轮换 tx 源重试成功
    assert ("000001", "backfill") in {(c, s) for c, _st, _e, s in calls}
    assert ("000001", "tx") in {(c, s) for c, _st, _e, s in calls}
    # 回填请求必须跳过东财(backfill/tx 专用源)
    assert {s for _c, _st, _e, s in calls} == {"backfill", "tx"}

    # 第二次执行:覆盖已到最新,应全部跳过且不再请求网络
    before = len(calls)
    result2 = task_runner.task_backfill_history(db, years=5)
    assert result2["codes_done"] == 0
    assert result2["codes_skipped"] == 2
    assert result2["rows_added"] == 0
    assert len(calls) == before


def test_backfill_incremental_only_fetches_new_days(db, calls, monkeypatch):
    db.add(DailyQuote(code="600001", trade_date=START_5Y,
                      open=1, high=1, low=1, close=1, volume=1,
                      amount=1, turnover=1, pct_chg=1))
    db.commit()

    result = task_runner.task_backfill_history(db, years=5)

    # 600001 已有起点数据 → 只补起点后一天;000001 无数据 → 全量
    starts = {(c, s): st for c, st, _e, s in calls}
    assert starts[("600001", "backfill")] == "20211029"
    assert ("000001", "backfill") in starts
    assert result["codes_failed"] == 0


def test_bse_codes_use_bj_symbols(monkeypatch):
    """北交所(920xxx/4x/8x)必须用 bj 前缀,否则各源返回空 → 回填 degraded。"""
    from app.shared import data_gateway as dg

    assert dg._is_bse_code("920011") and dg._is_bse_code("430047")
    assert not dg._is_bse_code("600519") and not dg._is_bse_code("000001")
    assert dg._sina_symbol("920011") == "bj920011"
    assert dg._sina_symbol("600519") == "sh600519"
    assert dg._sina_symbol("000001") == "sz000001"
    assert dg._bs_code("920011") == "bj.920011"
    # baostock 不覆盖北交所:直接返回空而不是抛错
    assert dg._daily_hist_bs("920011", "20211027", "20261027").empty

    import akshare

    seen = {}

    def fake_tx(symbol, start_date, end_date, adjust):
        seen["symbol"] = symbol
        return pd.DataFrame([{
            "date": date(2026, 10, 27), "open": 1.0, "close": 1.0,
            "high": 1.0, "low": 1.0, "amount": 100.0,
        }])

    monkeypatch.setattr(akshare, "stock_zh_a_hist_tx", fake_tx)
    assert not dg._daily_hist_tx("920011", "20261001", "20261027").empty
    assert seen["symbol"] == "bj920011"
