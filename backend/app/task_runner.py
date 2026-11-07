"""任务运行器:把各采集/计算任务包装为可日志记录、可手动触发的单元。

定时与手动触发共用同一入口 _log_wrap,失败写 task_log 便于后台排查。
同一任务带互斥锁(重入直接拒绝),后台线程执行供 API 手动触发使用,
避免慢任务(如行业重试、历史回填)阻塞请求线程造成"页面卡死"。

并发与多 worker 约束(必读):
- 进程内:同名任务互斥(_task_locks) + 全局并发上限(MAX_CONCURRENT_TASKS,
  默认 3,超限直接 409 拒绝,防止回填拖垮采集)。
- 多进程/多 worker:SQLite 只允许单写进程。本项目默认单容器单 scheduler
  (见 docker-compose deploy.replicas=1);如需横向扩展,必须先迁移到
  MySQL/Postgres 并把 _file_lock 换成 DB advisory 锁,详见 _file_lock 说明。
- 状态语义:success=全部完成;degraded=部分完成(有 errors/codes_failed);
  failed=整体抛错。degraded 不冒充 success,前端任务日志可直接区分。
"""
import inspect
import logging
import json
import os
import threading
import time
from bisect import bisect_right
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .db import SessionLocal
from .portfolio.models import User
from .portfolio.report_service import weekly_report
from .screener import industry_monitor, market_regime, news_monitor, strategy_engine, \
    technical_signal, universe_manager
from .screener.lhb_match import match_seats
from .screener.models import (DailyQuote, FinancialIndicator, FundFlowSnapshot,
                              IntradaySnapshot, LhbDaily, MarketStat, StockBasic,
                              TaskLog, Valuation)
from .shared import data_gateway as dg

log = logging.getLogger("tasks")

# 任务互斥:同名任务运行中时拒绝再次触发(定时/手动共用)
_task_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_pending_tasks: set[str] = set()
# 进程内公平游标；同名任务由 run_task 互斥，重跑从上次尝试后继续。
_sync_quote_cursor: str | None = None
# 全局并发上限:后台任务同时运行数,超限 run_task_async 返回 False(API 转 409)。
# 取值见 config.max_concurrent_tasks(默认 3,环境变量 MAX_CONCURRENT_TASKS 覆盖)。
def _max_concurrent_tasks() -> int:
    try:
        from .config import settings as _settings

        return int(_settings.max_concurrent_tasks)
    except Exception:
        return int(os.environ.get("MAX_CONCURRENT_TASKS", "3"))


MAX_CONCURRENT_TASKS = _max_concurrent_tasks()


@contextmanager
def _file_lock(name: str):
    """跨进程互斥(单机多 worker 场景)。

    当前实现:SQLite 单写进程 + docker-compose replicas=1,进程内锁已足够,
    此处为文档型守卫——同一 data 目录下若意外起第二个进程,第二个直接拒绝,
    避免双写 WAL 冲突。如迁移到 MySQL/Postgres 后需多副本,请把这里换成
    DB advisory 锁(GET_LOCK/pg_advisory_lock)再放开 replicas。
    """
    try:
        import fcntl  # noqa: PLC0415 unix 容器路径使用,Windows 无此模块则降级
    except ImportError:
        # Windows 开发机:退化为进程内锁(生产跑 linux 容器,有 flock 保护)
        yield True
        return

    from .config import ROOT_DIR

    lock_path = ROOT_DIR / "data" / f".task-{name}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        yield False
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


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
    """后台线程执行任务;同名任务在提交时原子占位,避免检查-提交竞态。

    全局并发上限 MAX_CONCURRENT_TASKS:超限直接返回 False(API 转 409),
    防止回填等慢任务拖垮采集流水线。
    """
    with _locks_guard:
        if name in _pending_tasks:
            return False
        limit = _max_concurrent_tasks()
        if len(_pending_tasks) >= limit:
            log.warning("task %s rejected: %d tasks running (max %d)",
                        name, len(_pending_tasks), limit)
            return False
        _pending_tasks.add(name)

    def _worker():
        try:
            run_task(name, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("async task %s rejected/failed: %s", name, exc)
        finally:
            with _locks_guard:
                _pending_tasks.discard(name)

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


TASK_DEGRADED_MARKERS = ("errors", "codes_failed", "failed")


def _is_degraded(result: dict) -> bool:
    """degraded=部分完成:任一标记非空即降级,不冒充 success。

    - error:单点致命错误字符串(如策略引擎返回 {"error": ...})
    - errors:逐项错误列表/字典/字符串(如赛道同步单赛道失败、回填失败明细)
    - codes_failed/failed:计数类(如回填失败数)
    向后兼容:旧调用方只看 success/failed 时,degraded 在 _log_wrap 统一转 failed
    (带 degraded 前缀,日志可区分);新调用方可直接用此函数预判。
    """
    if not isinstance(result, dict):
        return False
    if result.get("error"):
        return True
    for key in TASK_DEGRADED_MARKERS:
        val = result.get(key)
        if isinstance(val, (list, tuple, set)) and len(val) > 0:
            return True
        if isinstance(val, dict) and len(val) > 0:
            return True
        if isinstance(val, str) and val.strip():
            return True
        if isinstance(val, (int, float)) and val > 0:
            return True
    return False


def _progress_writer(task_id: int):
    """慢任务进度写入器:独立会话只更新本任务日志的 detail。

    任务自身可能正在写业务表(回填逐股提交),用独立会话避免把任务的
    未提交事务一起提交/回滚;写失败只记 debug,绝不影响任务本身。
    """
    def _update(msg: str) -> None:
        s = SessionLocal()
        try:
            s.execute(update(TaskLog).where(TaskLog.id == task_id)
                      .values(detail=str(msg)[:2000]))
            s.commit()
        except Exception:  # noqa: BLE001 进度只是展示,失败可忽略
            s.rollback()
            log.debug("task %s progress update failed", task_id, exc_info=True)
        finally:
            s.close()
    return _update


def _log_wrap(name: str, fn, **kwargs):
    db = SessionLocal()
    t = TaskLog(task_name=name, status="running")
    started = time.monotonic()
    try:
        db.add(t)
        db.commit()
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}
        if "progress" in params:
            result = fn(db, progress=_progress_writer(t.id), **kwargs) or {}
        else:
            result = fn(db, **kwargs) or {}
        # degraded(部分完成)绝不冒充 success:统一记 failed,detail 带 degraded 前缀
        if isinstance(result, dict) and _is_degraded(result):
            raise RuntimeError(f"task degraded: {result}")
        t.status, t.detail = "success", str(result)[:2000]
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("task %s failed", name)
        db.rollback()
        t.status, t.detail = "failed", f"{exc}"[:2000]
        try:
            db.add(t)
            db.commit()
        except Exception:
            db.rollback()
            log.exception("task %s failure log persistence failed", name)
        raise
    finally:
        t.duration_ms = int((time.monotonic() - started) * 1000)
        try:
            db.commit()
        except Exception:
            db.rollback()
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
        # 多 worker(多进程)守卫:同一 data 目录下第二个进程直接拒绝,防双写 WAL
        with _file_lock(name) as acquired:
            if not acquired:
                raise RuntimeError(f"任务 {name} 已在另一进程运行中,请稍候")
            return _log_wrap(name, TASKS[name], **kwargs)
    finally:
        lock.release()


