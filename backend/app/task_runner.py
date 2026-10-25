"""任务运行器:把各采集/计算任务包装为可日志记录、可手动触发的单元。

定时与手动触发共用同一入口 _log_wrap,失败写 task_log 便于后台排查。
同一任务带互斥锁(重入直接拒绝),后台线程执行供 API 手动触发使用,
避免慢任务(如行业重试、历史回填)阻塞请求线程造成"页面卡死"。
"""
import logging
import threading
import time
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import SessionLocal
from .portfolio.models import User
from .portfolio.report_service import weekly_report
from .screener import industry_monitor, market_regime, news_monitor, strategy_engine, \
    technical_signal, universe_manager
from .screener.models import (DailyQuote, FinancialIndicator, IntradaySnapshot,
                              StockBasic, TaskLog, Valuation)
from .shared import data_gateway as dg

log = logging.getLogger("tasks")

# 任务互斥:同名任务运行中时拒绝再次触发(定时/手动共用)
_task_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _get_lock(name: str) -> threading.Lock:
    with _locks_guard:
        return _task_locks.setdefault(name, threading.Lock())


def cleanup_zombie_tasks() -> int:
    """启动时把上次进程中断遗留的 running 记录标记为失败。"""
    db = SessionLocal()
    try:
        n = (db.execute(update(TaskLog)
                        .where(TaskLog.status == "running")
                        .values(status="failed", detail="进程重启中断"))
             .rowcount)
        db.commit()
        if n:
            log.warning("cleaned %d zombie running task rows", n)
        return n or 0
    finally:
        db.close()


