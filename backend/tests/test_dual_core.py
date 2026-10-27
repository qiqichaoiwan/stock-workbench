"""双核共振判定引擎:周线聚合、昨日高/历史高区分、五种结论、清单持久化与接口。"""
from datetime import date, timedelta

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import ScreenerBase, get_db
from app.screener import api, dual_core
from app.screener.models import DualCoreDaily, TradeChecklist
from app.shared.security import current_user_id


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    ScreenerBase.metadata.create_all(engine)
    return Session(engine)


def _weekdays(n: int, start: date = date(2024, 1, 1)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _rows(code: str, n: int = 175, base: float = 100.0, slope: float = 0.5) -> list[DualCoreDaily]:
    """构造单调上升的日线:close=base+slope*i,high=close+0.4,low=close-0.4。"""
    days = _weekdays(n)
    out = []
    for i, d in enumerate(days):
        close = base + slope * i
        out.append(DualCoreDaily(code=code, name="测试指数", trade_date=d,
                                 open=close - 0.1, high=close + 0.4,
                                 low=close - 0.4, close=close, amount=1e10, pct_chg=0.1))
    return out


def _upsert(rows: list[DualCoreDaily], patch: dict) -> list[DualCoreDaily]:
    """按索引覆盖最后若干行(索引为负),便于构造极端日。"""
    for idx, values in patch.items():
        row = rows[idx]
        for k, v in values.items():
            setattr(row, k, v)
    return rows


def _init_db(rows: dict[str, list[DualCoreDaily]]) -> Session:
    db = _db()
    for rs in rows.values():
        db.add_all(rs)
    db.commit()
    return db


def _level(db: Session) -> dict:
    payload = dual_core.analyze(db)
    assert payload["ready"] is True
    # v1 behavior is retained strictly as a historical reference, never live.
    assert payload['verdict']['cap'] == 0
    assert payload['source_missing']
    payload = dict(payload, **payload['legacy_analysis'])
    return payload


def test_weekly_bars_aggregate_ohlc():
    """周线由日线按 W-FRI 聚合:open 首日、high 最高、low 最低、close 末日。"""
    days = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21),
            date(2026, 10, 22), date(2026, 10, 23),
            date(2026, 10, 26), date(2026, 10, 27)]
    closes = [10.0, 11.0, 12.0, 11.5, 13.0, 12.0, 14.0]
    df = pd.DataFrame({
        "trade_date": days,
        "open": [c - 0.5 for c in closes],
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "amount": [1e8] * len(days),
    })
    weekly = dual_core.weekly_bars(df)
    assert len(weekly) == 2
    assert weekly["high"].iloc[0] == 14.0   # 13.0 + 1
    assert weekly["low"].iloc[0] == 9.0     # 10.0 - 1
    assert weekly["close"].iloc[0] == 13.0
    assert weekly["close"].iloc[1] == 14.0  # 当周未完成周仍取最新 close
    assert weekly["open"].iloc[1] == 11.5   # 12.0 - 0.5


def test_prev_high_is_yesterday_not_history():
    """昨日高 ≠ 历史高:历史最高应取不含当日的全序列最大值。"""
    rows = _rows("883957", n=60, base=100.0, slope=0.0)
    _upsert(rows, {-20: {"high": 500.0}, -2: {"high": 120.0, "low": 110.0}})
    db = _init_db({"883957": rows, "883994": _rows("883994")})
    data = _level(db)["data"]["883957"]
    assert data["prev_high"] == 120.0        # 昨日最高
    assert data["ath_high"] == 500.0         # 历史最高(不含当日)
    db.close()


def test_attack_when_both_cores_break_and_ma5_resonates():
    a_rows, b_rows = _rows("883957"), _rows("883994", slope=0.4)
    # A: 现价 > 昨日最高; B: 盘中 ≥ 历史最高
    a_prev_high = a_rows[-2].high
    _upsert(a_rows, {-1: {"close": a_prev_high + 1.0, "high": a_prev_high + 1.3}})
    b_ath = max(r.high for r in b_rows[:-1])
    _upsert(b_rows, {-1: {"close": b_ath + 1.0, "high": b_ath + 1.5}})
    db = _init_db({"883957": a_rows, "883994": b_rows})
    payload = _level(db)
    v = payload["verdict"]
    assert v["level"] == "进攻"
    assert v["veto"] is False
    assert v["daily_ok"] is True
    assert v["ma5_ok"] is True
    assert v["cap"] in (50, 100)             # 周线不配合时减半
    assert payload["checks"][0]["pass"] is True
    db.close()