def task_sync_basic(db: Session):
    return {"stocks": universe_manager.sync_stock_basic(db)}


def task_sync_members(db: Session):
    return {"indexes": universe_manager.sync_all_indexes(db)}


def task_sync_tracks(db: Session):
    """赛道成分同步:单赛道失败隔离,返回 tracks{name:{added,source}} + errors。"""
    universe_manager.ensure_default_tracks(db)
    out, errors = {}, []
    for t in universe_manager.list_tracks(db):
        try:
            n = universe_manager.sync_track_members(db, t)
            out[t.name] = {"added": n, "source": getattr(t, "member_source", "") or ""}
        except Exception as exc:  # noqa: BLE001 单赛道失败不拖垮整体
            errors.append(f"{t.name}: {exc}")
    return {"tracks": out, "errors": errors}


def task_tracks_backfill(db: Session):
    """一键回填赛道成分:ensure_default_tracks + 逐 track sync,逐项隔离。

    返回 {results: {name: {"added": n, "source": s}}, errors: [...]}
    (与 backfill_all_tracks 一致,供 POST /tracks/backfill 复用)。"""
    return universe_manager.backfill_all_tracks(db)


def task_sync_indexes(db: Session):
    """指数日线落库(INDEX_DISPLAY):取最后一行落库,单指数失败继续。

    窗口取近 90 天:网关 get_index_daily 有 30 行门限(<30 行判失败),
    长窗口保证门限通过,落库仍是最新一行(即近 5 交易日内数据)。
    """
    from datetime import timedelta

    from .screener.models import IndexDaily

    start = (dg.today() - timedelta(days=90)).strftime("%Y%m%d")
    out, errors = {}, {}
    spot_amount: dict[str, float] = {}
    spot_tried = False
    for code, name in dg.INDEX_DISPLAY:
        try:
            df, source = dg.get_index_daily_with_source(code, start)
            if df.empty:
                continue
            last = df.sort_values("trade_date").iloc[-1]
            td = last["trade_date"]
            if hasattr(td, "date"):
                td = td.date()
            close = _f(last.get("close"))
            amount = _f(last.get("amount"))
            # pct_chg:源无此列时由收盘推导
            if "pct_chg" in df.columns:
                pct = _f(last.get("pct_chg"))
            else:
                closes = pd.to_numeric(df.sort_values("trade_date")["close"],
                                       errors="coerce").dropna()
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else 0.0
                pct = ((float(closes.iloc[-1]) / prev - 1) * 100
                       if len(closes) >= 2 and prev else 0.0)
            row = db.execute(select(IndexDaily).where(
                IndexDaily.code == code, IndexDaily.trade_date == td)
            ).scalar_one_or_none()
            if amount <= 0 and row is not None and _f(row.amount) > 0:
                # 腾讯/新浪备源没有成交额;同日重跑时保留已有东财真实值
                amount = _f(row.amount)
            elif amount <= 0 and td == dg.today():
                # 当日走备源:用指数快照「成交额」(收盘后即当日全额)补值,
                # 一次拉全表;失败则保持 0,页面成交额列显示 -。
                if not spot_tried:
                    spot_amount, spot_tried = dg.get_index_spot_amount(), True
                amount = _f(spot_amount.get(code))
            if row is None:
                row = IndexDaily(code=code, name=name, trade_date=td)
                db.add(row)
            row.name, row.close = name, close
            row.pct_chg, row.amount = pct, amount
            row.source = source
            db.commit()
            out[code] = 1
        except Exception as exc:  # noqa: BLE001 单指数失败记 errors 继续
            db.rollback()
            errors[code] = str(exc)[:200]
            log.warning("sync index %s failed: %s", code, exc)
    result: dict = {"indexes": out}
    if errors:
        result["errors"] = errors
    return result


