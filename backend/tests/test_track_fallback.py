"""赛道成分替代降级回归测试(内存 sqlite,不碰生产库,不联网)。

覆盖:
1. ths 占位行展开为种子成分,落库 source=concept_board_ths。
2. 无 concept_board 的 track 走本地展开(seed+industry+keywords 并集非空)。
3. with_source 四源皆失败时本地展开仍可用 keywords 命中。
4. 策略赛道降级 reasons 标记携带 track.member_source。
"""
import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import ScreenerBase  # noqa: E402
from app.screener import universe_manager  # noqa: E402
from app.screener.models import StockBasic, StockPool, Track, TrackMembership  # noqa: E402


def _fresh_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    ScreenerBase.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add_basic(db, code, name="测试股", industry=""):
    db.add(StockBasic(code=code, name=name, industry=industry, board="主板",
                      is_st=False, total_mv=300.0, circ_mv=200.0))
    db.commit()


def test_ths_marker_expands_to_seeds(monkeypatch):
    db = _fresh_session()
    for code in ("300750", "300014"):
        _add_basic(db, code, name=f"电池股{code}", industry="电池")
    db.add(Track(name="固态电池", concept_board="固态电池", industry_index="电池",
                 keywords="固态电池,硫化物电解质"))
    db.commit()
    track = db.execute(select(Track).where(Track.name == "固态电池")).scalar_one()
    marker = pd.DataFrame([{"code": "THS:308294", "name": "固态电池",
                            "ths_code": "308294"}])
    monkeypatch.setattr(
        universe_manager.dg, "get_concept_board_members_with_source",
        lambda board, track=None, db=None: (marker, "ths"))
    n = universe_manager.sync_track_members(db, track)
    codes = {c for (c,) in db.execute(
        select(TrackMembership.stock_code).where(TrackMembership.track_id == track.id))}
    assert {"300750", "300014"} <= codes
    assert "THS:308294" not in codes
    srcs = {s for (s,) in db.execute(
        select(TrackMembership.source).where(TrackMembership.track_id == track.id))}
    assert srcs == {"concept_board_ths"}
    assert n >= 2
    db.refresh(track)
    assert track.member_source == "local_ths_verified"


def test_local_expand_without_concept_board():
    db = _fresh_session()
    _add_basic(db, "002463", name="沪电股份", industry="元件")
    _add_basic(db, "300308", name="中际旭创", industry="通信设备")
    db.add(Track(name="PCB", concept_board="", industry_index="元件",
                 keywords="PCB,印制电路板,覆铜板"))
    db.commit()
    track = db.execute(select(Track).where(Track.name == "PCB")).scalar_one()
    n = universe_manager.sync_track_members(db, track)
    codes = {c for (c,) in db.execute(
        select(TrackMembership.stock_code).where(TrackMembership.track_id == track.id))}
    assert "002463" in codes  # industry_index 等值命中
    assert n >= 1
    db.refresh(track)
    assert track.member_source == "local"


def test_local_expand_on_gateway_failure(monkeypatch):
    db = _fresh_session()
    for i in range(80):
        _add_basic(db, f"{600000 + i}", name=f"半导体测试{i}", industry="半导体")
    db.add(Track(name="半导体", concept_board="中芯概念", industry_index="半导体",
                 keywords="半导体,晶圆,光刻,国产替代"))
    db.commit()
    track = db.execute(select(Track).where(Track.name == "半导体")).scalar_one()

    def _boom(board, track=None):
        raise RuntimeError("concept_board 四源均失败")

    monkeypatch.setattr(
        universe_manager.dg, "get_concept_board_members_with_source", _boom)
    n = universe_manager.sync_track_members(db, track)
    count = db.execute(select(TrackMembership).where(
        TrackMembership.track_id == track.id)).scalars().all()
    assert len(count) >= 1
    assert n >= 1
    db.refresh(track)
    assert track.member_source == "local"


def test_reasons_fallback_carries_source():
    from app.screener import strategy_engine
    from app.screener.models import DailyQuote, FinancialIndicator

    db = _fresh_session()
    trade_d = date(2026, 10, 23)
    db.add(StockBasic(code="600001", name="测试半导体", industry="半导体",
                      board="主板", is_st=False, total_mv=300.0, circ_mv=200.0))
    db.add(DailyQuote(code="600001", trade_date=trade_d, open=20.0, high=21.0,
                      low=19.5, close=20.0, turnover=5.0, amount=1e8, pct_chg=1.0))
    db.add(FinancialIndicator(code="600001", period="20260630", ann_date=trade_d,
                              roe=10.0, gross_margin=30.0, net_margin=15.0,
                              revenue_yoy=20.0, profit_yoy=20.0,
                              ocf_to_profit=1.0, debt_ratio=40.0))
    db.add(Track(name="半导体", concept_board="中芯概念", industry_index="半导体",
                 keywords="半导体,晶圆", member_source="concept_board_ths"))
    db.commit()
    with patch.object(strategy_engine.dg, "get_index_daily",
                       side_effect=RuntimeError("offline")):
        res = strategy_engine.run_strategy(db, trade_date=trade_d)
    assert "error" not in res
    rows = db.execute(select(StockPool).where(
        StockPool.pool_type == "track", StockPool.status == "in")).scalars().all()
    assert rows, "fallback 下赛道池不应空转"
    reasons = [r for p in rows for r in json.loads(p.reasons or "[]")]
    assert any("concept_board_ths" in r for r in reasons), reasons