def test_veto_when_core_b_no_new_high():
    """883994 未创历史新高 = 一票否决,即使全A 突破昨高也空仓。"""
    a_rows, b_rows = _rows("883957"), _rows("883994", slope=0.4)
    a_prev_high = a_rows[-2].high
    _upsert(a_rows, {-1: {"close": a_prev_high + 1.0, "high": a_prev_high + 1.3}})
    _upsert(b_rows, {-1: {"high": max(r.high for r in b_rows[:-1]) - 1.0}})
    db = _init_db({"883957": a_rows, "883994": b_rows})
    payload = _level(db)
    v = payload["verdict"]
    assert v["level"] == "空仓"
    assert v["veto"] is True
    assert v["cap"] == 0
    assert payload["checks"][0]["veto"] is True
    assert payload["checks"][0]["pass"] is False
    db.close()


def test_retreat_line_forces_empty_position():
    """883994 创新高但 883957 跌破昨日最低 → 退军线,尾盘全清。"""
    a_rows, b_rows = _rows("883957"), _rows("883994", slope=0.4)
    b_ath = max(r.high for r in b_rows[:-1])
    _upsert(b_rows, {-1: {"close": b_ath + 1.0, "high": b_ath + 1.5}})
    _upsert(a_rows, {-1: {"close": a_rows[-2].low - 1.0, "high": a_rows[-2].low - 0.5}})
    db = _init_db({"883957": a_rows, "883994": b_rows})
    payload = _level(db)
    v = payload["verdict"]
    assert v["level"] == "空仓"
    assert v["veto"] is False
    assert any("退军线" in r for r in v["reasons"])
    retreat = next(c for c in payload["checks"] if c["key"] == "retreat_line")
    assert retreat["pass"] is False
    db.close()


def test_probe_when_ma5_not_resonating():
    """日线双新高已触发但 5 日线走平/向下 → 轻仓试错。"""
    a_rows = _rows("883957")
    a_prev_high = a_rows[-2].high
    _upsert(a_rows, {-1: {"close": a_prev_high + 1.0, "high": a_prev_high + 1.3}})
    b_rows = _rows("883994", slope=0.4)
    b_ath = max(r.high for r in b_rows[:-1])
    # 近 6 日:巨量尖顶 → 回落 → 今日冲高创历史新高但收回;MA5 向下、收盘仍在 MA5 上方
    _upsert(b_rows, {
        -6: {"close": 500.0, "high": 501.0, "low": 499.0},
        -5: {"close": 100.0, "high": 101.0, "low": 99.0},
        -4: {"close": 100.0, "high": 101.0, "low": 99.0},
        -3: {"close": 100.0, "high": 101.0, "low": 99.0},
        -2: {"close": 100.0, "high": 101.0, "low": 99.0},
        -1: {"close": 300.0, "high": 502.0, "low": 99.0},
    })
    db = _init_db({"883957": a_rows, "883994": b_rows})
    payload = _level(db)
    v = payload["verdict"]
    assert payload["data"]["883994"]["new_high_intraday"] is True
    assert payload["data"]["883994"]["ma5_up"] is False
    assert v["ma5_ok"] is False
    assert v["level"] == "轻仓试错"
    assert v["cap"] in (20, 40)
    db.close()


def test_stale_cores_flagged_when_dates_differ():
    """两核数据日不一致(单核同步失败)时标记 stale,并在理由里提示先对齐数据。"""
    a_rows = _rows("883957")
    b_rows = _rows("883994", slope=0.4)[:-1]      # 少一天 = 数据落后
    db = _init_db({"883957": a_rows, "883994": b_rows})
    payload = _level(db)
    assert payload["stale"] is True
    assert any("数据不同步" in r for r in payload["verdict"]["reasons"])
    db.close()


def test_not_ready_without_data():
    db = _db()
    payload = dual_core.analyze(db)
    assert payload["ready"] is False
    assert payload["checks"][0]['key'] == 'data'
    assert payload["checks"][0]['pass'] is False
    assert payload['source_missing']
    assert "dual_core" in payload["message"]
    db.close()