def task_sync_dual_core(db: Session):
    """双核指数日线落库(同花顺 883957/883994):全量 upsert,单核失败继续。

    同花顺 last1200 接口一次返回全量历史(883994 即上市以来全部),因此这里
    直接按 (code, trade_date) 增量补齐:历史日期只插不改,最近 5 根覆盖更新
    (盘中快照收盘后会变),pct_chg 由同一序列收盘价推导,与源端口径一致。
    """
    from .screener.models import DualCoreDaily

    out, errors = {}, {}
    for code, name in dg.DUAL_CORE_DISPLAY:
        try:
            df = dg.get_ths_index_daily(code)
            if df.empty:
                raise ValueError("empty dataframe")
            df = df.sort_values("trade_date").reset_index(drop=True)
            closes = pd.to_numeric(df["close"], errors="coerce")
            pct = (closes / closes.shift(1) - 1) * 100
            existing = {r[0]: r[1] for r in db.execute(
                select(DualCoreDaily.trade_date, DualCoreDaily.id).where(
                    DualCoreDaily.code == code)).all()}
            tail = max(len(df) - 5, 0)
            added = updated = 0
            for i, r in df.iterrows():
                td = r["trade_date"]
                if hasattr(td, "date"):
                    td = td.date()
                values = {
                    "name": name, "open": _f(r.get("open")), "high": _f(r.get("high")),
                    "low": _f(r.get("low")), "close": _f(r.get("close")),
                    "amount": _f(r.get("amount")),
                    "pct_chg": 0.0 if i == 0 or pd.isna(pct.iloc[i]) else _f(pct.iloc[i]),
                    "source": "ths",
                }
                row_id = existing.get(td)
                if row_id is None:
                    db.add(DualCoreDaily(code=code, trade_date=td, **values))
                    added += 1
                elif i >= tail:
                    db.execute(update(DualCoreDaily).where(
                        DualCoreDaily.id == row_id).values(**values))
                    updated += 1
            db.commit()
            out[code] = {"name": name, "rows": int(len(df)), "added": added,
                         "updated": updated, "last": str(df["trade_date"].iloc[-1])}
        except Exception as exc:  # noqa: BLE001 单核失败记 errors 继续
            db.rollback()
            errors[code] = str(exc)[:200]
            log.warning("sync dual core %s failed: %s", code, exc)
    result: dict = {"dual_core": out}
    if errors:
        result["errors"] = errors
    return result


def task_lhb_sync(db: Session, trade_date=None) -> dict:
    """龙虎榜日同步:拉取当日龙虎榜 + 席位→游资匹配落库(逐条 savepoint)。

    trade_date: date/datetime/'YYYYMMDD' 均可,缺省 completed_trade_day。
    seats 映射缺失(无 JSON)时 matched_traders 为空继续落库,不抛错;网关失败抛错。
    """
    seats_map = dg.load_hot_money_seats()
    td = dg.completed_trade_day() if trade_date is None else trade_date
    if hasattr(td, "strftime") and not isinstance(td, str):
        pass
    df = dg.get_lhb_daily(td)
    rows = 0
    for _, r in df.iterrows():
        buy = list(r.get("buy_seats") or [])
        sell = list(r.get("sell_seats") or [])
        matched = match_seats(buy, sell, seats_map)
        tdate = r.get("trade_date")
        if hasattr(tdate, "date"):
            tdate = tdate.date()
        with db.begin_nested():
            exists = db.execute(select(LhbDaily).where(
                LhbDaily.code == r["code"], LhbDaily.trade_date == tdate)
            ).scalar_one_or_none()
            vals = {
                "name": str(r.get("name", "")), "close": _f(r.get("close")),
                "pct_chg": _f(r.get("pct_chg")), "lhb_net": _f(r.get("lhb_net")),
                "buy_seats": json.dumps(buy, ensure_ascii=False),
                "sell_seats": json.dumps(sell, ensure_ascii=False),
                "matched_traders": json.dumps(matched, ensure_ascii=False),
            }
            if exists is None:
                db.add(LhbDaily(code=r["code"], trade_date=tdate, **vals))
            else:
                for k, v in vals.items():
                    setattr(exists, k, v)
        rows += 1
    db.commit()
    ds = tdate.isoformat() if hasattr(tdate, "isoformat") else str(trade_date)
    return {"date": ds, "lhb_rows": rows}


