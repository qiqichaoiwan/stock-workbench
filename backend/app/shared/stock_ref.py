"""共享只读引用:Portfolio 上下文读取行情的唯一入口(只读,不写 Screener 表)。

隔离原则:portfolio 模块禁止直接 import screener 内部服务;
需要行情/基础信息时统一走本模块的只读查询。
"""
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..shared import data_gateway as dg
from ..screener.models import DailyQuote, PoolPerformance, StockBasic, StockPool


def latest_quote_date(db: Session) -> date | None:
    cutoff = dg.completed_trade_day()
    row = db.execute(select(DailyQuote.trade_date).where(
        DailyQuote.trade_date <= cutoff).order_by(DailyQuote.trade_date.desc()).limit(1)).first()
    return row[0] if row else None


def latest_close(db: Session, code: str, as_of: date | None = None) -> tuple[date, float] | None:
    """该股截至 as_of(默认已完成交易日)的最新收盘,回填/巡检不偷看未来。"""
    cutoff = as_of or dg.completed_trade_day()
    row = db.execute(select(DailyQuote.trade_date, DailyQuote.close).where(
        DailyQuote.code == code, DailyQuote.trade_date <= cutoff).order_by(DailyQuote.trade_date.desc()).limit(1)).first()
    return (row[0], float(row[1])) if row else None


def quote_history(db: Session, code: str, days: int = 60,
                  as_of: date | None = None) -> list[dict]:
    """近 N 个交易日日线;as_of 截止 completed_trade_day,禁止偷看未来行情。"""
    cutoff = as_of or dg.completed_trade_day()
    rows = db.execute(
        select(DailyQuote.trade_date, DailyQuote.open, DailyQuote.high,
               DailyQuote.low, DailyQuote.close, DailyQuote.volume)
        .where(DailyQuote.code == code,
               DailyQuote.trade_date <= cutoff,
               DailyQuote.trade_date >= cutoff - timedelta(days=days * 2))
        .order_by(DailyQuote.trade_date)).all()
    return [{"trade_date": r[0], "open": r[1], "high": r[2], "low": r[3],
             "close": r[4], "volume": r[5]} for r in rows]


def max_high_since(db: Session, code: str, start: date,
                   as_of: date | None = None) -> float | None:
    """建仓日(含)以来的最高价;as_of 截止,禁止偷看未来行情。"""
    cutoff = as_of or dg.completed_trade_day()
    v = db.execute(select(func.max(DailyQuote.high)).where(
        DailyQuote.code == code,
        DailyQuote.trade_date >= start,
        DailyQuote.trade_date <= cutoff)).scalar()
    return float(v) if v is not None else None


def atr14(db: Session, code: str, as_of: date | None = None) -> float | None:
    """Wilder ATR(14),用于 G03 吊灯止损;as_of 截止,禁止偷看未来。"""
    cutoff = as_of or dg.completed_trade_day()
    rows = db.execute(
        select(DailyQuote.trade_date, DailyQuote.high, DailyQuote.low, DailyQuote.close)
        .where(DailyQuote.code == code, DailyQuote.trade_date <= cutoff)
        .order_by(DailyQuote.trade_date.desc()).limit(30)
    ).all()
    if len(rows) < 16:
        return None
    data = list(reversed(rows))
    trs = []
    for i in range(1, len(data)):
        _, h, l, c = data[i]
        _, ph, pl, pc = data[i - 1]
        trs.append(max(float(h) - float(l), abs(float(h) - float(pc)), abs(float(l) - float(pc))))
    if not trs:
        return None
    atr = sum(trs[:14]) / 14
    for tr in trs[14:]:
        atr = (atr * 13 + tr) / 14
    return round(atr, 4)


def stock_exists(db: Session, code: str) -> bool:
    """代码是否真实存在:查基础表或行情表。

    部分代码(如北交所/新上市)只落了行情未落 StockBasic,故两表都可作为依据;
    纯占位代码(999999)两表皆无 → False。
    """
    if db.execute(select(StockBasic.code).where(
            StockBasic.code == code).limit(1)).first() is not None:
        return True
    return db.execute(select(DailyQuote.code).where(
        DailyQuote.code == code).limit(1)).first() is not None


def stock_name(db: Session, code: str) -> str:
    row = db.execute(select(StockBasic.name).where(StockBasic.code == code)).first()
    return row[0] if row else ""


def pool_rating(db: Session, code: str) -> dict | None:
    """该股票在总池/赛道池的最新评级(只读引用)。"""
    row = db.execute(select(StockPool).where(
        StockPool.code == code, StockPool.status == "in")
        .order_by(StockPool.id.desc())).scalars().first()
    if row is None:
        return None
    return {"pool_type": row.pool_type, "rating": row.rating, "score": row.score,
            "in_price": row.in_price, "trade_date": row.trade_date.isoformat()}


def pool_perf_summary(db: Session) -> dict:
    """池内整体表现摘要(只读)。"""
    from datetime import datetime

    rows = db.execute(select(PoolPerformance).order_by(
        PoolPerformance.trade_date.desc()).limit(500)).scalars().all()
    if not rows:
        return {}
    latest_date = max(r.trade_date for r in rows)
    latest = [r for r in rows if r.trade_date == latest_date]
    import statistics
    cums = [r.cum_pct for r in latest]
    excess = [r.cum_pct - r.benchmark_pct for r in latest]
    return {"trade_date": latest_date.isoformat(), "count": len(latest),
            "avg_cum_pct": round(statistics.mean(cums), 2) if cums else 0,
            "avg_excess_pct": round(statistics.mean(excess), 2) if excess else 0}