def test_checklist_persist_per_user_and_day():
    """清单 PUT 整份覆盖、按 user_id + trade_date 隔离;人工项不参与自动判定。"""
    db = _db()
    holder = {"uid": 1}
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_id] = lambda: holder["uid"]
    with TestClient(app) as client:
        r = client.put("/api/screener/dual-core/checklist", json={
            "trade_date": "2026-10-27",
            "items": {"industry_lead": True, "stock_weekly": False},
            "review": {"q1": "否", "q2": "是", "q3": "追高了一次"},
        })
        assert r.status_code == 200
        assert r.json()["trade_date"] == "2026-10-27"

        got = client.get("/api/screener/dual-core/checklist",
                         params={"trade_date": "2026-10-27"}).json()
        assert got["items"] == {"industry_lead": True, "stock_weekly": False}
        assert got["review"]["q3"] == "追高了一次"

        # 整份覆盖:空提交会清空
        client.put("/api/screener/dual-core/checklist",
                   json={"trade_date": "2026-10-27", "items": {}, "review": {}})
        got = client.get("/api/screener/dual-core/checklist",
                         params={"trade_date": "2026-10-27"}).json()
        assert got["items"] == {} and got["review"] == {}

        # 另一个用户同一天互不影响
        holder["uid"] = 2
        got2 = client.get("/api/screener/dual-core/checklist",
                          params={"trade_date": "2026-10-27"}).json()
        assert got2["items"] == {}

    assert db.query(TradeChecklist).count() == 1
    db.close()


def test_checklist_rejects_bad_date():
    db = _db()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_id] = lambda: 1
    with TestClient(app) as client:
        resp = client.get("/api/screener/dual-core/checklist",
                          params={"trade_date": "2026/10/27"})
    assert resp.status_code == 422
    db.close()


def test_dual_core_endpoint_no_data():
    db = _db()
    from app.db import PortfolioBase
    from app.portfolio import dual_core_checklist_models
    PortfolioBase.metadata.create_all(db.get_bind())
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_id] = lambda: 1
    with TestClient(app) as client:
        resp = client.get("/api/screener/dual-core")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert body["verdict"]["level"] == "无数据"
    db.close()


def test_task_sync_dual_core_upsert(monkeypatch):
    """任务落库:首跑全量插入、重跑只更新最近 5 根,pct_chg 由收盘推导。"""
    from app import task_runner

    db = _db()
    days = _weekdays(40)
    df = pd.DataFrame({
        "trade_date": days,
        "open": [100.0 + i for i in range(40)],
        "high": [101.0 + i for i in range(40)],
        "low": [99.0 + i for i in range(40)],
        "close": [100.5 + i for i in range(40)],
        "amount": [1e9] * 40,
        "name": ["测试指数"] * 40,
    })
    monkeypatch.setattr(task_runner.dg, "get_ths_index_daily",
                        lambda code, bars=1200, use_cache=True: df.copy())
    first = task_runner.task_sync_dual_core(db)
    assert "errors" not in first
    assert first["dual_core"]["883957"]["added"] == 40
    assert db.query(DualCoreDaily).count() == 80   # 两个核
    last = db.query(DualCoreDaily).filter_by(code="883957").order_by(
        DualCoreDaily.trade_date.desc()).first()
    assert last.pct_chg != 0.0

    second = task_runner.task_sync_dual_core(db)
    assert second["dual_core"]["883957"]["added"] == 0
    assert second["dual_core"]["883957"]["updated"] == 5
    assert db.query(DualCoreDaily).count() == 80
    db.close()


def test_task_sync_dual_core_isolates_single_failure(monkeypatch):
    from app import task_runner

    db = _db()

    def _boom(code, bars=1200, use_cache=True):
        if code == "883994":
            raise RuntimeError("ths down")
        days = _weekdays(35)
        return pd.DataFrame({
            "trade_date": days,
            "open": [10.0] * 35, "high": [11.0] * 35,
            "low": [9.0] * 35, "close": [10.5] * 35,
            "amount": [1e7] * 35, "name": ["测试"] * 35,
        })

    monkeypatch.setattr(task_runner.dg, "get_ths_index_daily", _boom)
    result = task_runner.task_sync_dual_core(db)
    assert "883957" in result["dual_core"]
    assert "883994" in result["errors"]
    assert db.query(DualCoreDaily).filter_by(code="883994").count() == 0
    db.close()