def _same_quote_price(a, b) -> bool:
    a, b = float(a), float(b)
    return (pd.notna(a) and pd.notna(b) and 0 < a < float("inf")
            and 0 < b < float("inf") and abs(a - b) <= max(0.015, abs(b) * 0.0001))


def _verify_adjusted_history(db: Session, code: str, history: dict, before: date) -> None:
    """已有前复权历史每一重叠行都必须可核验，避免除权后不同基准混写。"""
    existing = db.execute(select(DailyQuote).where(
        DailyQuote.code == code, DailyQuote.trade_date < before)).scalars().all()
    for row in existing:
        other = history[row.trade_date]
        if not all(_same_quote_price(getattr(row, key), other[key])
                   for key in ("open", "high", "low", "close")):
            raise ValueError("adjustment baseline changed")


def task_sync_quotes_from_spot(db: Session):
    """收盘后:用逐股带时间的行情生成当日日线行。

    落库前经 validate_quote_date 校验:非交易日/未来日期直接抛错,不写脏数据。
    TaskLog detail 首字段写数据来源(供前端显示):{"source": ...}。
    """
    # 盘中价格不能标成上一交易日；休市日不能猜测快照日期。
    global _sync_quote_cursor
    now = dg.now_tz()
    # Require an explicitly verified trading day.  ``is_trade_day`` fails
    # closed on calendar outages; ``is not True`` also protects this write
    # path if a future calendar adapter introduces an ``unknown`` state.
    if dg.is_trade_day(now.date()) is not True or now.hour < 15:
        raise ValueError("日线快照同步仅能在交易日收盘后运行")
    trade_date = dg.validate_quote_date(dg.completed_trade_day())
    if trade_date != now.date():
        raise ValueError("无法确认当日收盘日期，拒绝同步日线")
    from .portfolio.models import Holding, Watchlist

    # 风控与自选并不限于选股候选域，必须一并获取收盘价。
    portfolio_codes = set(db.execute(select(Holding.code).where(Holding.status == "holding")).scalars())
    portfolio_codes.update(db.execute(select(Watchlist.code)).scalars())
    codes = set(universe_manager.candidate_universe(db)) | portfolio_codes
    if not codes:
        return {"source": "sina", "date": trade_date.isoformat(), "quotes": 0}

    # 只能使用带逐股原始时间的报价；全市场快照/本机抓取时间无法证明
    # 停牌股票的价格属于今天，不能降级后盲目覆盖日线。
    # 限制整项任务和单股历史请求的等待；超时后的网关只读线程可能继续运行。
    deadline = time.monotonic() + 120.0
    rows = 0
    missing = 0
    unprocessed = 0
    # 重跑优先补当日尚无日线的股票，避免每次超时都卡在同一批。
    current = set(db.execute(select(DailyQuote.code).where(
        DailyQuote.trade_date == trade_date, DailyQuote.code.in_(codes))).scalars())
    def rotated(group):
        ordered_group = sorted(group)
        pos = bisect_right(ordered_group, _sync_quote_cursor) if _sync_quote_cursor else 0
        return ordered_group[pos:] + ordered_group[:pos]

    ordered = (rotated((codes - current) & portfolio_codes)
               + rotated((codes - current) - portfolio_codes)
               + rotated(current & portfolio_codes)
               + rotated(current - portfolio_codes))
    quotes = {}
    for index, code in enumerate(ordered):
        if time.monotonic() >= deadline:
            unprocessed = len(ordered) - index
            break
        if index % 50 == 0:
            batch = set(ordered[index:index + 50])
            try:
                quotes, _snapshot_at = dg._run_with_timeout(
                    dg._fetch_sina_batch_quotes, min(8.0, max(0.01, deadline - time.monotonic())), batch)
            except Exception as exc:  # noqa: BLE001 只放弃当前批次，不撤销已入库的股票
                log.warning("closing snapshot batch unavailable: %s", exc)
                quotes = {}
            fetched_at = dg.now_tz()
            if fetched_at.date() != trade_date:
                unprocessed = len(ordered) - index
                break
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=dg.TZ)
            if time.monotonic() >= deadline:
                unprocessed = len(ordered) - index
                break
        _sync_quote_cursor = code
        quote = quotes.get(code) or {}
        try:
            stamp = datetime.fromisoformat(quote.get("quote_at") or "")
            if stamp.tzinfo is None:
                raise ValueError("quote timestamp has no timezone")
            stamp = stamp.astimezone(dg.TZ)
            prices = {key: float(quote[key]) for key in ("open", "high", "low", "price")}
            if (stamp.date() != trade_date or stamp.hour < 15 or stamp > fetched_at
                    or not all(pd.notna(v) and 0 < v < float("inf") for v in prices.values())
                    or prices["high"] < max(prices["open"], prices["price"], prices["low"])
                    or prices["low"] > min(prices["open"], prices["price"])):
                raise ValueError("quote timestamp or prices are invalid")
        except (TypeError, ValueError, KeyError, OverflowError):
            missing += 1
            continue
        # 日线是前复权口径。现货价无法证明历史复权因子未改变，需获取
        # 当日完整前复权序列并核对所有已有重叠日，再允许写入。
        previous = db.execute(select(DailyQuote).where(
            DailyQuote.code == code, DailyQuote.trade_date < trade_date
        ).order_by(DailyQuote.trade_date)).scalars().all()
        start = previous[0].trade_date if previous else trade_date
        try:
            hist, _hist_source = dg._run_with_timeout(
                dg.get_daily_history, min(8.0, max(0.01, deadline - time.monotonic())),
                code, start.strftime("%Y%m%d"), trade_date.strftime("%Y%m%d"),
                source="backfill")
            history = {}
            for record in hist.to_dict("records"):
                day = _to_date(record.get("trade_date"))
                if day in history:
                    raise ValueError("duplicate daily date")
                history[day] = record
            daily = history[trade_date]
            daily_prices = {key: float(daily[key]) for key in ("open", "high", "low", "close")}
            if not all(_same_quote_price(daily_prices[key], prices["price" if key == "close" else key])
                       for key in ("open", "high", "low", "close")):
                raise ValueError("daily quote does not match verified closing snapshot")
            _verify_adjusted_history(db, code, history, trade_date)
        except Exception as exc:  # noqa: BLE001 单股缺失/除权重算不得污染已有序列
            log.warning("daily quote %s could not be verified: %s", code, exc)
            missing += 1
            continue
        # 只覆盖来源实际提供的字段，避免无换手率/市值的源将历史值清零。
        vals = daily_prices
        for field in ("amount", "turnover", "pct_chg", "total_mv", "volume"):
            value = daily.get(field)
            try:
                number = float(value)
                if pd.notna(number) and abs(number) < float("inf") and (field == "pct_chg" or number > 0):
                    vals[field] = number
            except (TypeError, ValueError, OverflowError):
                pass
        with db.begin_nested():
            exists = db.execute(select(DailyQuote).where(
                DailyQuote.code == code, DailyQuote.trade_date == trade_date)).scalar_one_or_none()
            if exists is None:
                db.add(DailyQuote(code=code, trade_date=trade_date, **vals))
            else:
                for key, value in vals.items():
                    setattr(exists, key, value)
        # 网络调用之间不持有 SQLite 写事务，避免阻塞持仓与其他任务。
        db.commit()
        rows += 1
    result = {"source": "sina", "date": trade_date.isoformat(), "quotes": rows,
              "unprocessed": unprocessed}
    errors = []
    if missing:
        errors.append(f"{missing} 只股票缺少可核验的当日收盘报价，已跳过")
    if unprocessed:
        errors.append(f"{unprocessed} 只股票因任务时限或跨日尚未处理")
    if errors:
        result["errors"] = errors
    return result


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
    trade_date = dg.completed_trade_day()
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
    errors = []
    for u in users:
        try:
            rec = weekly_report(db, u.id)
            done.append({"user": u.username, "report_id": rec.id})
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning("weekly report for %s failed: %s", u.username, exc)
            errors.append({"user": u.username, "error": str(exc)})
    return {"reports": done, "errors": errors}


