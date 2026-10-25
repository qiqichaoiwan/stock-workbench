"""报告服务:每日持仓日报 + 每周报告(逐用户生成)。"""
import json
import logging
import statistics
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..shared import llm_gateway, stock_ref
from ..screener.market_regime import latest_state
from ..screener.models import NewsItem, StockPool
from ..screener.strategy_engine import get_params
from .ai_advisor import DISCLAIMER
from .holding_service import holdings_view, trade_stats, update_daily
from .models import TradeRecord, WeeklyReport

log = logging.getLogger("report")


def _alerts(db: Session) -> list[dict]:
    try:
        return update_daily(db)
    except Exception as exc:  # noqa: BLE001
        log.warning("holding daily update failed: %s", exc)
        return []


def daily_report(db: Session, user_id: int) -> str:
    """每日持仓日报:评级变化、止损提醒、市场状态。规则输出,不依赖 LLM。"""
    alerts = _alerts(db)
    views = holdings_view(db, user_id)
    state = latest_state(db)
    lines = [
        f"# 持仓日报 {date.today().isoformat()}",
        "",
        f"**市场状态**:{state.trend_label or '未知'} / {state.cycle_stage or '未知'},"
        f"建议仓位 {state.position_range}(PM02草案,待验证)"
        if state else "**市场状态**:暂无",
        "",
    ]
    if alerts:
        lines.append("## ⚠️ 风险提醒")
        for a in alerts:
            lines.append(f"- [{a['level']}] {a['message']}")
        lines.append("")
    if views:
        lines.append("## 持仓")
        lines.append("| 代码 | 名称 | 现价 | 浮盈% | 止损价 | 阶段 | 池评级 |")
        lines.append("|---|---|---|---|---|---|---|")
        for v in views:
            pr = v.get("pool_rating") or {}
            lines.append(
                f"| {v['code']} | {v['name']} | {v['close']} | {v['unrealized_pct']} | "
                f"{v['stop_price']} | {v['stop_stage']} | {pr.get('rating', '-')} |")
    else:
        lines.append("暂无持仓。")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def weekly_report(db: Session, user_id: int) -> WeeklyReport:
    """每周报告:本周交易复盘+持仓表现+市场回顾+AI 复盘(LLM 可用时)。"""
    today = date.today()
    week_start = today - timedelta(days=today.weekday() + 7)  # 上周一
    week_end = week_start + timedelta(days=4)

    trades = db.execute(select(TradeRecord).where(
        TradeRecord.user_id == user_id,
        TradeRecord.sell_date >= week_start, TradeRecord.sell_date <= week_end)).scalars().all()
    stats = trade_stats(db, user_id)
    views = holdings_view(db, user_id)
    pool_perf = stock_ref.pool_perf_summary(db)
    state = latest_state(db)

    lines = [
        f"# 周报 {week_start.isoformat()} ~ {week_end.isoformat()}",
        "",
        f"**市场**:{state.trend_label if state else '-'}/{state.cycle_stage if state else '-'},"
        f"建议仓位 {state.position_range if state else '-'}(PM02草案)",
        "",
        f"**选股池表现**:{json.dumps(pool_perf, ensure_ascii=False) if pool_perf else '暂无'}",
        "",
        f"**本周卖出**:{len(trades)} 笔;"
        f"累计统计:胜率{stats.get('win_rate', '-')}%,"
        f"盈亏比{stats.get('profit_factor', '-')},总盈亏{stats.get('total_pnl', 0)}元",
        "",
        "## 当前持仓概览",
    ]
    for v in views:
        lines.append(f"- {v['code']} {v['name']}:现价{v['close']},"
                     f"浮盈{v['unrealized_pct']}%,阶段{v['stop_stage']},"
                     f"止损{v['stop_price']}")
    content = "\n".join(lines)

    # AI 复盘(可选,失败不阻断)
    try:
        system = "你是严谨的A股投研助手,做周度复盘,输出300字以内,客观指出问题与下周关注点。"
        extra, model = llm_gateway.chat(db, user_id, system, content[:3000])
        content += "\n\n## AI 复盘(参考建议)\n" + extra + "\n"
    except Exception as exc:  # noqa: BLE001
        log.info("weekly ai skipped: %s", exc)

    rec = WeeklyReport(user_id=user_id, week_start=week_start, week_end=week_end,
                       title=f"周报 {week_start.isoformat()}",
                       content=content,
                       stats_json=json.dumps(stats, ensure_ascii=False))
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def list_weekly_reports(db: Session, user_id: int, limit: int = 20) -> list[dict]:
    rows = db.execute(select(WeeklyReport).where(WeeklyReport.user_id == user_id)
                      .order_by(WeeklyReport.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "title": r.title, "week_start": r.week_start.isoformat(),
             "week_end": r.week_end.isoformat(), "created_at": r.created_at.isoformat()}
            for r in rows]


def get_weekly_report(db: Session, user_id: int, report_id: int) -> WeeklyReport | None:
    rec = db.get(WeeklyReport, report_id)
    return rec if rec and rec.user_id == user_id else None


def top_news(db: Session, limit: int = 20) -> list[dict]:
    rows = db.execute(select(NewsItem).where(NewsItem.screened == True)  # noqa: E712
                      .order_by(NewsItem.heat.desc(), NewsItem.id.desc())
                      .limit(limit)).scalars().all()
    return [{"id": r.id, "title": r.title, "summary": r.summary[:200],
             "category": r.category, "sentiment": r.sentiment,
             "related_industry": r.related_industry,
             "publish_time": r.publish_time.isoformat() if r.publish_time else "",
             "heat": r.heat, "screened": bool(r.screened)} for r in rows]


def pool_overview(db: Session) -> dict:
    """选股池总览(供仪表盘与持仓工作台引用,只读)。"""
    open_pools = db.execute(select(StockPool).where(
        StockPool.status == "in").order_by(StockPool.score.desc())).scalars().all()
    main = [{"code": p.code, "name": p.name, "rating": p.rating, "score": p.score,
             "in_price": p.in_price, "trade_date": p.trade_date.isoformat()}
            for p in open_pools if p.pool_type == "main"]
    by_track: dict[str, list] = {}
    for p in open_pools:
        if p.pool_type == "track":
            by_track.setdefault(str(p.track_id), []).append(
                {"code": p.code, "name": p.name, "rating": p.rating, "score": p.score})
    params = get_params(db)
    return {"main": main, "by_track": by_track, "updated": main[0]["trade_date"] if main else "",
            "capacity": params.get("pool_top_n")}
