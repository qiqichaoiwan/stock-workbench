"""行业截面元信息:数据日/覆盖度/陈旧判定(降级快照不冒充真实值)。"""
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import ScreenerBase, get_db
from app.screener import api, industry_monitor
from app.screener.models import IndexDaily, IndustryDaily
from app.shared.security import current_user_id


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    ScreenerBase.metadata.create_all(engine)
    return Session(engine)


def _row(code, name, trade_date, *, mom20=0.0, mom60=0.0, flow=0.0,
         ratio_score=0.0, pe=0.0, avoid=None):
    return IndustryDaily(
        industry_code=code, industry_name=name, trade_date=trade_date,
        close=100.0, pct_chg=1.0, amount=1e9, market_amount=1e12, ratio=0.1,
        ratio_score=ratio_score, mom_20=mom20, mom_60=mom60, fund_flow=flow,
        flow_streak=0, pe_pct=pe, avoid_until=avoid)


def test_industry_summary_empty():
    db = _db()
    meta = industry_monitor.industry_summary(db)
    assert meta["as_of"] == ""
    assert meta["rows"] == 0
    assert meta["heat_mode"] == "empty"
    assert meta["coverage"] == {"momentum": False, "flow": False,
                                "crowding": False, "valuation": False}
    db.close()


def test_industry_summary_degraded_and_stale():
    """降级快照(动量/资金/拥挤度全 0)→ 覆盖度为 false、heat 标 degraded;
    数据日落后于指数日线数据日 → stale(提示去跑 industry_daily)。"""
    db = _db()
    d26 = date(2026, 10, 26)
    db.add_all([_row("new_blhy", "玻璃行业", d26), _row("new_jrhy", "金融行业", d26)])
    db.add(IndustryDaily(industry_code="new_blhy", industry_name="玻璃行业",
                         trade_date=date(2026, 10, 23), close=99.0, pct_chg=-1.0,
                         amount=1e9, market_amount=1e12, ratio=0.1))
    db.add(IndexDaily(code="sh000001", name="上证指数", trade_date=date(2026, 10, 27),
                      close=3648.3, pct_chg=-1.33, amount=6.7e11, source="tx"))
    db.commit()

    meta = industry_monitor.industry_summary(db)
    assert meta["as_of"] == "2026-10-26"
    assert meta["expected"] == "2026-10-27"
    assert meta["stale"] is True
    assert meta["rows"] == 2
    assert meta["history_days"] == 2
    assert meta["heat_mode"] == "degraded"
    assert meta["coverage"]["momentum"] is False
    assert meta["coverage"]["flow"] is False
    db.close()


def test_industry_summary_full_coverage():
    db = _db()
    d27 = date(2026, 10, 27)
    db.add_all([
        _row("b1", "行业A", d27, mom20=3.0, mom60=8.0, flow=2.5,
             ratio_score=120.0, pe=0.3),
        _row("b2", "行业B", d27, mom20=-1.0, mom60=2.0, flow=-1.0,
             ratio_score=-40.0, pe=0.7, avoid=d27),
    ])
    db.add(IndexDaily(code="sh000001", name="上证指数", trade_date=d27,
                      close=3648.3, pct_chg=-1.33, amount=6.7e11, source="tx"))
    db.commit()

    meta = industry_monitor.industry_summary(db)
    assert meta["stale"] is False
    assert meta["coverage"] == {"momentum": True, "flow": True,
                                "crowding": True, "valuation": True}
    assert meta["heat_mode"] == "ok"
    assert meta["avoid_count"] == 1
    db.close()


def test_industries_summary_endpoint_returns_meta():
    """接口层:空库也返回 200 + 完整 meta 字段(前端据 coverage 渲染「积累中」)。"""
    db = _db()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_id] = lambda: 1
    with TestClient(app) as client:
        resp = client.get("/api/screener/industries/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == ""
    assert body["heat_mode"] == "empty"
    assert set(body["coverage"]) == {"momentum", "flow", "crowding", "valuation"}
    db.close()