def task_fund_flow_snapshot(db: Session):
    """板块概念资金流盘中快照:industry/concept 各一行/板块落 FundFlowSnapshot。

    单源失败记 errors 继续另一源;返回 {"rows": n, "errors": [...]}。"""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    snap_time = dg.now_tz().replace(tzinfo=None, second=0, microsecond=0)
    sources = (("industry", dg.get_industry_fund_flow),
               ("concept", dg.get_concept_fund_flow))
    rows, errors = 0, []
    for board_type, fetch in sources:
        try:
            df = fetch()
        except Exception as exc:  # noqa: BLE001 单源失败隔离,继续另一源
            errors.append(f"{board_type}: {exc}"[:200])
            log.warning("fund flow %s failed: %s", board_type, exc)
            continue
        for _, r in df.iterrows():
            name = str(r.get("board_name") or "").strip()
            if not name:
                continue
            # 源无 board_code 时用 board_name 兜底,保证
            # (board_type, board_code, snap_time) 唯一约束下同截面多板块不互踩。
            code = str(r.get("board_code") or name).strip()[:32]
            try:
                vals = {"board_type": board_type, "board_code": code,
                        "board_name": name,
                        "net_inflow": _f(r.get("fund_flow")),
                        "snap_time": snap_time, "source": "eastmoney"}
                # 逐行 savepoint:单行失败只回滚本行,不再用 db.rollback()
                # (那会把本循环已积累的未提交行整批回滚,计数虚高)。
                with db.begin_nested():
                    if db.bind and db.bind.dialect.name == "sqlite":
                        stmt = sqlite_insert(FundFlowSnapshot).values(**vals)
                        stmt = stmt.on_conflict_do_nothing(
                            index_elements=["board_type", "board_code", "snap_time"])
                        db.execute(stmt)
                    else:
                        exists = db.execute(select(FundFlowSnapshot).where(
                            FundFlowSnapshot.board_type == board_type,
                            FundFlowSnapshot.board_code == code,
                            FundFlowSnapshot.snap_time == snap_time)).scalar_one_or_none()
                        if exists is None:
                            db.add(FundFlowSnapshot(**vals))
                rows += 1
            except Exception as exc:  # noqa: BLE001 单行失败不污染整批(savepoint 已回滚本行)
                errors.append(f"{board_type}/{name}: {exc}"[:200])
    db.commit()
    out: dict = {"rows": rows, "time": snap_time.isoformat()}
    if errors:
        out["errors"] = errors
    return out


