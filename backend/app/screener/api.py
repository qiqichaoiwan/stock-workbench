"""Screener REST API:仪表盘/选股池/行业/信号/新闻/赛道/策略配置/任务。

权限分级:GET 看板=登录即可;写操作(任务/参数/策略卡/赛道/池)=admin 或 analyst。
"""
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..portfolio.models import User
from ..screener import industry_monitor, market_regime, news_monitor, strategy_engine, \
    technical_signal, universe_manager
from ..screener.models import (CommodityPrice, IndustryDaily, MarketState, NewsItem,
                               PoolPerformance, StockPool, StrategyCard, StrategyParams,
                               TaskLog, TechnicalSignal, Track)
from ..shared.security import current_user_id, require_analyst
from ..task_runner import TASKS, run_task

router = APIRouter(prefix="/api/screener", tags=["screener"])

MAX_LIMIT = 500


def _stock_names(db: Session) -> dict[str, str]:
    from .models import StockBasic

    return dict(db.execute(select(StockBasic.code, StockBasic.name)).all())


# ---------- 仪表盘 ----------

@router.get("/dashboard")
def dashboard(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    state = market_regime.latest_state(db)
    from ..shared import stock_ref

    avoid = industry_monitor.crowd_avoid_industries(db)
    signals = technical_signal.latest_signals(db, limit=50)
    return {
        "market_state": {
            "trade_date": state.trade_date.isoformat() if state else "",
            "trend_label": state.trend_label if state else "",
            "cycle_stage": state.cycle_stage if state else "",
            "position_range": state.position_range if state else "",
            "breadth": state.breadth if state else 0,
            "amount_pct_60d": state.amount_pct_60d if state else 0,
            "valuation_pct": state.valuation_pct if state else 0,
            "margin_ratio": state.margin_ratio if state else 0,
            "note": "PM02 仓位分档为待验证草案" if state else "",
        } if state else None,
        "pool_perf": stock_ref.pool_perf_summary(db),
        "crowd_avoid": avoid,
        "market_wide_crowded": len(avoid) > industry_monitor.AVOID_MAX,
        "rising_commodities": industry_monitor.rising_commodities(db),
        "signals": [{"code": s.code, "name": _stock_names(db).get(s.code, ""),
                     "type": s.signal_type, "strength": s.strength,
                     "trade_date": s.trade_date.isoformat()} for s in signals[:20]],
        "risk_guard": market_regime.risk_guard_active(db),
    }


@router.get("/market-states")
def market_states(limit: int = 60, user_id: int = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    limit = min(max(limit, 1), MAX_LIMIT)
    rows = db.execute(select(MarketState).order_by(
        MarketState.trade_date.desc()).limit(limit)).scalars().all()
    return [{"trade_date": r.trade_date.isoformat(), "trend_label": r.trend_label,
             "cycle_stage": r.cycle_stage, "position_range": r.position_range,
             "breadth": r.breadth, "amount_pct_60d": r.amount_pct_60d,
             "valuation_pct": r.valuation_pct, "margin_ratio": r.margin_ratio,
             "score": json.loads(r.score_json or "{}")} for r in rows]


# ---------- 选股池 ----------

@router.get("/pool")
def pool(pool_type: str = "main", track_id: int | None = None,
         user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    q = select(StockPool).where(StockPool.status == "in",
                                StockPool.pool_type == pool_type)
    if pool_type == "track" and track_id is not None:
        q = q.where(StockPool.track_id == track_id)
    rows = db.execute(q.order_by(StockPool.score.desc())).scalars().all()
    return [{"id": r.id, "code": r.code, "name": r.name, "rating": r.rating,
             "score": r.score, "in_price": r.in_price,
             "trade_date": r.trade_date.isoformat(),
             "reasons": json.loads(r.reasons or "[]"),
             "track_id": r.track_id} for r in rows]


@router.get("/pool/history")
def pool_history(status: str = "out", limit: int = 100,
                 user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    limit = min(max(limit, 1), MAX_LIMIT)
    rows = db.execute(select(StockPool).where(StockPool.status == status)
                      .order_by(StockPool.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "code": r.code, "name": r.name, "pool_type": r.pool_type,
             "in_price": r.in_price, "trade_date": r.trade_date.isoformat(),
             "out_date": r.out_date.isoformat() if r.out_date else "",
             "out_reason": r.out_reason, "score": r.score} for r in rows]


@router.get("/pool/performance")
def pool_perf(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    from ..shared import stock_ref

    rows = db.execute(select(PoolPerformance).order_by(
        PoolPerformance.trade_date.desc()).limit(1000)).scalars().all()
    by_date: dict[str, list[float]] = {}
    for r in rows:
        by_date.setdefault(r.trade_date.isoformat(), []).append(r.cum_pct)
    series = [{"date": d, "avg_cum": round(sum(v) / len(v), 2)}
              for d, v in sorted(by_date.items())]
    return {"summary": stock_ref.pool_perf_summary(db), "series": series}


@router.post("/pool/{pool_id}/out")
def force_out(pool_id: int, reason: str = "manual",
              analyst: User = Depends(require_analyst),
              user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    p = db.get(StockPool, pool_id)
    if p is None or p.status != "in":
        raise HTTPException(404, "池记录不存在")
    p.status, p.out_date, p.out_reason = "out", date.today(), f"manual:{reason}"
    db.commit()
    return {"ok": True}


# ---------- 行业与商品 ----------

@router.get("/industries")
def industries(trade_date: str | None = None, user_id: int = Depends(current_user_id),
               db: Session = Depends(get_db)):
    q = select(IndustryDaily)
    if trade_date:
        q = q.where(IndustryDaily.trade_date == date.fromisoformat(trade_date))
    else:
        latest = db.execute(select(IndustryDaily.trade_date).order_by(
            IndustryDaily.trade_date.desc()).limit(1)).scalar_one_or_none()
        if latest is None:
            return []
        q = q.where(IndustryDaily.trade_date == latest)
    rows = db.execute(q.order_by(IndustryDaily.heat_score.desc())).scalars().all()
    return [{"industry_code": r.industry_code, "industry_name": r.industry_name,
             "trade_date": r.trade_date.isoformat(), "close": r.close,
             "pct_chg": r.pct_chg, "ratio": r.ratio, "ratio_score": r.ratio_score,
             "avoid_until": r.avoid_until.isoformat() if r.avoid_until else "",
             "fund_flow": r.fund_flow, "flow_streak": r.flow_streak,
             "mom_20": r.mom_20, "mom_60": r.mom_60, "heat_score": r.heat_score}
            for r in rows]


@router.get("/emerging")
def emerging(top: int = 12, user_id: int = Depends(current_user_id),
             db: Session = Depends(get_db)):
    """新兴赛道雷达:启动期优先(低位+转强+未拥挤),叠加新闻热度突增。"""
    return industry_monitor.emerging_tracks(db, top=min(max(top, 1), 30))


@router.get("/commodities")
def commodities(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.execute(select(CommodityPrice).order_by(
        CommodityPrice.series_name, CommodityPrice.trade_date.desc())).scalars().all()
    seen: dict[str, CommodityPrice] = {}
    for r in rows:
        seen.setdefault(r.series_name, r)
    return [{"series_name": r.series_name, "related_track": r.related_track,
             "price": r.price, "mom_20": r.mom_20, "up_days": r.up_days,
             "signal": r.signal, "trade_date": r.trade_date.isoformat()}
            for r in seen.values()]


# ---------- 技术信号与新闻 ----------

@router.get("/signals")
def signals(trade_date: str | None = None, signal_type: str | None = None,
            limit: int = 100, user_id: int = Depends(current_user_id),
            db: Session = Depends(get_db)):
    limit = min(max(limit, 1), MAX_LIMIT)
    d = date.fromisoformat(trade_date) if trade_date else None
    rows = technical_signal.latest_signals(db, trade_date=d, signal_type=signal_type,
                                           limit=limit)
    from .models import StockBasic

    names = dict(db.execute(
        select(StockBasic.code, StockBasic.name)).all()) if rows else {}
    return [{"id": r.id, "code": r.code, "name": names.get(r.code, ""),
             "trade_date": r.trade_date.isoformat(),
             "signal_type": r.signal_type, "strength": r.strength,
             "detail": json.loads(r.detail or "{}")} for r in rows]


@router.get("/news")
def news(limit: int = 50, screened_only: bool = True,
         user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    limit = min(max(limit, 1), MAX_LIMIT)
    from ..portfolio.report_service import top_news

    return top_news(db, limit) if screened_only else [
        {"id": r.id, "title": r.title, "summary": r.summary[:200], "category": r.category,
         "sentiment": r.sentiment, "related_industry": r.related_industry,
         "publish_time": r.publish_time.isoformat() if r.publish_time else "",
         "heat": r.heat, "screened": r.screened}
        for r in db.execute(select(NewsItem).order_by(NewsItem.id.desc())
                            .limit(limit)).scalars().all()]


# ---------- 赛道管理 ----------

class TrackIn(BaseModel):
    name: str
    concept_board: str = ""
    industry_index: str = ""
    keywords: str = ""
    enabled: bool = True
    note: str = ""


@router.get("/tracks")
def list_tracks(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = universe_manager.list_tracks(db)
    from sqlalchemy import func

    from ..screener.models import TrackMembership

    counts = dict(db.execute(
        select(TrackMembership.track_id, func.count(TrackMembership.id))
        .group_by(TrackMembership.track_id)).all())
    return [{"id": t.id, "name": t.name, "concept_board": t.concept_board,
             "industry_index": t.industry_index, "keywords": t.keywords,
             "enabled": t.enabled, "note": t.note, "member_count": counts.get(t.id, 0)}
            for t in rows]


@router.post("/tracks")
def create_track(body: TrackIn, analyst: User = Depends(require_analyst),
                 user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    t = Track(**body.model_dump())
    db.add(t)
    db.commit()
    return {"id": t.id}


@router.put("/tracks/{track_id}")
def update_track(track_id: int, body: TrackIn, analyst: User = Depends(require_analyst),
                 user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    t = db.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "赛道不存在")
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    db.commit()
    return {"ok": True}


@router.post("/tracks/{track_id}/sync")
def sync_track(track_id: int, analyst: User = Depends(require_analyst),
               user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    t = db.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "赛道不存在")
    try:
        n = universe_manager.sync_track_members(db, t)
    except Exception:  # noqa: BLE001 东财概念接口被墙时的友好降级
        raise HTTPException(502, "概念板块成分接口暂时不可达(本地网络受限),"
                                 "云端部署后自动恢复;已有成分不受影响")
    return {"added": n}


# ---------- 策略配置与策略卡 ----------

@router.get("/strategy-params")
def get_strategy_params(user_id: int = Depends(current_user_id),
                        db: Session = Depends(get_db)):
    params = strategy_engine.get_params(db)
    return [{"key": k, "value": v,
             "note": strategy_engine.PARAM_NOTE.get(k, "")}
            for k, v in sorted(params.items())]


class ParamsIn(BaseModel):
    params: dict[str, str]


@router.put("/strategy-params")
def set_strategy_params(body: ParamsIn, analyst: User = Depends(require_analyst),
                        user_id: int = Depends(current_user_id),
                        db: Session = Depends(get_db)):
    for k, v in body.params.items():
        if k not in strategy_engine.PARAM_DEFAULTS:
            continue
        row = db.get(StrategyParams, k)
        if row is None:
            db.add(StrategyParams(key=k, value=str(v),
                                  note=strategy_engine.PARAM_NOTE.get(k, "")))
        else:
            row.value = str(v)
    db.commit()
    return {"ok": True}


@router.get("/strategy-cards")
def strategy_cards(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    strategy_engine.ensure_defaults(db)
    rows = db.execute(select(StrategyCard).order_by(StrategyCard.card_code)).scalars().all()
    return [{"id": r.id, "card_code": r.card_code, "title": r.title,
             "status": r.status, "hypothesis": r.hypothesis,
             "evidence": json.loads(r.evidence or "{}"),
             "source_note": r.source_note, "review_note": r.review_note}
            for r in rows]


class CardIn(BaseModel):
    status: str | None = None
    review_note: str | None = None


@router.put("/strategy-cards/{card_id}")
def update_card(card_id: int, body: CardIn, analyst: User = Depends(require_analyst),
                user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    card = db.get(StrategyCard, card_id)
    if card is None:
        raise HTTPException(404, "策略卡不存在")
    if body.status:
        if body.status not in ("unverified", "testing", "verified", "deprecated", "reference"):
            raise HTTPException(400, "非法状态")
        card.status = body.status
    if body.review_note is not None:
        card.review_note = body.review_note
    db.commit()
    return {"ok": True}


# ---------- 任务中心 ----------

@router.get("/tasks")
def task_logs(limit: int = 50, user_id: int = Depends(current_user_id),
              db: Session = Depends(get_db)):
    limit = min(max(limit, 1), MAX_LIMIT)
    rows = db.execute(select(TaskLog).order_by(TaskLog.id.desc()).limit(limit)).scalars().all()
    from datetime import timedelta, timezone

    def _cn(dt):
        return (dt.replace(tzinfo=timezone.utc) +
                timedelta(hours=8)).replace(tzinfo=None).isoformat(sep=" ") if dt else ""

    return [{"id": r.id, "task_name": r.task_name, "run_at": _cn(r.run_at),
             "status": r.status, "detail": r.detail, "duration_ms": r.duration_ms}
            for r in rows]


class TaskRunIn(BaseModel):
    years: int = 5


@router.post("/tasks/{name}/run")
def run_task_api(name: str, body: TaskRunIn | None = None,
                 analyst: User = Depends(require_analyst),
                 user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    if name not in TASKS:
        raise HTTPException(404, f"未知任务 {name}")
    kwargs = {}
    if name == "backfill_history" and body:
        kwargs["years"] = body.years
    # 后台线程执行,请求立即返回;慢任务(行业重试/历史回填)不再阻塞页面
    from ..task_runner import run_task_async

    started = run_task_async(name, **kwargs)
    if not started:
        raise HTTPException(409, f"任务 {name} 正在运行中,请稍后在日志里查看进度")
    return {"queued": True, "task": name,
            "message": "已提交后台执行,请在下方日志查看结果(慢任务可能需要数分钟)"}
