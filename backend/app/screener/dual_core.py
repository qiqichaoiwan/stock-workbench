"""双核共振·主升浪狙击战法(多周期升级版)判定引擎。

策略文档口径(周线定格局 / 日线定发令枪 / 5日线定买卖点,大周期保护小周期):
- 一票否决:883994(昨日打首板表现)日线未创历史新高 → 今日禁止打板接力,强制空仓。
- 日线发令枪:883957(同花顺全A)现价 > 昨日最高价,且 883994 盘中 ≥ 历史最高点。
- 周线土壤:883957 站稳 10/20 周均线;883994 周线多头排列且未出现高位巨阴
  (跌破 5 周线 + 周线跌幅 ≤ -5%)。
- 5日线共振:双核均在 5 日均线之上且 5 日均线拐头向上。
- 退军线:883957 现价跌破昨日最低价 → 尾盘无条件清仓。
- 周期降维:883957 周线跌破 10 周线但日线突破昨高 → 判为诱多,仓位强制减半。

红线:
- 周线由日线聚合(W-FRI),不单独落库,避免两处口径漂移。
- 历史最高 = 不含当日的历史 high 最大值;当天"盘中触及"用 high 判定,
  "收盘新高"用 close 判定,两者分开返回,不混为一个结论。
- 历史不足(周线 < 21 根 / 日线 < 6 根)时对应判定返回 None(不明文当 False),
  前端显示「历史不足」而不是伪造的"不满足"。
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import DualCoreDaily, IndustryDaily

log = logging.getLogger("dual_core")

CORE_A = "883957"   # 同花顺全A(沪深京):定大方向/发令枪
CORE_B = "883994"   # 昨日打首板表现:情绪一票否决

# 文档里的仓位档位(百分比上限);周线不配合时强制减半(见 _position)
CAP_ATTACK = 100
CAP_PROBE = 40
CAP_DEFEND = 20


def _round(v, digits: int = 3):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, digits)


def _load_daily(db: Session, code: str) -> pd.DataFrame:
    rows = db.execute(select(
        DualCoreDaily.trade_date, DualCoreDaily.open, DualCoreDaily.high,
        DualCoreDaily.low, DualCoreDaily.close, DualCoreDaily.amount,
    ).where(DualCoreDaily.code == code).order_by(DualCoreDaily.trade_date)).all()
    df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "amount"])
    for col in ("open", "high", "low", "close", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"]).reset_index(drop=True)


def weekly_bars(df: pd.DataFrame) -> pd.DataFrame:
    """日线聚合成周线(W-FRI 标签,最后一根为当周未完成周)。"""
    if df.empty:
        return df
    idx = pd.to_datetime(df["trade_date"])
    agg = df.set_index(idx).resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"})
    return (agg.dropna(subset=["close"]).reset_index()
            .rename(columns={"trade_date": "week", "index": "week"}))


def _weekly_macd(weekly: pd.DataFrame) -> tuple[float | None, float | None, bool | None]:
    """周线 MACD(12,26,9);返回 (dif, dea, 红柱是否缩短)。"""
    if len(weekly) < 35:
        return None, None, None
    ema12 = weekly["close"].ewm(span=12, adjust=False).mean()
    ema26 = weekly["close"].ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2
    shrink = bool(hist.iloc[-1] < hist.iloc[-2])
    return _round(dif.iloc[-1]), _round(dea.iloc[-1]), shrink


def _core_snapshot(df: pd.DataFrame) -> dict:
    """单核多周期快照:日线(现价/昨高/昨低/5日线)+ 周线(5/10/20 周线/MACD)。"""
    if len(df) < 2:
        return {"bars": len(df), "ready": False}
    last, prev = df.iloc[-1], df.iloc[-2]
    closes = df["close"]
    ma5 = closes.rolling(5).mean()
    ma5_now = _round(ma5.iloc[-1])
    ma5_prev = _round(ma5.iloc[-2]) if len(closes) >= 6 else None
    prior_high = _round(df["high"].iloc[:-1].max())          # 历史最高(不含当日)
    prior_close_high = _round(closes.iloc[:-1].max())
    prev_high = _round(prev["high"])                          # 昨日最高价
    weekly = weekly_bars(df)
    w_close = _round(weekly["close"].iloc[-1]) if len(weekly) else None
    wma5 = _round(weekly["close"].rolling(5).mean().iloc[-1]) if len(weekly) >= 5 else None
    wma10 = _round(weekly["close"].rolling(10).mean().iloc[-1]) if len(weekly) >= 10 else None
    wma20 = _round(weekly["close"].rolling(20).mean().iloc[-1]) if len(weekly) >= 20 else None
    w_pct = (_round((weekly["close"].iloc[-1] / weekly["close"].iloc[-2] - 1) * 100, 2)
             if len(weekly) >= 2 else None)
    dif, dea, shrink = _weekly_macd(weekly)
    return {
        "bars": int(len(df)),
        "weekly_bars": int(len(weekly)),
        "ready": True,
        "trade_date": str(last["trade_date"]),
        "close": _round(last["close"]),
        "high": _round(last["high"]),
        "low": _round(last["low"]),
        "pct_chg": _round((last["close"] / prev["close"] - 1) * 100, 2),
        "amount": _round(last["amount"], 0),
        "prev_high": prev_high,
        "prev_low": _round(prev["low"]),
        "ath_high": prior_high,                      # 不含当日的历史最高
        "new_high_intraday": bool(_round(last["high"]) >= prior_high) if prior_high else None,
        "new_high_close": bool(_round(last["close"]) >= prior_close_high) if prior_close_high else None,
        "ma5": ma5_now,
        "ma5_prev": ma5_prev,
        "ma5_up": bool(ma5_now > ma5_prev) if ma5_now is not None and ma5_prev is not None else None,
        "above_ma5": bool(_round(last["close"]) > ma5_now) if ma5_now else None,
        "w_close": w_close, "wma5": wma5, "wma10": wma10, "wma20": wma20,
        "w_pct": w_pct,
        "w_above_ma5": bool(w_close > wma5) if wma5 else None,
        "w_above_ma10": bool(w_close > wma10) if wma10 else None,
        "w_above_ma20": bool(w_close > wma20) if wma20 else None,
        "w_bull": bool(wma5 > wma10 > wma20) if wma5 and wma10 and wma20 else None,
        "w_macd_dif": dif, "w_macd_dea": dea, "w_macd_hist_shrink": shrink,
    }


def _check(step: int, key: str, label: str, *, auto: bool, passed,
           value: str = "", hint: str = "", veto: bool = False) -> dict:
    return {"step": step, "key": key, "label": label, "auto": auto,
            "pass": passed, "value": value, "hint": hint, "veto": veto}


def _position(verdict_level: str, weekly_ok: bool) -> tuple[int, str]:
    cap = {"进攻": CAP_ATTACK, "轻仓试错": CAP_PROBE,
           "防守": CAP_DEFEND, "空仓": 0}[verdict_level]
    if cap and not weekly_ok:
        cap = int(cap / 2)
    return cap, f"0%-{cap}%" if cap else "0%(空仓)"


def _industry_top(db: Session) -> tuple[str, list[dict]]:
    latest = db.execute(select(func.max(IndustryDaily.trade_date))).scalar_one_or_none()
    if latest is None:
        return "", []
    rows = db.execute(select(IndustryDaily.industry_name, IndustryDaily.pct_chg,
                             IndustryDaily.amount).where(
        IndustryDaily.trade_date == latest).order_by(
        IndustryDaily.pct_chg.desc()).limit(3)).all()
    return str(latest), [{"name": r[0], "pct_chg": _round(r[1], 2),
                          "amount": _round(r[2], 0)} for r in rows]


def analyze(db: Session) -> dict:
    """计算双核共振当日判定与逐项检查(文档二的四步清单)。"""
    a_df, b_df = _load_daily(db, CORE_A), _load_daily(db, CORE_B)
    if a_df.empty or b_df.empty:
        return {"as_of": "", "ready": False, "data": {},
                "stale": False, "stale_cores": [],
                "verdict": {"level": "无数据", "cap": 0, "position_text": "0%", "reasons": []},
                "checks": [], "divergence": {"notes": []}, "industry_top": [],
                "series": {}, "message": "双核指数尚未落库,请先在任务中心运行「dual_core」"}
    a, b = _core_snapshot(a_df), _core_snapshot(b_df)
    as_of = max(str(a.get("trade_date") or ""), str(b.get("trade_date") or ""))

    a_break_prev_high = (a["close"] > a["prev_high"]) if a.get("prev_high") else None
    b_new_high = b.get("new_high_intraday")            # 盘中 ≥ 历史最高 = 文档"历史最高点"
    ma5_ok = (bool(a.get("above_ma5")) and bool(b.get("above_ma5"))
              and a.get("ma5_up") is not False and b.get("ma5_up") is not False) \
        if a.get("above_ma5") is not None and b.get("above_ma5") is not None else None
    weekly_a_ok = (a.get("w_above_ma10") and a.get("w_above_ma20")) \
        if a.get("w_above_ma10") is not None else None
    weekly_b_ok = (b.get("w_bull") if b.get("w_bull") is not None
                   else b.get("w_above_ma5"))
    weekly_ok = bool(weekly_a_ok) and bool(weekly_b_ok)
    b_weekly_bear = (b.get("w_above_ma5") is False and (b.get("w_pct") or 0) <= -5)
    retreat = (a["close"] < a["prev_low"]) if a.get("prev_low") else None
    dimension_down = bool(a.get("w_above_ma10") is False and a_break_prev_high)

    # ---- 数据同步性:双核必须同一交易日,否则判定基于过期数据(不强制改结论) ----
    stale_cores = []
    if a.get("trade_date") and a.get("trade_date") != as_of:
        stale_cores.append(f"{CORE_A} 最新 {a.get('trade_date')}")
    if b.get("trade_date") and b.get("trade_date") != as_of:
        stale_cores.append(f"{CORE_B} 最新 {b.get('trade_date')}")

    # ---- 结论(一票否决优先,其次退军线,再次日线/5日线组合) ----
    reasons: list[str] = []
    veto = False
    if stale_cores:
        reasons.append(f"数据不同步({'、'.join(stale_cores)},业务日 {as_of}),请先运行任务 dual_core 对齐双核数据")
    if b_new_high is False:
        level, veto = "空仓", True
        reasons.append(f"绝对底线:{CORE_B} 今日未创历史新高(现价 {b['close']} / 历史高 {b['ath_high']}),"
                       "禁止打板接力,管住手等待发令枪")
    elif b_weekly_bear:
        level, veto = "空仓", True
        reasons.append(f"{CORE_B} 周线跌破 5 周线且周跌幅 {b.get('w_pct')}%(严重亏钱效应),"
                       "周线级别一票否决")
    elif retreat:
        level = "空仓"
        reasons.append(f"退军线触发:{CORE_A} 现价 {a['close']} 跌破昨日最低 {a['prev_low']},"
                       "尾盘无条件清仓,不留恋")
    elif a_break_prev_high and b_new_high:
        if ma5_ok is False:
            level = "轻仓试错"
            reasons.append(f"日线双新高已触发,但 5 日线未共振(5 日线走平/向下),按冲突规则轻仓试错")
        elif weekly_ok:
            level = "进攻"
            reasons.append("周线土壤 + 日线双新高 + 5 日线共振,扣动扳机")
        else:
            level = "进攻"
            reasons.append("日线双新高 + 5 日线共振;周线未完全配合,仓位强制减半")
    elif a_break_prev_high and b_new_high is None:
        level = "防守"
        reasons.append(f"{CORE_A} 已突破昨高,但 {CORE_B} 历史不足,无法确认双核共振")
    else:
        level = "防守"
        reasons.append(f"{CORE_A} 未突破昨日最高 {a['prev_high']}(现价 {a['close']}),发令枪未响,不主动做多")
    if dimension_down and level != "空仓":
        reasons.append("周期降维:全A 周线已跌破 10 周线而日线强行突破,判为诱多,仓位减半")
    cap, pos_text = _position(level, weekly_ok)

    # ---- 背离 ----
    top_div = bool(b_new_high and b.get("w_macd_hist_shrink") is True)
    bottom_div = bool(b_new_high is False and b.get("w_above_ma20") is False
                      and b.get("ma5_up") is True)
    div_notes = []
    if top_div:
        div_notes.append(f"顶背离预警:{CORE_B} 日线创历史新高但周线 MACD 红柱缩短,"
                         "次日若情绪不佳坚决不接力高位连板,只做低位首板/中军")
    if bottom_div:
        div_notes.append(f"底背离试错:{CORE_B} 未创新高且周线在 20 周线下,仅 5 日线拐头,"
                         "仍不符合开仓条件,绝不左侧抄底")
    if dimension_down:
        div_notes.append("周期降维打击:全A 周线跌破关键支撑而日线突破昨高,判为诱多,仓位减半")

    # ---- 四步清单(文档二) ----
    industry_date, industry_top = _industry_top(db)
    industry_hint = ("当日行业涨幅前三:" + "、".join(
        f"{r['name']} {r['pct_chg']}%" for r in industry_top)) if industry_top else "行业截面暂无数据"
    checks = [
        _check(1, "core_b_new_high",
               f"【大前提】{CORE_B}(昨日打板表现)是否创出历史新高?(否 → 今日禁止打板接力)",
               auto=True, passed=b_new_high, veto=True,
               value=f"现价 {b['close']} / 历史高 {b['ath_high']}"
                     f"({'已创' if b_new_high else '未创' if b_new_high is False else '历史不足'})",
               hint="日线级别绝对底线,未创新高则无论周线多好一律空仓"),
        _check(1, "core_a_break_prev_high",
               f"{CORE_A}(同花顺全A)是否突破昨日最高价?",
               auto=True, passed=a_break_prev_high,
               value=f"现价 {a['close']} / 昨高 {a['prev_high']}",
               hint="日线发令枪:现价 > 昨日最高价"),
        _check(1, "weekly_bull",
               "周线定调:883957 与 883994 周线是否处于多头(未破 5周/10周均线)?",
               auto=True, passed=weekly_ok,
               value=f"全A: {a.get('w_close')} vs 10周 {a.get('wma10')} / 20周 {a.get('wma20')};"
                     f" 情绪: 5周 {b.get('wma5')} / 10周 {b.get('wma10')}",
               hint="周线定格局:全A 站上 10/20 周线,情绪指数周线多头排列且无高位巨阴"),
        _check(1, "ma5_resonance",
               "5日线共振:两指数是否均在 5 日均线之上且 5 日线拐头向上?",
               auto=True, passed=ma5_ok,
               value=f"全A: {a['close']} vs MA5 {a.get('ma5')}; 情绪: {b['close']} vs MA5 {b.get('ma5')}",
               hint="5 日线定买卖点;双核跌破 5 日线且反抽无力则确认短期转弱"),
        _check(2, "industry_lead",
               "行业:所选行业周线是否向上?日线是否排名前三且放量?",
               auto=False, passed=None, value=industry_hint,
               hint="周线多头 + 日线放量突破前高,只做领涨主线,放弃冷门反弹"),
        _check(2, "stock_weekly",
               "个股:所选个股周线是否在 30 周线上?日线是否一阳穿多线/箱体突破?",
               auto=False, passed=None,
               hint="周线站上 30 周线且周 MACD 金叉;日线一阳穿多线、N 字反包"),
        _check(2, "stock_ma5",
               "微观:个股 5 日线是否向上,回踩是否不破?(日线突破但 5 日线向下 → 剔除)",
               auto=False, passed=None,
               hint="龙头看日线突破,中军看 5 日线支撑"),
        _check(3, "entry_path",
               "开仓路径:激进扫板(日线双新高+主线龙一/龙二) / 稳健中军(14:45 后沿 5 日线买入)",
               auto=False, passed=None,
               hint="二选一,写进复盘;稳健者一律 14:45 后尾盘执行"),
        _check(3, "position_cap",
               f"仓位上限(系统建议 {pos_text};周线不配合强制减半)",
               auto=False, passed=None, value=pos_text,
               hint="浮盈加仓、滚动操作,绝不向下摊平"),
        _check(4, "hold_ma5",
               "持仓股是否跌破 5 日线?(破 → 减仓一半;未破 → 持有)",
               auto=False, passed=None,
               hint="收盘前 10 分钟检查,优先级 5 日线 > 日线"),
        _check(4, "retreat_line",
               f"退军线:{CORE_A} 现价是否仍在昨日最低价之上?(跌破 → 尾盘无条件全清)",
               auto=True, passed=None if retreat is None else not retreat,
               value=f"现价 {a['close']} / 昨低 {a['prev_low']}",
               hint="触及绝对防守线忽略周线支撑,机械化清仓"),
        _check(4, "divergence",
               "背离检查:是否无顶背离(创历史新高但周线 MACD 红柱缩短)?",
               auto=True,
               passed=None if b.get("w_macd_hist_shrink") is None else not top_div,
               value=("顶背离" if top_div else
                      "历史不足" if b.get("w_macd_hist_shrink") is None else "无顶背离"),
               hint="883994 日线新高 + 周线 MACD 红柱缩短 = 顶背离预警,次日冲高止盈不格局"),
    ]

    series = {
        CORE_A: [{"date": str(d), "close": _round(c)}
                 for d, c in zip(a_df["trade_date"].tail(60), a_df["close"].tail(60))],
        CORE_B: [{"date": str(d), "close": _round(c)}
                 for d, c in zip(b_df["trade_date"].tail(60), b_df["close"].tail(60))],
    }

    return {
        "as_of": as_of,
        "ready": True,
        "source": "ths",
        "stale": bool(stale_cores),
        "stale_cores": stale_cores,
        "data": {CORE_A: a, CORE_B: b},
        "verdict": {
            "level": level, "cap": cap, "position_text": pos_text, "veto": veto,
            "weekly_ok": weekly_ok, "daily_ok": bool(a_break_prev_high and b_new_high),
            "ma5_ok": ma5_ok, "reasons": reasons,
        },
        "divergence": {"top": top_div, "bottom": bottom_div,
                       "dimension_down": dimension_down, "notes": div_notes},
        "checks": checks,
        "industry_date": industry_date,
        "industry_top": industry_top,
        "series": series,
    }