def task_market_stat(db: Session):
    """大盘统计盘中快照:落库一行 MarketStat,返回 {"stat": {...}}。"""
    stat = dg.get_market_stat_spot()
    snap_time = dg.now_tz().replace(tzinfo=None, second=0, microsecond=0)
    exists = db.execute(select(MarketStat).where(
        MarketStat.snap_time == snap_time)).scalar_one_or_none()
    if exists is None:
        db.add(MarketStat(
            snap_time=snap_time, advance=int(stat.get("advance") or 0),
            decline=int(stat.get("decline") or 0),
            limit_up=int(stat.get("limit_up") or 0),
            limit_down=int(stat.get("limit_down") or 0),
            amount_total=_f(stat.get("amount_total")),
            source=str(stat.get("source") or ""),
            source_note=str(stat.get("source_note") or "")))
        db.commit()
    return {"stat": {**{k: stat.get(k) for k in
                        ("advance", "decline", "limit_up", "limit_down",
                         "amount_total", "source", "source_note")},
                     "snap_time": snap_time.isoformat()}}


WATCH_ALERT_WINDOW_HOURS = 24
# 一次扫描最多等待被超时工作线程占用的预警槽 15 秒，避免后续批次瞬间丢弃。
WATCH_ALERT_BUSY_WAIT_SECONDS = 15.0
WATCH_ALERT_BUSY_RETRY_SECONDS = 0.25


def task_watch_alert(db: Session):
    """自购三价预警(只读,不写止损):遍历 Watchlist,命中规则生成预警并去重落库。

    规则:close>=take_profit / close<=stop_loss / |pct|>=alert_pct /
    触及 cost_price±1%;去重:同 user+code+rule 24h 内已推送跳过
    (查 WatchAlertLog,只按时间查,无唯一约束)。
    返回 {"alerts": n, "errors": [...]}。"""
    from datetime import datetime, timedelta, timezone

    from .portfolio.models import WatchAlertLog, Watchlist
    from .portfolio import live_quotes

    watches = db.execute(select(Watchlist)).scalars().all()
    # 每批取到后立刻处理，后续慢批次不会让前批行情过期。
    # 独立预警并发槽使页面轮询不挤占扫描容量。
    codes = list(dict.fromkeys(w.code for w in watches))
    alerts, errors = 0, []
    since = datetime.now(timezone.utc).replace(tzinfo=None) \
        - timedelta(hours=WATCH_ALERT_WINDOW_HOURS)
    busy_budget = WATCH_ALERT_BUSY_WAIT_SECONDS
    for start in range(0, len(codes), 50):
        batch = codes[start:start + 50]
        quotes, batch_errors = live_quotes.fetch(batch, priority=True)
        while batch_errors == ['行情请求繁忙'] and busy_budget > 0:
            wait = min(WATCH_ALERT_BUSY_RETRY_SECONDS, busy_budget)
            before = time.monotonic()
            time.sleep(wait)
            busy_budget = max(0, busy_budget - (time.monotonic() - before))
            quotes, batch_errors = live_quotes.fetch(batch, priority=True)
        errors.extend(f"批次 {start + 1}: {error}" for error in batch_errors)
        batch_alerts = 0
        try:
            # SQLite 的旧事务模式不会因 SELECT 自动 BEGIN；显式开启外层事务，
            # 使逐条 SAVEPOINT 在本批提交失败时也能整体回滚。
            conn = db.connection()
            if conn.dialect.name == "sqlite" and not conn.connection.driver_connection.in_transaction:
                conn.exec_driver_sql("BEGIN")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append(f"批次 {start + 1} 开始事务失败: {exc}"[:200])
            continue
        for w in (item for item in watches if item.code in batch):
            row_alerts = 0
            try:
                # 逐自选 savepoint:单条失败只回滚本条,不整批回滚
                with db.begin_nested():
                    quote = quotes.get(w.code)
                    if not quote or not live_quotes.is_realtime(quote):
                        errors.append(f"{w.code}: 无可核验的新鲜盘中行情")
                        continue
                    close = quote["price"]
                    pct = quote.get("pct_chg")
                    hits: list[tuple[str, str]] = []
                    if w.take_profit and close >= w.take_profit:
                        hits.append(("take_profit", f"{w.code} 现价 {close} 已达止盈位 {w.take_profit}"))
                    if w.stop_loss and close <= w.stop_loss:
                        hits.append(("stop_loss", f"{w.code} 现价 {close} 已触止损位 {w.stop_loss}"))
                    if w.alert_pct and pct is not None and abs(pct) >= w.alert_pct:
                        hits.append(("alert_pct", f"{w.code} 涨跌幅 {pct:.2f}% 触阈值 ±{w.alert_pct}%"))
                    if w.cost_price and abs(close / w.cost_price - 1) <= 0.01:
                        hits.append(("cost_touch", f"{w.code} 现价 {close} 触及成本 {w.cost_price}±1%"))
                    for rule, message in hits:
                        dup = db.execute(select(WatchAlertLog.id).where(
                            WatchAlertLog.user_id == w.user_id, WatchAlertLog.code == w.code,
                            WatchAlertLog.rule == rule,
                            WatchAlertLog.created_at >= since)).first()
                        if dup:
                            continue  # 24h 内已推送,去重跳过
                        db.add(WatchAlertLog(user_id=w.user_id, code=w.code,
                                             rule=rule, message=message))
                        row_alerts += 1
                batch_alerts += row_alerts
            except Exception as exc:  # noqa: BLE001 单行异常隔离(savepoint 已回滚本条)
                errors.append(f"{w.code}: {exc}"[:200])
                log.warning("watch alert %s failed: %s", w.code, exc)
        try:
            db.commit()
            alerts += batch_alerts
        except Exception as exc:  # noqa: BLE001 仅回滚本批，继续后续批次
            db.rollback()
            errors.append(f"批次 {start + 1} 提交失败: {exc}"[:200])
            log.warning("watch alert batch %s commit failed: %s", start + 1, exc)
    out: dict = {"alerts": alerts}
    if errors:
        out["errors"] = errors
    return out


