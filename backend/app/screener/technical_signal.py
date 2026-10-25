"""技术信号引擎(相对口径):底分型、连红、量能偏离、个股相对强弱。

- 底分型+连红3-4天+量能放大 = 会议纪要的入场信号组合
- 相对强弱:个股 vs 所属行业指数 vs 基准指数;行业涨而个股跌 = 弱势(rs_weak)
- 量能用「相对自身MA20的偏离度」,遵守相对口径红线
"""
import json
import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..shared import data_gateway as dg
from .models import DailyQuote, StockBasic, TechnicalSignal

log = logging.getLogger("technical_signal")

RED_STREAK_MIN = 3
VOLUME_SURGE_PCT = 50.0   # 量能偏离自身MA20超过50%
FRACTAL_WINDOW = 5        # 底分型需在近N日内(含确认延迟)
HISTORY_DAYS = 60         # 计算窗口


def _find_bottom_fractals(low: pd.Series) -> list[int]:
    """返回底分型中间K线的下标(low[i] 低于左右两侧);确认需 i+1 存在。"""
    lows = low.tolist()
    return [i for i in range(1, len(lows) - 1)
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]]


def scan_signals(db: Session, trade_date: date | None = None) -> dict:
    """对候选域全量扫描,写入当日信号。返回统计。"""
    trade_date = trade_date or dg.prev_trade_day()
    start_d = trade_date - timedelta(days=HISTORY_DAYS * 2)

    codes = {c for (c,) in db.execute(select(DailyQuote.code).where(
        DailyQuote.trade_date == trade_date))}
    if not codes:
        log.warning("no quotes for %s", trade_date)
        return {"scanned": 0}

    rows = db.execute(
        select(DailyQuote.code, DailyQuote.trade_date, DailyQuote.close,
               DailyQuote.high, DailyQuote.low, DailyQuote.volume, DailyQuote.pct_chg)
        .where(DailyQuote.code.in_(codes), DailyQuote.trade_date >= start_d)
        .order_by(DailyQuote.code, DailyQuote.trade_date)
    ).all()
    df = pd.DataFrame(rows, columns=["code", "trade_date", "close", "high", "low", "volume", "pct_chg"])

    # 行业与基准 20 日动量(相对强弱参照)
    ind_rows = db.execute(select(StockBasic.code, StockBasic.industry)).all()
    industry_of = {c: i for c, i in ind_rows if i}
    from .models import IndustryDaily as ID
    ind_mom: dict[str, float] = {}
    for name, mom in db.execute(select(ID.industry_name, ID.mom_20).where(
            ID.trade_date == trade_date)).all():
        ind_mom[name] = float(mom or 0)

    bench_mom = 0.0
    try:
        bench = dg.get_index_daily("sh000985",
                                   (trade_date - timedelta(days=90)).strftime("%Y%m%d"),
                                   trade_date.strftime("%Y%m%d"))
        if len(bench) > 21:
            bench_mom = float(bench["close"].iloc[-1] / bench["close"].iloc[-21] - 1) * 100
    except Exception as exc:  # noqa: BLE001
        log.warning("benchmark for rs failed: %s", exc)

    db.execute(TechnicalSignal.__table__.delete().where(
        TechnicalSignal.trade_date == trade_date))

    stats = {"scanned": len(codes), "signals": 0}
    pending: list[TechnicalSignal] = []

    for code, g in df.groupby("code"):
        if len(g) < 25:
            continue
        close = g["close"].astype(float).reset_index(drop=True)
        low = g["low"].astype(float).reset_index(drop=True)
        volume = g["volume"].astype(float).reset_index(drop=True)
        name = industry_of.get(code, "")

        # 连红(连续上涨天数,截至最后一天)
        red_streak = 0
        pct = g["pct_chg"].astype(float).tolist()
        for v in reversed(pct):
            if v > 0:
                red_streak += 1
            else:
                break

        # 底分型:近 FRACTAL_WINDOW 日内出现(分型确认在 i+1,取倒数第二根为界)
        fractals = _find_bottom_fractals(low)
        last_idx = len(low) - 1
        fractal_recent = any(last_idx - i <= FRACTAL_WINDOW for i in fractals)

        # 量能偏离度(相对自身 MA20)
        vol_ma20 = volume.tail(20).mean()
        vol_dev = (volume.iloc[-1] / vol_ma20 - 1) * 100 if vol_ma20 else 0.0

        # 相对强弱:个股20日动量 vs 行业 vs 基准
        mom20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 21 else 0.0
        ind_m = ind_mom.get(name)
        rs_detail = {"stock_mom20": round(mom20, 2), "industry": name,
                     "industry_mom20": ind_m, "benchmark_mom20": round(bench_mom, 2)}

        def emit(sig_type: str, strength: float, detail: dict):
            pending.append(TechnicalSignal(trade_date=trade_date, code=code,
                                           signal_type=sig_type, strength=round(strength, 2),
                                           detail=json.dumps(detail, ensure_ascii=False)))

        if red_streak >= RED_STREAK_MIN:
            emit("red_streak", red_streak, {"streak": red_streak})
        if fractal_recent:
            emit("bottom_fractal", 1.0, {"window": FRACTAL_WINDOW})
        if vol_dev > VOLUME_SURGE_PCT:
            emit("volume_surge", vol_dev, {"dev_pct": round(vol_dev, 2)})
        if ind_m is not None:
            if mom20 > ind_m and ind_m > bench_mom:
                emit("rs_strong", mom20 - bench_mom, rs_detail)
            elif ind_m > 0 and mom20 < 0:
                emit("rs_weak", mom20 - ind_m, rs_detail)
        # 组合入场信号:底分型 + 连红 + 量能
        if fractal_recent and red_streak >= RED_STREAK_MIN and vol_dev > VOLUME_SURGE_PCT:
            emit("buy_setup", red_streak + vol_dev / 100, {
                **rs_detail, "streak": red_streak, "vol_dev": round(vol_dev, 2),
                "note": "底分型+连红+量能放大(参考会议纪要口径)",
            })

    db.add_all(pending)
    db.commit()
    stats["signals"] = len(pending)
    log.info("signals @%s: %s", trade_date, stats)
    return stats


def latest_signals(db: Session, trade_date: date | None = None,
                   signal_type: str | None = None, limit: int = 200) -> list[TechnicalSignal]:
    q = select(TechnicalSignal).order_by(TechnicalSignal.id.desc()).limit(limit)
    if trade_date is not None:
        q = q.where(TechnicalSignal.trade_date == trade_date)
    if signal_type:
        q = q.where(TechnicalSignal.signal_type == signal_type)
    return list(db.execute(q).scalars())
