"""核心逻辑单元测试(纯函数,不依赖外部数据源)。

覆盖:G03 止损引擎、SC-U08 拥挤度偏离、底分型识别、PM02 滞回、新闻规则。
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.portfolio.holding_service import (  # noqa: E402
    breakeven_price, _stage_b_reached, _trail_stop,
)
from app.portfolio.models import Holding  # noqa: E402
from app.screener.industry_monitor import compute_ratio_score  # noqa: E402
from app.screener.market_regime import _apply_hysteresis, _decide_cycle_stage  # noqa: E402
from app.screener.models import MarketState  # noqa: E402
from app.screener.technical_signal import _find_bottom_fractals  # noqa: E402
from app.screener.news_monitor import _match_patterns, BEAR_PATTERNS, BULL_PATTERNS  # noqa: E402


# ---------- G03 止损 ----------

def test_breakeven_includes_fees():
    be = breakeven_price(100.0)
    # 保本价必须高于买入价(摩擦成本)
    assert be > 100.0
    # 手算:100×(1+0.00025+0.00001)/(1−0.00025−0.0005) ≈ 100.086
    assert 100.05 < be < 100.12


def _mk_holding(**kw) -> Holding:
    defaults = dict(code="600519", buy_price=100.0, quantity=100,
                    breakeven_price=100.09, initial_stop_price=90.0,
                    stop_price=90.0, stop_stage="A", highest_price=100.0,
                    risk_r=10.0, trail_param=8.0)
    defaults.update(kw)
    h = Holding(**defaults)
    return h


def test_stage_b_trigger_by_1r():
    h = _mk_holding()
    assert not _stage_b_reached(102.0, h)   # < 1R(110) 且 < 保本价+3%(103.09)
    assert _stage_b_reached(105.0, h)       # ≥ 保本价+3% → 触发
    assert _stage_b_reached(110.0, h)       # ≥ 1R → 触发


def test_trail_stop_never_below_breakeven_inputs():
    h = _mk_holding(stop_stage="C", highest_price=120.0, stop_price=100.09,
                    breakeven_price=100.09)
    # 百分比跟踪:120×(1−8%)=110.4
    trail = _trail_stop(h, atr=None)
    assert abs(trail - 110.4) < 1e-6
    # ATR 吊灯取高者:120−2×2=116
    trail2 = _trail_stop(h, atr=2.0)
    assert abs(trail2 - 116.0) < 1e-6


# ---------- SC-U08 拥挤度 ----------

def test_ratio_score_flat_series_is_zero():
    s = pd.Series([5.0] * 130)
    scores = compute_ratio_score(s)
    assert scores.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_ratio_score_spike_positive():
    s = pd.Series([5.0] * 130)
    s.iloc[-1] = 25.0  # 偏离均值4倍 → score ≈ 400
    score = compute_ratio_score(s).iloc[-1]
    assert score > 300  # 触发 SC-U08 回避阈值


# ---------- 底分型 ----------

def test_bottom_fractal_detected():
    lows = pd.Series([10.0, 9.5, 9.0, 9.4, 10.0])  # 第3根最低
    assert 2 in _find_bottom_fractals(lows)


def test_no_fractal_in_downtrend():
    lows = pd.Series([10.0, 9.0, 8.0, 7.0, 6.0])
    assert _find_bottom_fractals(lows) == []


# ---------- PM02 滞回 ----------

def test_downgrade_immediate():
    prev = MarketState(trade_date=date(2026, 1, 1), cycle_stage="衰退",
                       position_range="80%-100%", created_at=None)
    stage, rng, pending, cnt = _apply_hysteresis(prev, "过热", "0%-30%", date(2026, 1, 20))
    assert stage == "过热" and rng == "0%-30%" and cnt == 0


def test_upgrade_needs_confirmation_and_dwell():
    prev = MarketState(trade_date=date(2026, 1, 1), cycle_stage="过热",
                       position_range="0%-30%", stage_pending="复苏",
                       pending_count=1)
    # 未达驻留期 → 保持旧档
    stage, rng, pending, cnt = _apply_hysteresis(prev, "复苏", "50%-70%", date(2026, 1, 15))
    assert stage == "过热" and pending == "复苏" and cnt == 2


def test_cycle_stage_mapping():
    # 经济强+流动性差 → 下行
    assert _decide_cycle_stage(True, False, False, 50)[0] == "下行"
    # 经济强+流动性强+估值上行+分位>80 → 过热 0-10
    assert _decide_cycle_stage(True, True, True, 85) == ("过热", "0%-10%")
    # 经济低迷+流动性弱 → 衰退
    assert _decide_cycle_stage(False, False, False, 20)[0] == "衰退"


# ---------- 新闻规则 ----------

def test_news_patterns():
    assert "业绩预增" in _match_patterns("公司发布业绩预告:净利润预增120%", BULL_PATTERNS)
    assert "减持解禁" in _match_patterns("控股股东计划减持2%股份", BEAR_PATTERNS)
    assert _match_patterns("今天天气不错", BULL_PATTERNS) == []
