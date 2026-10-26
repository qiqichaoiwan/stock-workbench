"""第二轮审计修复回归:P0 prune 降级守卫 + 赛道池出池滞回/停用过滤/降级入池。

全部内存库 + monkeypatch mock 网关,不联网。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import PortfolioBase, ScreenerBase  # noqa: E402
from app.screener import universe_manager as um  # noqa: E402
from app.screener.models import (StockBasic, StockPool, Track,  # noqa: E402
                                 TrackMembership)
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


@pytest.fixture()
def db():
    eng = create_engine("sqlite://")
    ScreenerBase.metadata.create_all(eng)
    PortfolioBase.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(Track(id=1, name="半导体", concept_board="中芯概念", industry_index="半导体",
                keywords="半导体,晶圆", enabled=True))
    for code, name in [("688981", "中芯国际"), ("603986", "兆易创新")]:
        s.add(StockBasic(code=code, name=name, industry="半导体"))
    s.commit()
    yield s
    s.close()


def _mk_members(db, codes, source="concept_board_local"):
    for c in codes:
        db.add(TrackMembership(track_id=1, stock_code=c, source=source))
    db.commit()


# ---------- P0: prune 降级源守卫 ----------

def test_prune_rejects_degraded_source(db, monkeypatch):
    _mk_members(db, ["688981", "603986"])
    monkeypatch.setattr(
        um.dg, "get_concept_board_members_with_source",
        lambda board, track=None, db=None: (
            pd.DataFrame([{"code": "THS:308690", "name": board}]), "ths"))
    t = db.get(Track, 1)
    with pytest.raises(um.DegradedSourceError):
        um.prune_track_members(db, t)
    n = db.query(TrackMembership).count()
    assert n == 2  # 一行未删


def test_prune_cleans_on_eastmoney(db, monkeypatch):
    _mk_members(db, ["688981", "603986"])  # 603986 假装过期
    db.add(TrackMembership(track_id=1, stock_code="300750", source="manual"))
    db.commit()
    monkeypatch.setattr(
        um.dg, "get_concept_board_members_with_source",
        lambda board, track=None, db=None: (
            pd.DataFrame([{"code": "688981", "name": "中芯国际"}]), "eastmoney"))
    removed = um.prune_track_members(db, db.get(Track, 1))
    assert removed == 1
    left = {m.stock_code for m in db.query(TrackMembership).all()}
    assert left == {"688981", "300750"}  # 过期删,manual 保留


# ---------- 赛道池策略行为(出池滞回/停用过滤/降级入池) ----------

def _mk_pool_row(db, track_id, code, status="in"):
    db.add(StockPool(trade_date=__import__("datetime").date(2026, 10, 23),
                     pool_type="track", track_id=track_id, code=code, name=code,
                     in_price=10.0, score=61.0, rating="hold", status=status))
    db.commit()


def test_track_exit_hysteresis_keeps_incumbent_below_line(db, monkeypatch):
    """弱市 score_min 抬高:在池存量低于线只停新增,不再全清(出池滞回)。"""
    import datetime

    from app.screener import strategy_engine as se
    _mk_pool_row(db, 1, "600001")
    # market 弱势 → score_min = 60 + 10 = 70,存量 61 分应保留。
    # run_strategy 内部是函数内 import,须 patch 源模块 market_regime。
    monkeypatch.setattr("app.screener.market_regime.risk_guard_active",
                        lambda d: True)
    monkeypatch.setattr("app.screener.market_regime.latest_state",
                        lambda d: None)
    monkeypatch.setattr(se, "build_candidates", lambda d, td=None: pd.DataFrame([
        {"code": "600001", "name": "测试主板股", "industry": "半导体",
         "is_st": False, "board": "主板", "close": 50.0, "total_mv": 300.0,
         "list_date": None, "roe": 5.0, "profit_yoy": 10.0,
         "ocf_to_profit": float("nan"), "debt_ratio": float("nan"),
         "gross_margin": 30.0,
         "turnover": 3.0, "mom20": 5.0, "crowd_avoid": False,
         "roe_ind_pct": 100.0, "gm_ind_pct": 100.0, "pe_pct_5y": float("nan"),
         "pe_ttm": float("nan"), "tracks": None, "buy_setup": False,
         "rs_weak": False, "rs_strong": False, "pricing_power": None,
         "ma60": float("nan"), "ma120": float("nan")},
    ]))
    monkeypatch.setattr(se, "track_news_heat", lambda d: {})
    monkeypatch.setattr(se, "latest_signals", lambda d, **kw: [])
    out = se.run_strategy(db, datetime.date(2026, 10, 23))
    row = db.query(StockPool).filter_by(code="600001").one()
    assert row.status == "in"  # 滞回:低分存量不出池
    assert out["outs"] == 0


def test_track_disabled_exits_pool(db, monkeypatch):
    """停用赛道:存量全部出池(留档),不再产生新入选。"""
    import datetime

    from app.screener import strategy_engine as se
    db.get(Track, 1).enabled = False
    db.commit()
    _mk_pool_row(db, 1, "600001")
    # df 非空才能走到赛道循环(空 df 会提前 return error)
    monkeypatch.setattr(se, "build_candidates", lambda d, td=None: pd.DataFrame([
        {"code": "600001", "name": "测试主板股", "industry": "半导体",
         "is_st": False, "board": "主板", "close": 50.0, "total_mv": 300.0,
         "list_date": None, "roe": 5.0, "profit_yoy": 10.0,
         "ocf_to_profit": float("nan"), "debt_ratio": float("nan"),
         "gross_margin": 30.0,
         "turnover": 3.0, "mom20": 5.0, "crowd_avoid": False,
         "roe_ind_pct": 100.0, "gm_ind_pct": 100.0, "pe_pct_5y": float("nan"),
         "pe_ttm": float("nan"), "tracks": None, "buy_setup": False,
         "rs_weak": False, "rs_strong": False, "pricing_power": None,
         "ma60": float("nan"), "ma120": float("nan")},
    ]))
    monkeypatch.setattr(se, "track_news_heat", lambda d: {})
    out = se.run_strategy(db, datetime.date(2026, 10, 23))
    row = db.query(StockPool).filter_by(code="600001", pool_type="track").one()
    assert row.status == "out"
    assert row.out_reason == "赛道已停用"


def test_star_market_incumbent_forced_out(db, monkeypatch):
    """科创板已入池存量:每次策略运行都置出,释放名额并留档。"""
    import datetime

    from app.screener import strategy_engine as se
    _mk_pool_row(db, 1, "688981")
    monkeypatch.setattr("app.screener.market_regime.risk_guard_active",
                        lambda d: False)
    monkeypatch.setattr("app.screener.market_regime.latest_state",
                        lambda d: None)
    monkeypatch.setattr(se, "build_candidates", lambda d, td=None: pd.DataFrame([
        {"code": "600001", "name": "测试主板股", "industry": "半导体",
         "is_st": False, "board": "主板", "close": 50.0, "total_mv": 300.0,
         "list_date": None, "roe": 5.0, "profit_yoy": 10.0,
         "ocf_to_profit": float("nan"), "debt_ratio": float("nan"),
         "gross_margin": 30.0, "turnover": 3.0, "mom20": 5.0,
         "crowd_avoid": False, "roe_ind_pct": 100.0, "gm_ind_pct": 100.0,
         "pe_pct_5y": float("nan"), "pe_ttm": float("nan"), "tracks": None,
         "buy_setup": False, "rs_weak": False, "rs_strong": False,
         "pricing_power": None, "ma60": float("nan"), "ma120": float("nan")},
    ]))
    monkeypatch.setattr(se, "track_news_heat", lambda d: {})
    monkeypatch.setattr(se, "latest_signals", lambda d, **kw: [])
    out = se.run_strategy(db, datetime.date(2026, 10, 23))
    row = db.query(StockPool).filter_by(code="688981").one()
    assert row.status == "out"
    assert row.out_reason == "科创板不参与选股"
    assert out["outs"] >= 1


def test_hard_filter_track_mode_allows_neg_profit():
    """track_mode + allow_sell_to_hold:业绩转负/缺失不再被 hard_filter 拦截
    (由 _rating 判 sell 后策略层降级 hold),参数语义兑现。"""
    import datetime

    from app.screener.strategy_engine import PARAM_DEFAULTS, hard_filter

    df = pd.DataFrame([
        {"code": "A", "is_st": False, "board": "主板", "close": 10.0,
         "total_mv": 0.0, "list_date": None, "roe": 5.0, "profit_yoy": -30.0,
         "ocf_to_profit": float("nan"), "debt_ratio": float("nan"),
         "gross_margin": 30.0,
         "turnover": 3.0, "mom20": 5.0, "crowd_avoid": False,
         "roe_ind_pct": float("nan"), "ma60": float("nan"), "ma120": float("nan")},
    ])
    params = dict(PARAM_DEFAULTS)
    # 总池口径:负增长被拦
    main = hard_filter(df, params, as_of=datetime.date(2026, 10, 23))
    assert main.empty
    # 赛道口径(默认 track_allow_sell_to_hold=1):放行
    track = hard_filter(df, params, as_of=datetime.date(2026, 10, 23),
                        track_mode=True)
    assert list(track["code"]) == ["A"]
