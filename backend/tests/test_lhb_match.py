"""龙虎榜命中游资:买入/卖出方向、同向去重、券商更名归一、历史库存兼容、落库与接口。"""
import json
from datetime import date

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import task_runner
from app.db import ScreenerBase, get_db
from app.screener import api
from app.screener.lhb_match import filter_hot_money, match_seats, normalize_matched
from app.screener.models import LhbDaily
from app.shared import data_gateway as dg
from app.shared.security import current_user_id

SEATS = {
    "中信证券股份有限公司上海分公司": {"trader": "上海超短帮", "style": "超短接力", "risk": "高"},
    "东方财富证券股份有限公司拉萨东环路第二证券营业部": {
        "trader": "散户合力", "style": "跟风接力", "risk": "高"},
    "机构专用": {"trader": "公募/私募机构", "style": "价值波段", "risk": "低"},
    "沪股通专用": {"trader": "北向资金", "style": "外资配置", "risk": "低"},
    "国泰君安证券股份有限公司南京太平南路证券营业部": {
        "trader": "作手新一", "style": "打板龙头", "risk": "高"},
}


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


def test_match_seats_dedupes_same_side_and_skips_unknown():
    got = match_seats(
        ["机构专用", "未知营业部", "东方财富证券股份有限公司拉萨东环路第二证券营业部"],
        ["中信证券股份有限公司上海分公司", "机构专用"],
        SEATS)
    # 同方向重复只留一条;买入/卖出方向各一条,未知席位不产生空标签
    assert [(x["trader"], x["side"]) for x in got] == [
        ("公募/私募机构", "买入"), ("散户合力", "买入"),
        ("上海超短帮", "卖出"), ("公募/私募机构", "卖出"),
    ]
    assert got[1]["seat"].endswith("拉萨东环路第二证券营业部")
    # 机构/北向由 kind 标记,展示层过滤;游资保留
    assert [x["kind"] for x in got] == ["机构", "游资", "游资", "机构"]


def test_filter_hot_money_keeps_only_active_traders():
    entries = [
        {"trader": "上海超短帮", "kind": "游资"},
        {"trader": "公募/私募机构", "kind": "机构"},
        {"trader": "北向资金", "kind": "北向"},
        {"trader": "散户合力"},  # 缺 kind 视为游资
    ]
    assert [x["trader"] for x in filter_hot_money(entries)] == ["上海超短帮", "散户合力"]


def test_match_seats_normalizes_renamed_broker_prefix():
    """国泰君安 2025 年合并为 国泰海通,营业部名称不变,前缀归一后仍能命中。"""
    got = match_seats(["国泰海通证券股份有限公司南京太平南路证券营业部"], None, SEATS)
    assert [(x["trader"], x["side"]) for x in got] == [("作手新一", "买入")]


def test_normalize_matched_handles_legacy_formats():
    raw = json.dumps([
        "北向资金", "北向资金",  # 历史数据里的重复字符串
        {"seat": "机构专用", "trader": "公募/私募机构", "style": "价值波段", "risk": "低"},
        {"seat": "机构专用", "trader": "公募/私募机构"},
        123,
    ])
    assert normalize_matched(raw) == [
        {"seat": "北向资金", "trader": "北向资金", "side": "", "kind": "北向",
         "style": "", "risk": ""},
        {"seat": "机构专用", "trader": "公募/私募机构", "side": "", "kind": "机构",
         "style": "价值波段", "risk": "低"},
    ]
    assert normalize_matched("not-json") == []
    assert normalize_matched(None) == []


def test_lhb_sync_stores_deduped_matches(db, monkeypatch):
    monkeypatch.setattr(task_runner.dg, "load_hot_money_seats", lambda: SEATS)
    monkeypatch.setattr(task_runner.dg, "completed_trade_day",
                        lambda at=None: date(2026, 10, 23))
    frame = pd.DataFrame([{
        "code": "603232", "name": "格尔软件", "trade_date": date(2026, 10, 23),
        "close": 10.0, "pct_chg": 5.0, "lhb_net": 100.0,
        "buy_seats": ["机构专用", "东方财富证券股份有限公司拉萨东环路第二证券营业部"],
        "sell_seats": ["机构专用", "中信证券股份有限公司上海分公司"],
    }])
    monkeypatch.setattr(task_runner.dg, "get_lhb_daily", lambda td: frame)

    assert task_runner.task_lhb_sync(db) == {"date": "2026-10-23", "lhb_rows": 1}
    row = db.execute(select(LhbDaily)).scalar_one()
    matched = json.loads(row.matched_traders)
    assert [(m["trader"], m["side"]) for m in matched] == [
        ("公募/私募机构", "买入"), ("散户合力", "买入"),
        ("公募/私募机构", "卖出"), ("上海超短帮", "卖出"),
    ]
    # 落库保留 kind,展示接口再过滤机构/北向
    assert [m["kind"] for m in matched] == ["机构", "游资", "机构", "游资"]


def test_lhb_api_returns_display_fields(client, db):
    """前端依赖 trade_date 列与对象化 matched_traders,缺失会显示空列/JSON 串。"""
    db.add(LhbDaily(
        code="603232", name="格尔软件", trade_date=date(2026, 10, 27),
        close=10.0, pct_chg=5.0, lhb_net=100.0,
        matched_traders=json.dumps([
            {"seat": "中信证券股份有限公司上海分公司", "trader": "上海超短帮",
             "style": "超短接力", "risk": "高"},
            {"seat": "中信证券股份有限公司上海分公司", "trader": "上海超短帮",
             "style": "超短接力", "risk": "高"},
            {"seat": "机构专用", "trader": "公募/私募机构", "style": "价值波段", "risk": "低"},
        ], ensure_ascii=False)))
    db.commit()

    r = client.get("/api/screener/lhb")
    assert r.status_code == 200
    row = r.json()[0]
    assert row["trade_date"] == "2026-10-27"
    # 去重且过滤机构,只留游资
    assert row["matched_traders"] == [
        {"seat": "中信证券股份有限公司上海分公司", "trader": "上海超短帮", "side": "",
         "kind": "游资", "style": "超短接力", "risk": "高"},
    ]


def test_hot_money_seats_endpoint_filters_institutions(client, monkeypatch):
    monkeypatch.setattr(dg, "load_hot_money_seats", lambda: {
        seat: {**info, "kind": dg.seat_kind(seat, info["trader"])}
        for seat, info in SEATS.items()
    })
    r = client.get("/api/screener/hot-money-seats")
    assert r.status_code == 200
    got = r.json()
    assert {x["trader"] for x in got} == {"上海超短帮", "散户合力", "作手新一"}
    assert all(x["kind"] == "游资" for x in got)


def test_lhb_api_recomputes_matches_with_side(client, db):
    """库存旧数据没有方向;接口按当前映射重算,并应用券商更名归一。"""
    db.add(LhbDaily(
        code="600001", name="测试股", trade_date=date(2026, 10, 26),
        close=10.0, pct_chg=1.0, lhb_net=50.0,
        buy_seats=json.dumps(["国泰海通证券股份有限公司南京太平南路证券营业部",
                              "机构专用"]),
        sell_seats=json.dumps(["机构专用", "沪股通专用"]),
        matched_traders="[]"))  # 库存匹配为空,旧数据完全依赖重算
    db.commit()

    r = client.get("/api/screener/lhb", params={"trade_date": "2026-10-26"})
    assert r.status_code == 200
    got = r.json()[0]["matched_traders"]
    # 机构/北向被过滤,只剩买方游资
    assert [(t["trader"], t["side"], t["kind"]) for t in got] == [("作手新一", "买入", "游资")]
