"""行情读取接口回归：内存 SQLite，区分空记录和数据库故障。"""
import logging
from datetime import date, datetime

import pandas as pd
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import ScreenerBase, get_db
from app.screener import api
from app.screener.models import FundFlowSnapshot, IndexDaily, MarketStat
from app.shared.security import current_user_id
from app import task_runner


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


ENDPOINTS = [
    ("/indexes", {}, api.indexes, IndexDaily, [], "指数数据"),
    ("/fund-flow", {}, api.fund_flow, FundFlowSnapshot, [], "板块资金流"),
    ("/fund-flow/history", {"board_name": "测试板块"}, api.fund_flow_history,
     FundFlowSnapshot, [], "板块资金流历史"),
    ("/market-stat/latest", {}, api.market_stat_latest, MarketStat, {}, "大盘统计"),
]


@pytest.mark.parametrize("path,params,handler,model,empty,label", ENDPOINTS)
def test_empty_records_return_success(client, path, params, handler, model, empty, label):
    response = client.get(f"/api/screener{path}", params=params)
    assert response.status_code == 200
    assert response.json() == empty


@pytest.mark.parametrize("failure", ["missing_table", "query_error"])
@pytest.mark.parametrize("path,params,handler,model,empty,label", ENDPOINTS)
def test_database_failure_returns_503_and_logs(
        db, client, monkeypatch, caplog, failure, path, params, handler, model, empty, label):
    error = OperationalError("SELECT", {}, RuntimeError("database unavailable"))
    if failure == "missing_table":
        model.__table__.drop(db.get_bind())
    else:
        def fail_execute(*args, **kwargs):
            raise error

        monkeypatch.setattr(db, "execute", fail_execute)

    with caplog.at_level(logging.ERROR, logger=api.log.name):
        with pytest.raises(HTTPException) as caught:
            handler(user_id=1, db=db, **params)
        response = client.get(f"/api/screener{path}", params=params)

    assert caught.value.status_code == 503
    assert isinstance(caught.value.__cause__, OperationalError)
    if failure == "query_error":
        assert caught.value.__cause__ is error
    assert response.status_code == 503
    assert label in response.json()["detail"]
    assert "数据库服务暂时不可用" in response.json()["detail"]
    assert "database unavailable" not in response.json()["detail"]
    records = [record for record in caplog.records if record.name == api.log.name]
    assert len(records) == 2
    assert all(record.exc_info for record in records)


def test_indexes_returns_latest_per_code(client, db):
    db.add_all([
        IndexDaily(code="000001", name="上证指数", trade_date=date(2026, 10, 22), close=3000),
        IndexDaily(code="000001", name="上证指数", trade_date=date(2026, 10, 23),
                   close=3100, pct_chg=1.2, amount=123, source="eastmoney"),
        IndexDaily(code="399001", name="深证成指", trade_date=date(2026, 10, 22), close=10000),
    ])
    db.commit()
    response = client.get("/api/screener/indexes", params={"codes": "000001,missing,399001"})
    assert response.status_code == 200
    rows = response.json()
    assert [row["code"] for row in rows] == ["000001", "399001"]
    assert rows[0] == {"code": "000001", "name": "上证指数", "trade_date": "2026-10-23",
                       "close": 3100, "pct_chg": 1.2, "amount": 123, "source": "eastmoney"}


def _flow(board_type, code, name, timestamp, value):
    return FundFlowSnapshot(board_type=board_type, board_code=code, board_name=name,
                            snap_time=datetime.fromisoformat(timestamp), net_inflow=value,
                            source="eastmoney")


def test_fund_flow_latest_snapshot_isolated_by_category_and_sorted(client, db):
    db.add_all([
        _flow("industry", "A", "旧板块", "2026-10-23T10:00:00", 999),
        _flow("industry", "A", "板块A", "2026-10-23T14:00:00", -2),
        _flow("industry", "B", "板块B", "2026-10-23T14:00:00", 5),
        _flow("concept", "A", "概念A", "2026-10-23T15:00:00", 100),
    ])
    db.commit()
    response = client.get("/api/screener/fund-flow")
    assert response.status_code == 200
    assert [row["board_code"] for row in response.json()] == ["B", "A"]
    assert all(row["snap_time"] == "2026-10-23T14:00:00" for row in response.json())
    assert all(row["source"] == "eastmoney" for row in response.json())
    limited = client.get("/api/screener/fund-flow", params={"limit": 1})
    assert [row["board_code"] for row in limited.json()] == ["B"]
    concept = client.get("/api/screener/fund-flow", params={"board_type": "concept"})
    assert [row["board_name"] for row in concept.json()] == ["概念A"]