def run_task_async(name: str, **kwargs) -> bool:
    """后台线程执行任务;已在运行则返回 False。调用方立即返回响应。

    这里只做非阻塞探测(探测即释放),真正互斥由 run_task 内部锁保证;
    极端并发下第二个线程会在 run_task 处被拒并写失败日志。
    """
    lock = _get_lock(name)
    if not lock.acquire(blocking=False):
        return False
    lock.release()

    def _worker():
        try:
            run_task(name, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("async task %s rejected/failed: %s", name, exc)

    threading.Thread(target=_worker, name=f"task-{name}", daemon=True).start()
    return True


def recent_periods(n: int = 8) -> list[str]:
    """近 n 个报告期(YYYYMMDD):1231/0930/0630/0331。"""
    periods: list[str] = []
    y = date.today().year
    while len(periods) < n:
        for md in ("1231", "0930", "0630", "0331"):
            p = f"{y}{md}"
            if p < date.today().strftime("%Y%m%d"):
                periods.append(p)
            if len(periods) == n:
                break
        y -= 1
    return sorted(periods)


def _log_wrap(name: str, fn, **kwargs):
    db = SessionLocal()
    t = TaskLog(task_name=name, status="running")
    db.add(t)
    db.commit()
    started = time.monotonic()
    try:
        result = fn(db, **kwargs) or {}
        t.status, t.detail = "success", str(result)[:2000]
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("task %s failed", name)
        t.status, t.detail = "failed", f"{exc}"[:2000]
        raise
    finally:
        t.duration_ms = int((time.monotonic() - started) * 1000)
        db.commit()
        db.close()


def run_task(name: str, **kwargs):
    if name not in TASKS:
        raise KeyError(name)
    lock = _get_lock(name)
    # 调度线程/后台线程内已持有锁时直接执行(run_task_async 先拿锁);
    # 同名任务并发触发时拒绝,防止双重回填/双重采集
    if not lock.acquire(blocking=False):
        raise RuntimeError(f"任务 {name} 正在运行中,请稍候")
    try:
        return _log_wrap(name, TASKS[name], **kwargs)
    finally:
        lock.release()


def task_sync_basic(db: Session):
    return {"stocks": universe_manager.sync_stock_basic(db)}


def task_sync_members(db: Session):
    return {"indexes": universe_manager.sync_all_indexes(db)}


def task_sync_tracks(db: Session):
    universe_manager.ensure_default_tracks(db)
    out, errors = {}, []
    for t in universe_manager.list_tracks(db):
        try:
            out[t.name] = universe_manager.sync_track_members(db, t)
        except Exception as exc:  # noqa: BLE001 单赛道失败不拖垮整体
            errors.append(f"{t.name}: {exc}")
    return {"tracks": out, "errors": errors}


def task_sync_quotes_from_spot(db: Session):
    """收盘后:用全市场快照一次性生成当日日线行(候选域)。"""
    snap = dg.get_spot_snapshot()
    codes = set(universe_manager.candidate_universe(db))
    trade_date = dg.prev_trade_day()
    df = snap[snap["code"].isin(codes)].dropna(subset=["price"])
    rows = 0
    for _, r in df.iterrows():
        exists = db.execute(select(DailyQuote).where(
            DailyQuote.code == r["code"], DailyQuote.trade_date == trade_date)).scalar_one_or_none()
        vals = {
            "open": _f(r.get("open")), "high": _f(r.get("high")),
            "low": _f(r.get("low")), "close": _f(r["price"]),
            "amount": _f(r.get("amount")), "turnover": _f(r.get("turnover")),
            "pct_chg": _f(r.get("pct_chg")), "total_mv": _f(r.get("total_mv")),
        }
        if exists is None:
            db.add(DailyQuote(code=r["code"], trade_date=trade_date, **vals))
        else:
            for k, v in vals.items():
                setattr(exists, k, v)
        rows += 1
    db.commit()
    return {"date": trade_date.isoformat(), "quotes": rows}


def task_intraday_snapshot(db: Session):
    """盘中半小时快照(候选域)。"""
    snap = dg.get_spot_snapshot()
    codes = set(universe_manager.candidate_universe(db))
    ts = dg.now_tz().replace(tzinfo=None)
    n = 0
    for _, r in snap[snap["code"].isin(codes)].dropna(subset=["price"]).iterrows():
        db.add(IntradaySnapshot(code=r["code"], snap_time=ts,
                                open=_f(r.get("open")), high=_f(r.get("high")),
                                low=_f(r.get("low")), price=_f(r["price"]),
                                amount=_f(r.get("amount"))))
        n += 1
    db.commit()
    return {"snapshots": n, "time": ts.isoformat()}


def _f(val, default: float = 0.0) -> float:
    """NaN/None → default(SQLite 中 NaN 会写为 NULL,违反非空约束)。"""
    if val is None or (isinstance(val, float) and val != val):  # NaN check
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def task_sync_financials(db: Session):
    """近8个报告期业绩报表批量同步(point-in-time 公告日)。"""
    codes = set(universe_manager.candidate_universe(db))
    total = 0
    for period in recent_periods(8):
        try:
            df = dg.get_earnings_by_period(period)
        except Exception as exc:  # noqa: BLE001
            log.warning("yjbb %s failed: %s", period, exc)
            continue
        df = df[df["code"].isin(codes)]
        for _, r in df.iterrows():
            exists = db.execute(select(FinancialIndicator).where(
                FinancialIndicator.code == r["code"],
                FinancialIndicator.period == period)).scalar_one_or_none()
            ocf = r.get("ocf_to_profit")
            fields = {
                "ann_date": r.get("ann_date"), "roe": _f(r.get("roe")),
                "gross_margin": _f(r.get("gross_margin")),
                "revenue_yoy": _f(r.get("revenue_yoy")),
                "profit_yoy": _f(r.get("profit_yoy")),
                "ocf_to_profit": _f(ocf) if ocf is not None else 0.0,
            }
            if exists is None:
                db.add(FinancialIndicator(code=r["code"], period=period, **fields))
            else:
                for k, v in fields.items():
                    setattr(exists, k, v)
            if r.get("industry"):
                basic = db.execute(select(StockBasic).where(
                    StockBasic.code == r["code"])).scalar_one_or_none()
                if basic and not basic.industry:
                    basic.industry = str(r["industry"])
            total += 1
    db.commit()
    # 资产负债率(同源 datacenter 批量接口)
    for period in recent_periods(2):
        try:
            zcfz = dg.get_balance_sheet_by_period(period)
        except Exception as exc:  # noqa: BLE001
            log.warning("zcfz %s failed: %s", period, exc)
            continue
        zcfz = zcfz[zcfz["code"].isin(codes)]
        for _, r in zcfz.iterrows():
            fin = db.execute(select(FinancialIndicator).where(
                FinancialIndicator.code == r["code"],
                FinancialIndicator.period == period)).scalar_one_or_none()
            if fin is not None:
                fin.debt_ratio = _f(r.get("debt_ratio"))
    db.commit()
    return {"records": total}


def task_sync_valuation(db: Session):
    """估值快照:快照PE/PB入库 + 相对自身历史分位(随库内历史增长)。"""
    trade_date = dg.prev_trade_day()
    snap = dg.get_spot_snapshot()
    codes = set(universe_manager.candidate_universe(db))
    snap = snap[snap["code"].isin(codes)].dropna(subset=["pe"])
    n = 0
    for _, r in snap.iterrows():
        hist = db.execute(select(Valuation.pe_ttm).where(
            Valuation.code == r["code"], Valuation.trade_date < trade_date,
            Valuation.pe_ttm > 0).order_by(Valuation.trade_date.desc()).limit(1250)).scalars().all()
        pe_series = pd.Series([float(x) for x in hist])
        pe = _f(r["pe"])
        pe_pct = float((pe_series < pe).mean() * 100) if len(pe_series) > 120 else 50.0
        pb = _f(r.get("pb"))
        row = db.execute(select(Valuation).where(
            Valuation.code == r["code"], Valuation.trade_date == trade_date)).scalar_one_or_none()
        if row is None:
            row = Valuation(code=r["code"], trade_date=trade_date)
        row.pe_ttm, row.pb, row.pe_pct_5y = pe, pb, round(pe_pct, 2)
        db.merge(row)
        n += 1
    db.commit()
    return {"valuations": n}


def task_industry_daily(db: Session):
    return {"boards": industry_monitor.sync_industry_daily(db)}


def task_commodities(db: Session):
    return {"series": industry_monitor.sync_commodities(db)}


def task_market_state(db: Session):
    row = market_regime.compute_market_state(db)
    return {"trend": row.trend_label, "stage": row.cycle_stage, "position": row.position_range}


def task_signals(db: Session):
    return technical_signal.scan_signals(db)


def task_strategy(db: Session):
    res = strategy_engine.run_strategy(db)
    if "error" not in res:
        res["perf"] = strategy_engine.update_pool_performance(db)
    return res


def task_news(db: Session):
    return news_monitor.sync_news(db)


def task_weekly_reports(db: Session):
    users = db.execute(select(User).where(User.is_active == True)).scalars().all()  # noqa: E712
    done = []
    for u in users:
        try:
            rec = weekly_report(db, u.id)
            done.append({"user": u.username, "report_id": rec.id})
        except Exception as exc:  # noqa: BLE001
            log.warning("weekly report for %s failed: %s", u.username, exc)
    return {"reports": done}


def task_backfill_history(db: Session, years: int = 5):
    """一次性回填:候选域个股日线(前复权)。仅初始化使用,耗时较长。"""
    codes = universe_manager.candidate_universe(db)
    start = (dg.today() - timedelta(days=years * 365)).strftime("%Y%m%d")
    done = failed = 0
    for i, code in enumerate(codes):
        try:
            df, _src = dg.get_daily_history(code, start)
            if df.empty:
                continue
            existing = {d for (d,) in db.execute(select(DailyQuote.trade_date).where(
                DailyQuote.code == code))}
            for _, r in df.iterrows():
                if r["trade_date"] in existing:
                    continue
                db.add(DailyQuote(code=code, trade_date=r["trade_date"],
                                  open=_f(r["open"]), high=_f(r["high"]),
                                  low=_f(r["low"]), close=_f(r["close"]),
                                  volume=_f(r.get("volume")),
                                  amount=_f(r.get("amount")),
                                  turnover=_f(r.get("turnover")),
                                  pct_chg=_f(r.get("pct_chg"))))
            db.commit()
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("backfill %s failed: %s", code, exc)
        if (i + 1) % 50 == 0:
            log.info("backfill progress %s/%s (failed %s)", i + 1, len(codes), failed)
    return {"codes_done": done, "codes_failed": failed}


TASKS = {
    "sync_basic": task_sync_basic,
    "sync_members": task_sync_members,
    "sync_tracks": task_sync_tracks,
    "sync_quotes": task_sync_quotes_from_spot,
    "intraday_snapshot": task_intraday_snapshot,
    "sync_financials": task_sync_financials,
    "sync_valuation": task_sync_valuation,
    "industry_daily": task_industry_daily,
    "commodities": task_commodities,
    "market_state": task_market_state,
    "signals": task_signals,
    "strategy": task_strategy,
    "news": task_news,
    "weekly_reports": task_weekly_reports,
    "backfill_history": task_backfill_history,
}


def _legacy_run_task_removed():  # noqa: N806 占位防误用
    raise RuntimeError("use run_task() defined above")
