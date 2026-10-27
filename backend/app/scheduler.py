"""定时任务调度(APScheduler,交易日本地时区 Asia/Shanghai)。

任务链(收盘后流水线顺序依赖,各步独立容错;与 build_scheduler 内 cron 保持同步):
  09:00-14:30 每30分钟  盘中快照(9:00 在集合竞价前仅作留痕,有效序列从 9:35 起)
  09:35/10:05-13:35/14:05  fund_flow + market_stat + watch_alert(三任务各错峰 1 分钟)
  11:40 / 14:40         新闻低频采集
  15:35                 日线落库(快照口径)+ 股票基础信息
  15:50                 持仓巡检(G03 止损推进,唯一写入口)
  16:05-16:40           估值 + 行业监测 + 商品 + 市场状态 + 技术信号 + 指数
  16:35                 财务增量
  17:05                 策略漏斗 + 池表现;17:10 龙虎榜
  周五 17:45            逐用户周报
  周一 08:30/08:40      指数成分 + 赛道维护
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .shared import data_gateway as dg
from .task_runner import run_task

log = logging.getLogger("scheduler")

# NOTE:巡检写库唯一入口为 task_runner.task_holding_patrol(逐用户 update_daily)。
# 此处不再直接 import update_daily/SessionLocal:避免调度层绕过任务日志与互斥锁。

_scheduler: BackgroundScheduler | None = None


def _guarded(name: str, **kwargs):
    """非交易日自动跳过 + 异常吞掉(APScheduler 线程内不允许抛出)。"""
    try:
        if not dg.is_trade_day():
            log.info("skip task %s: not a trade day", name)
            return
        result = run_task(name, **kwargs)
        log.info("scheduled task %s -> %s", name, result)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduled task %s failed: %s", name, exc)


def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=settings.tz)
    interval = settings.intraday_interval_minutes

    sched.add_job(_guarded, "cron", args=["intraday_snapshot"],
                  day_of_week="mon-fri", hour="9-14", minute=f"*/{interval}",
                  id="intraday_snapshot", max_instances=1, coalesce=True)

    # P1 盘中快照(9:35-14:05 每30分钟):fund_flow(板块概念资金流) +
    # market_stat(大盘统计) + watch_alert(自购三价预警,只读不写止损)。
    # 三任务各错峰 1 分钟(35/36/37),避免与 intraday 同分钟齐发挤占 SQLite 写锁。
    for i, name in enumerate(("fund_flow", "market_stat", "watch_alert")):
        m = 35 + i
        sched.add_job(_guarded, "cron", args=[name],
                      day_of_week="mon-fri", hour=9, minute=m,
                      id=f"intraday_{name}_0935", max_instances=1, coalesce=True)
        sched.add_job(_guarded, "cron", args=[name],
                      day_of_week="mon-fri", hour="10-13", minute="5,35",
                      id=f"intraday_{name}", max_instances=1, coalesce=True)
        sched.add_job(_guarded, "cron", args=[name],
                      day_of_week="mon-fri", hour=14, minute=5,
                      id=f"intraday_{name}_1405", max_instances=1, coalesce=True)

    for hm in ((11, 40), (14, 40)):
        sched.add_job(_guarded, "cron", args=["news"],
                      day_of_week="mon-fri", hour=hm[0], minute=hm[1],
                      id=f"news_{hm[0]}{hm[1]}", max_instances=1, coalesce=True)

    sched.add_job(_guarded, "cron", args=["sync_quotes"],
                  day_of_week="mon-fri", hour=15, minute=35,
                  id="sync_quotes", max_instances=1, coalesce=True)
    sched.add_job(_guarded, "cron", args=["sync_basic"],
                  day_of_week="mon-fri", hour=15, minute=45,
                  id="sync_basic", max_instances=1, coalesce=True)

    for name, minute in (("sync_valuation", 5), ("industry_daily", 15),
                         ("commodities", 20), ("market_state", 30), ("signals", 40)):
        sched.add_job(_guarded, "cron", args=[name],
                      day_of_week="mon-fri", hour=16, minute=minute,
                      id=f"eod_{name}", max_instances=1, coalesce=True)

    sched.add_job(_guarded, "cron", args=["sync_financials"],
                  day_of_week="mon-fri", hour=16, minute=35,
                  id="sync_financials", max_instances=1, coalesce=True)

    # 指数日线(收盘后 16:45):估值/行业之后、策略之前,不依赖策略;失败不阻塞策略。
    sched.add_job(_guarded, "cron", args=["sync_indexes"],
                  day_of_week="mon-fri", hour=16, minute=45,
                  id="eod_indexes", max_instances=1, coalesce=True)

    # 双核指数(收盘后 16:50):同花顺 883957/883994 全量 upsert,
    # 供「双核共振」页面当日判定与次日一票否决使用。
    sched.add_job(_guarded, "cron", args=["dual_core"],
                  day_of_week="mon-fri", hour=16, minute=50,
                  id="eod_dual_core", max_instances=1, coalesce=True)

    sched.add_job(_guarded, "cron", args=["strategy"],
                  day_of_week="mon-fri", hour=17, minute=5,
                  id="strategy", max_instances=1, coalesce=True)

    # 龙虎榜同步(收盘后 17:10):strategy 之后,不阻塞策略;失败不影响策略结果。
    sched.add_job(_guarded, "cron", args=["lhb_sync"],
                  day_of_week="mon-fri", hour=17, minute=10,
                  id="eod_lhb", max_instances=1, coalesce=True)

    # 持仓巡检(交易日 15:50,收盘后):逐用户推进 G03 止损,异常按用户隔离。
    # GET/日报禁止写库,唯一写入口为 task_runner.task_holding_patrol。
    sched.add_job(_guarded, "cron", args=["holding_patrol"],
                  day_of_week="mon-fri", hour=15, minute=50,
                  id="holding_patrol", max_instances=1, coalesce=True)

    sched.add_job(_guarded, "cron", args=["weekly_reports"],
                  day_of_week="fri", hour=17, minute=45,
                  id="weekly_reports", max_instances=1, coalesce=True)

    # 每周一 08:30 同步指数成分与赛道(低频维护)
    sched.add_job(_guarded, "cron", args=["sync_members"],
                  day_of_week="mon", hour=8, minute=30,
                  id="weekly_members", max_instances=1, coalesce=True)
    sched.add_job(_guarded, "cron", args=["sync_tracks"],
                  day_of_week="mon", hour=8, minute=40,
                  id="weekly_tracks", max_instances=1, coalesce=True)
    return sched


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = build_scheduler()
        _scheduler.start()
        log.info("scheduler started")
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