@pytest.mark.parametrize("identity", [{"board_code": "A", "board_name": "不应按此名称查询"},
                                    {"board_name": "同名板块"}])
def test_history_daily_last_snapshot_and_category_isolation(client, db, identity):
    db.add_all([
        _flow("industry", "A", "同名板块", "2026-10-21T15:00:00", 1),
        _flow("industry", "A", "同名板块", "2026-10-22T10:00:00", 2),
        _flow("industry", "A", "同名板块", "2026-10-22T15:00:00", 3),
        _flow("industry", "A", "同名板块", "2026-10-23T10:00:00", 4),
        _flow("industry", "A", "同名板块", "2026-10-23T14:00:00", 5),
        _flow("concept", "A", "同名板块", "2026-10-22T15:00:00", 100),
        _flow("concept", "A", "同名板块", "2026-10-23T15:00:00", 200),
        _flow("industry", "B", "其他板块", "2026-10-23T14:00:00", 300),
        _flow("industry", "B", "其他板块", "2026-10-23T15:00:00", 400),
    ])
    db.commit()
    response = client.get("/api/screener/fund-flow/history", params={**identity, "days": 2})
    assert response.status_code == 200
    assert response.json() == [{"snap_date": "2026-10-22", "net_inflow": 3},
                               {"snap_date": "2026-10-23", "net_inflow": 5}]


def test_history_code_survives_rename_and_ignores_same_name_other_code(client, db):
    db.add_all([
        _flow("industry", "A", "旧名称", "2026-10-22T15:00:00", 1),
        _flow("industry", "A", "新名称", "2026-10-23T14:00:00", 2),
        _flow("industry", "B", "新名称", "2026-10-23T15:00:00", 100),
    ])
    db.commit()
    response = client.get("/api/screener/fund-flow/history",
                          params={"board_code": "A", "board_name": "新名称"})
    assert response.json() == [{"snap_date": "2026-10-22", "net_inflow": 1},
                               {"snap_date": "2026-10-23", "net_inflow": 2}]


def test_market_stat_returns_latest(client, db):
    db.add_all([
        MarketStat(snap_time=datetime(2026, 10, 23, 10), advance=1),
        MarketStat(snap_time=datetime(2026, 10, 23, 15), advance=3000, decline=2000,
                   limit_up=60, limit_down=10, amount_total=10000,
                   source="eastmoney", source_note="测试口径"),
    ])
    db.commit()
    response = client.get("/api/screener/market-stat/latest")
    assert response.status_code == 200
    assert response.json() == {"snap_time": "2026-10-23T15:00:00", "advance": 3000,
                               "decline": 2000, "limit_up": 60, "limit_down": 10,
                               "amount_total": 10000, "source": "eastmoney",
                               "source_note": "测试口径"}


@pytest.mark.parametrize("path,params", [
    ("/fund-flow", {"board_type": "invalid"}),
    ("/fund-flow/history", {"board_type": "invalid", "board_name": "测试"}),
    ("/fund-flow/history", {}),
])
def test_invalid_fund_flow_parameters_remain_400(client, path, params):
    assert client.get(f"/api/screener{path}", params=params).status_code == 400


def test_snapshot_persists_eastmoney_for_both_categories(db, monkeypatch):
    timestamp = datetime(2026, 10, 23, 14, 30)
    monkeypatch.setattr(task_runner.dg, "now_tz", lambda: timestamp)
    frame = pd.DataFrame([{"board_code": "A", "board_name": "同名板块", "fund_flow": 3.5}])
    monkeypatch.setattr(task_runner.dg, "get_industry_fund_flow", lambda: frame.copy())
    monkeypatch.setattr(task_runner.dg, "get_concept_fund_flow", lambda: frame.copy())
    result = task_runner.task_fund_flow_snapshot(db)
    assert result["rows"] == 2
    assert "errors" not in result
    rows = db.scalars(select(FundFlowSnapshot)).all()
    assert len(rows) == 2
    assert {row.board_type for row in rows} == {"industry", "concept"}
    assert all(row.source == "eastmoney" and row.net_inflow == 3.5 for row in rows)