def task_holding_patrol(db: Session):
    """持仓巡检(唯一写止损的定时入口):逐用户调用 update_daily(user_id=...)。

    异常按用户隔离:单个用户失败记 errors 继续下一用户,整体不中断;
    有 errors 时记 degraded(前端可区分),不冒充全成功。
    GET/日报禁止写库,日报一律走 report_service._alerts 只读计算。"""
    from .portfolio.holding_service import update_daily

    users = db.execute(select(User).where(User.is_active == True)).scalars().all()  # noqa: E712
    done, errors = [], []
    for u in users:
        try:
            alerts = update_daily(db, user_id=u.id)
            done.append({"user": u.username,
                         "alerts": len(alerts) if isinstance(alerts, list) else 0})
        except Exception as exc:  # noqa: BLE001 单用户异常隔离,不阻断其他用户
            db.rollback()
            errors.append(f"{u.username}: {exc}"[:200])
            log.warning("holding patrol for %s failed: %s", u.username, exc)
    out: dict = {"users": done}
    if errors:
        out["errors"] = errors
    return out


def _to_date(val) -> date | None:
    """把 SQLite 聚合返回的字符串/日期统一成 date。"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _quote_rows(code: str, df: pd.DataFrame) -> list[dict]:
    """DataFrame → DailyQuote 批量插入行;跳过无效交易日。"""
    rows: list[dict] = []
    for r in df.to_dict("records"):
        td = r.get("trade_date")
        if td is None or pd.isna(td):
            continue
        rows.append({
            "code": code, "trade_date": td,
            "open": _f(r.get("open")), "high": _f(r.get("high")),
            "low": _f(r.get("low")), "close": _f(r.get("close")),
            "volume": _f(r.get("volume")), "amount": _f(r.get("amount")),
            "turnover": _f(r.get("turnover")), "pct_chg": _f(r.get("pct_chg")),
            "total_mv": 0.0, "adj_factor": 1.0,
        })
    return rows


def _insert_quotes(db: Session, rows: list[dict]) -> int:
    """批量写入日线,按 (code, trade_date) 去重(重复数据直接忽略),返回新增行数。

    SQLite 走 INSERT .. ON CONFLICT DO NOTHING 分批提交,比逐行 ORM add
    快一个数量级;其他数据库保持逐行存在性检查(兼容迁移前的路径)。
    """
    added = 0
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(DailyQuote).on_conflict_do_nothing(
            index_elements=["code", "trade_date"])
        conn = db.connection()
        # 每批 50 行,避免超过老版本 SQLite 的 999 个绑定参数上限
        for i in range(0, len(rows), 50):
            res = conn.execute(stmt, rows[i:i + 50])
            added += max(res.rowcount or 0, 0)  # 冲突被忽略的行不计入新增
        return added
    for row in rows:
        exists = db.execute(select(DailyQuote.id).where(
            DailyQuote.code == row["code"],
            DailyQuote.trade_date == row["trade_date"])).first()
        if exists is None:
            db.add(DailyQuote(**row))
            added += 1
    return added


def task_backfill_history(db: Session, years: int = 5, progress=None):
    """历史日线回填(前复权):按个股已有覆盖范围增量抓取,不重复下全量。

    - 首次(库内该股没有更早历史)按 years 年全量拉取;之后只补最新交易日
      之后的数据,已最新的股票直接跳过(不发请求),重复执行成本很低。
    - 回填专用源:新浪 → 腾讯 → baostock(跳过被限流的东财,见 data_gateway)。
    - 单股失败进重试队列,第二轮换 tx 源重试(跳过东财);仍失败才计入
      codes_failed,前端据此显示"部分完成"而非整单失败。
    """
    years = max(1, min(int(years or 5), 20))
    codes = universe_manager.candidate_universe(db)
    total = len(codes)
    end_day = dg.completed_trade_day()
    start_day = dg.today() - timedelta(days=years * 365)

    # 一次查出全部个股的已有日期覆盖,避免逐股 SELECT 全部历史日期
    coverage = {
        str(code): (_to_date(mn), _to_date(mx))
        for code, mn, mx in db.execute(
            select(DailyQuote.code, func.min(DailyQuote.trade_date),
                   func.max(DailyQuote.trade_date)).group_by(DailyQuote.code))
    }

    done = skipped = inserted = 0
    failures: list[tuple[str, str, date]] = []

    def _fetch_one(code: str, fetch_start: date, src: str) -> int:
        # 前复权会因除权调整历史；已有区间必须重取并核验基准。
        cov = coverage.get(code)
        if cov and cov[0] is not None:
            fetch_start = min(fetch_start, cov[0])
        df, _source = dg.get_daily_history(
            code, fetch_start.strftime("%Y%m%d"), end_day.strftime("%Y%m%d"),
            source=src)
        rows = _quote_rows(code, df) if not df.empty else []
        if not rows:
            raise ValueError("empty daily history")
        history = {}
        for row in rows:
            if row["trade_date"] in history:
                raise ValueError("duplicate daily date")
            history[row["trade_date"]] = row
        _verify_adjusted_history(db, code, history, end_day + timedelta(days=1))
        with db.begin_nested():
            added = _insert_quotes(db, rows)
        db.commit()
        return added

    def _report(i: int) -> None:
        if progress and (i == 1 or i % 20 == 0 or i == total):
            progress(f"回填中 {i}/{total} · 新增 {inserted} 行 · 跳过 {skipped} 只"
                     f" · 待重试 {len(failures)} 只 · 覆盖 {years} 年")

    for i, code in enumerate(codes, 1):
        cov = coverage.get(code)
        fetch_start = start_day
        if cov and cov[0] is not None:
            mn, mx = cov
            # 已有更早历史(已覆盖到请求起点附近)→ 只补最新交易日之后
            if mn <= start_day + timedelta(days=10):
                nxt = (mx or mn) + timedelta(days=1)
                if nxt > end_day:
                    skipped += 1
                    _report(i)
                    continue
                fetch_start = nxt
        try:
            inserted += _fetch_one(code, fetch_start, "backfill")
            done += 1
        except Exception as exc:  # noqa: BLE001 单股失败进重试队列,不拖垮整单
            db.rollback()
            failures.append((code, f"{code}: {exc}"[:200], fetch_start))
            log.warning("backfill %s failed (will retry): %s", code, exc)
        _report(i)

    # 第二轮:失败股票换 tx 源重试一次(跳过东财,避免再等 30s+ 重试)
    still: list[str] = []
    for code, msg, fetch_start in failures:
        try:
            inserted += _fetch_one(code, fetch_start, "tx")
            done += 1
        except Exception as exc:  # noqa: BLE001 重试仍失败才计入失败
            db.rollback()
            still.append(f"{msg} | retry: {exc}"[:200])
            log.warning("backfill retry %s failed: %s", code, exc)

    out: dict = {"years": years, "codes_done": done,
                 "codes_failed": len(still), "codes_skipped": skipped,
                 "rows_added": inserted,
                 "range": f"{start_day.isoformat()}~{end_day.isoformat()}"}
    if still:
        out["errors"] = still[:20]  # _is_degraded 据此记 degraded,不冒充 success
    return out


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
    "lhb_sync": task_lhb_sync,
    "weekly_reports": task_weekly_reports,
    "holding_patrol": task_holding_patrol,
    "backfill_history": task_backfill_history,
    "sync_indexes": task_sync_indexes,
    "dual_core": task_sync_dual_core,
    "tracks_backfill": task_tracks_backfill,
    "fund_flow": task_fund_flow_snapshot,
    "market_stat": task_market_stat,
    "watch_alert": task_watch_alert,
}


def _legacy_run_task_removed():  # noqa: N806 占位防误用
    raise RuntimeError("use run_task() defined above")
