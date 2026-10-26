"""Screener REST API:仪表盘/选股池/行业/信号/新闻/赛道/策略配置/任务。

权限分级:GET 看板=登录即可;写操作(任务/参数/策略卡/赛道/池)=admin 或 analyst。
"""
import json
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..portfolio.models import User
from ..screener import industry_monitor, market_regime, news_monitor, strategy_engine, \
    technical_signal, universe_manager
from ..shared.security import current_user_id, require_analyst
from ..task_runner import TASKS, run_task
from .models import (CommodityPrice, FundFlowSnapshot, IndustryDaily, LhbDaily,
                   MarketState, MarketStat, NewsItem, PoolPerformance,
                   StockPool, StrategyCard, StrategyParams, TaskLog, Track)
from .universe_manager import EXCLUDED_CODE_LIKE

log = logging.getLogger("screener.api")

router = APIRouter(prefix="/api/screener", tags=["screener"])

MAX_LIMIT = 500


def _stock_names(db: Session, codes: set[str] | list[str] | None = None) -> dict[str, str]:
    """股票代码→名称。codes 为空时返回全表(兼容旧调用);传入集合时只查 IN 范围。"""
    from .models import StockBasic

    q = select(StockBasic.code, StockBasic.name)
    if codes is not None:
        codes = list(dict.fromkeys(codes))
        if not codes:
            return {}
        q = q.where(StockBasic.code.in_(codes))
    return dict(db.execute(q).all())


# ---------- 驾驶舱（独立实时缓存，不写数据库） ----------

@router.get("/cockpit/market")
def cockpit_market(user_id: int = Depends(current_user_id)):
    from . import cockpit

    return cockpit.market()


@router.get("/cockpit/news")
def cockpit_news(user_id: int = Depends(current_user_id)):
    from . import cockpit

    return cockpit.news()


# ---------- 仪表盘 ----------

@router.get("/dashboard")
def dashboard(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    state = market_regime.latest_state(db)
    from ..shared import stock_ref

    avoid = industry_monitor.crowd_avoid_industries(db)
    signals = technical_signal.latest_signals(db, limit=50)
    sig_codes = [s.code for s in signals[:20]]
    names = _stock_names(db, sig_codes)
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
            "note": "PM02 仓位分档为待验证草案,不作投资建议" if state else "",
            "pm02_status": "unverified_draft" if state else "",
        } if state else None,
        "pool_perf": stock_ref.pool_perf_summary(db),
        "crowd_avoid": avoid,
        "market_wide_crowded": len(avoid) > industry_monitor.AVOID_MAX,
        "rising_commodities": industry_monitor.rising_commodities(db),
        "signals": [{"code": s.code, "name": names.get(s.code, ""),
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
                                StockPool.pool_type == pool_type,
                                StockPool.code.not_like(EXCLUDED_CODE_LIKE))
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
    rows = db.execute(select(StockPool).where(
        StockPool.status == status, StockPool.code.not_like(EXCLUDED_CODE_LIKE))
        .order_by(StockPool.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "code": r.code, "name": r.name, "pool_type": r.pool_type,
             "in_price": r.in_price, "trade_date": r.trade_date.isoformat(),
             "out_date": r.out_date.isoformat() if r.out_date else "",
             "out_reason": r.out_reason, "score": r.score} for r in rows]


@router.get("/pool/performance")
def pool_perf(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    from ..shared import stock_ref

    rows = db.execute(select(PoolPerformance).where(
        PoolPerformance.code.not_like(EXCLUDED_CODE_LIKE)).order_by(
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
        try:
            d = date.fromisoformat(trade_date)
        except ValueError:
            raise HTTPException(422, f"日期参数非法:{trade_date}(需 YYYY-MM-DD)")
        q = q.where(IndustryDaily.trade_date == d)
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


@router.get("/indexes")
def indexes(codes: str | None = None,
            user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    """指数条:读 scr_index_daily 每 code 最新行;codes 为空返回默认6个;无数据 [] 不 500。"""
    from ..shared import data_gateway as dg

    from .models import IndexDaily

    try:
        want = ([c.strip() for c in codes.split(",") if c.strip()] if codes
                else [c for c, _ in dg.INDEX_DISPLAY])
        want = want[:20]  # 上限:防一次传上千 code 逐个查询
        out = []
        for code in want:
            r = db.execute(select(IndexDaily).where(IndexDaily.code == code)
                           .order_by(IndexDaily.trade_date.desc()).limit(1)
                           ).scalars().first()
            if r is None:
                continue
            out.append({"code": r.code, "name": r.name,
                        "trade_date": r.trade_date.isoformat() if r.trade_date else "",
                        "close": r.close, "pct_chg": r.pct_chg,
                        "amount": r.amount, "source": r.source})
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("indexes query failed")
        raise HTTPException(503, "指数数据查询失败，数据库服务暂时不可用，请稍后重试") from exc


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


# ---------- 板块资金流与大盘统计(P1-1/P1-2:盘中快照读接口,无数据 [] / {} 不 500) ----------

@router.get("/fund-flow")
def fund_flow(board_type: str = "industry", limit: int = 20,
              user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    """返回最近 snap_time 截面 [{board_name, net_inflow, snap_time, source}]。"""
    from sqlalchemy import func

    limit = min(max(limit, 1), MAX_LIMIT)
    if board_type not in ("industry", "concept"):
        raise HTTPException(400, "board_type 须为 industry|concept")
    try:
        latest = db.execute(select(func.max(FundFlowSnapshot.snap_time)).where(
            FundFlowSnapshot.board_type == board_type)).scalar_one_or_none()
        if latest is None:
            return []
        rows = db.execute(select(FundFlowSnapshot).where(
            FundFlowSnapshot.board_type == board_type,
            FundFlowSnapshot.snap_time == latest).order_by(
            FundFlowSnapshot.net_inflow.desc()).limit(limit)).scalars().all()
        return [{"board_name": r.board_name, "board_code": r.board_code,
                 "net_inflow": r.net_inflow,
                 "snap_time": r.snap_time.isoformat() if r.snap_time else "",
                 "source": r.source} for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.exception("fund flow query failed")
        raise HTTPException(503, "板块资金流查询失败，数据库服务暂时不可用，请稍后重试") from exc


@router.get("/fund-flow/history")
def fund_flow_history(board_name: str = "", board_type: str = "industry",
                      board_code: str | None = None, days: int = 30,
                      user_id: int = Depends(current_user_id),
                      db: Session = Depends(get_db)):
    """该板块近 N 天每日最后一条 [(snap_date, net_inflow)] 供趋势图。

    优先按 board_code 查(板块名可重复/会改名),老数据无 code 时回退按 name。
    """
    from sqlalchemy import func

    if board_type not in ("industry", "concept"):
        raise HTTPException(400, "board_type 须为 industry|concept")
    if not board_name and not board_code:
        raise HTTPException(400, "board_name 或 board_code 至少提供一个")
    try:
        days = min(max(days, 1), 365)
        cond = [FundFlowSnapshot.board_type == board_type]
        if board_code:
            cond.append(FundFlowSnapshot.board_code == board_code)
        else:
            cond.append(FundFlowSnapshot.board_name == board_name)
        # 每日最后一条:按日期分组取 max(snap_time),再回查
        sub = (select(func.date(FundFlowSnapshot.snap_time).label("d"),
                      func.max(FundFlowSnapshot.snap_time).label("mt"))
               .where(*cond)
               .group_by(func.date(FundFlowSnapshot.snap_time))
               .order_by(func.date(FundFlowSnapshot.snap_time).desc())
               .limit(days).subquery())
        rows = db.execute(select(FundFlowSnapshot).where(
            *cond, FundFlowSnapshot.snap_time.in_(select(sub.c.mt)))
            .order_by(FundFlowSnapshot.snap_time)).scalars().all()
        return [{"snap_date": (r.snap_time.date().isoformat()
                                if r.snap_time else ""),
                 "net_inflow": r.net_inflow} for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.exception("fund flow history query failed")
        raise HTTPException(503, "板块资金流历史查询失败，数据库服务暂时不可用，请稍后重试") from exc


@router.get("/market-stat/latest")
def market_stat_latest(user_id: int = Depends(current_user_id),
                       db: Session = Depends(get_db)):
    """最近一行大盘统计;无数据 {} 不 500。"""
    try:
        r = db.execute(select(MarketStat).order_by(
            MarketStat.snap_time.desc()).limit(1)).scalars().first()
        if r is None:
            return {}
        return {"snap_time": r.snap_time.isoformat() if r.snap_time else "",
                "advance": r.advance, "decline": r.decline,
                "limit_up": r.limit_up, "limit_down": r.limit_down,
                "amount_total": r.amount_total, "source": r.source,
                "source_note": r.source_note}
    except Exception as exc:  # noqa: BLE001
        log.exception("market stat query failed")
        raise HTTPException(503, "大盘统计查询失败，数据库服务暂时不可用，请稍后重试") from exc


# ---------- 技术信号与新闻 ----------

@router.get("/lhb")
def lhb(trade_date: str | None = None, limit: int = 50,
        user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    """最近交易日龙虎榜 [{code,name,close,pct_chg,lhb_net,matched_traders}];无数据 [] 不 500。"""
    limit = min(max(limit, 1), MAX_LIMIT)
    # 日期参数在 try 外解析:非法日期返回 422,不与被吞成空数据的查询异常混在一起
    if trade_date:
        try:
            d = date.fromisoformat(trade_date)
        except ValueError:
            raise HTTPException(422, f"日期参数非法:{trade_date}(需 YYYY-MM-DD)")
    else:
        d = None
    try:
        q = select(LhbDaily)
        if d:
            q = q.where(LhbDaily.trade_date == d)
        else:
            latest = db.execute(select(LhbDaily.trade_date).order_by(
                LhbDaily.trade_date.desc()).limit(1)).scalar_one_or_none()
            if latest is None:
                return []
            q = q.where(LhbDaily.trade_date == latest)
        rows = db.execute(q.order_by(LhbDaily.lhb_net.desc()).limit(limit)).scalars().all()
        return [{"code": r.code, "name": r.name, "close": r.close,
                 "pct_chg": r.pct_chg, "lhb_net": r.lhb_net,
                 "matched_traders": json.loads(r.matched_traders or "[]")}
                for r in rows]
    except Exception:  # noqa: BLE001 无数据/老库缺表一律 [] 不 500
        return []


@router.get("/hot-money-seats")
def hot_money_seats(user_id: int = Depends(current_user_id),
                    db: Session = Depends(get_db)):
    """游资席位映射表 [{seat,trader,style,risk}]。"""
    from ..shared import data_gateway as dg

    return [{"seat": seat, **info} for seat, info in dg.load_hot_money_seats().items()]


@router.get("/search")
def search(q: str = "", limit: int = 20,
           user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    """拼音搜股:q 匹配 code 前缀/name 包含/pinyin 前缀/initials 前缀(大小写不敏感)。

    q 为空返回 [];返回 [{code,name}]。"""
    limit = min(max(limit, 1), 100)
    if not (q or "").strip():
        return []
    from ..shared import search as search_mod

    return search_mod.match_search(search_mod.get_cached_index(db), q, limit)


@router.get("/signals")
def signals(trade_date: str | None = None, signal_type: str | None = None,
            limit: int = 100, user_id: int = Depends(current_user_id),
            db: Session = Depends(get_db)):
    limit = min(max(limit, 1), MAX_LIMIT)
    if trade_date:
        try:
            d = date.fromisoformat(trade_date)
        except ValueError:
            raise HTTPException(422, f"日期参数非法:{trade_date}(需 YYYY-MM-DD)")
    else:
        d = None
    rows = technical_signal.latest_signals(db, trade_date=d, signal_type=signal_type,
                                           limit=limit)
    names = _stock_names(db, [r.code for r in rows]) if rows else {}
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

    def _track_dict(t) -> dict:
        # 老库缺列(未迁移)时 getattr 兜底,不 500。
        synced = getattr(t, "synced_at", None)
        return {"id": t.id, "name": t.name, "concept_board": t.concept_board,
                "industry_index": t.industry_index, "keywords": t.keywords,
                "enabled": t.enabled, "note": t.note,
                "member_count": counts.get(t.id, 0),
                "member_source": getattr(t, "member_source", "") or "",
                "synced_at": synced.isoformat() if synced else "",
                "sync_error": getattr(t, "sync_error", "") or ""}
    return [_track_dict(t) for t in rows]


@router.post("/tracks")
def create_track(body: TrackIn, analyst: User = Depends(require_analyst),
                 user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    dup = db.execute(select(Track).where(Track.name == body.name)).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(400, f"赛道名「{body.name}」已存在")
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
    dup = db.execute(select(Track).where(
        Track.name == body.name, Track.id != track_id)).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(400, f"赛道名「{body.name}」已存在")
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    db.commit()
    return {"ok": True}


@router.post("/tracks/{track_id}/sync")
def sync_track(track_id: int, analyst: User = Depends(require_analyst),
               user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    """同步单赛道成分,source 中文:eastmoney 东财/sina 新浪/keyword 关键词/
    ths 同花顺核验/local 本地展开/local_ths_verified 同花顺核验。"""
    t = db.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "赛道不存在")
    try:
        n = universe_manager.sync_track_members(db, t)
        source = getattr(t, "member_source", "") or ""
    except ConnectionError as exc:
        # 上游网络/被墙:本地错误不冒充 502 网关错误,转 503 并标注可恢复
        raise HTTPException(503, f"概念板块成分接口暂时不可达(本地网络受限):{exc};"
                                 "云端部署后自动恢复;已有成分不受影响")
    except TimeoutError as exc:
        raise HTTPException(503, f"概念板块成分接口超时:{exc};已有成分不受影响,稍后重试")
    except ValueError as exc:
        # 参数/数据错误是本地 4xx,不走 502
        raise HTTPException(422, f"概念板块参数或数据异常:{exc}")
    except Exception as exc:  # noqa: BLE001 未知本地错误:500,不冒充网关 502
        raise HTTPException(500, f"赛道同步本地失败:{exc}")
    return {"added": n, "source": source}


@router.post("/tracks/backfill")
def backfill_tracks(analyst: User = Depends(require_analyst),
                    user_id: int = Depends(current_user_id),
                    db: Session = Depends(get_db)):
    """一键回填全部赛道成分(ensure_default_tracks + 逐 track sync)。

    后台线程执行(7 赛道×多源×重试最坏可达分钟级,同步执行会占死请求线程,
    且绕过任务互斥/日志):走 run_task_async 与任务中心同口径,结果看任务日志
    (task=tracks_backfill)与赛道管理列表。已在运行返回 409。"""
    from ..task_runner import run_task_async

    started = run_task_async("tracks_backfill")
    if not started:
        raise HTTPException(409, "赛道回填已在运行中,请在任务日志查看进度")
    return {"queued": True, "task": "tracks_backfill",
            "message": "已提交后台执行,完成后赛道列表自动更新;慢网络下可能需数分钟"}


@router.post("/tracks/{track_id}/prune")
def prune_track(track_id: int, analyst: User = Depends(require_analyst),
                user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    """清理赛道过期成分(sync 只增不减的补位;需人工确认后调用)。"""
    t = db.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "赛道不存在")
    try:
        n = universe_manager.prune_track_members(db, t)
    except universe_manager.DegradedSourceError as exc:
        # 成分源处于降级链(ths/sina/keyword):差集清理不可信,409 冲突提示稍后再试
        raise HTTPException(409, str(exc))
    except ConnectionError as exc:
        raise HTTPException(503, f"概念板块成分接口暂时不可达:{exc}")
    except TimeoutError as exc:
        raise HTTPException(503, f"概念板块成分接口超时:{exc}")
    except ValueError as exc:
        raise HTTPException(422, f"概念板块参数或数据异常:{exc}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"赛道清理本地失败:{exc}")
    return {"removed": n}


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
    # 先全量校验再落库:任一非法值整体拒绝,避免半写入留下坏参数
    normalized: dict[str, str] = {}
    errors: list[str] = []
    for k, v in body.params.items():
        if k not in strategy_engine.PARAM_DEFAULTS:
            continue
        try:
            normalized[k] = strategy_engine.validate_param(k, v)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise HTTPException(422, "; ".join(errors))
    for k, v in normalized.items():
        row = db.get(StrategyParams, k)
        if row is None:
            db.add(StrategyParams(key=k, value=v,
                                  note=strategy_engine.PARAM_NOTE.get(k, "")))
        else:
            row.value = v
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


def _evidence_empty(card: StrategyCard) -> bool:
    """证据链是否为空:evidence 须为含非空值的 JSON 对象(空 {} 视为未填)。"""
    try:
        ev = json.loads(card.evidence or "{}")
    except (TypeError, ValueError):
        return False  # 非 JSON(历史脏数据)不当作空,避免误拦已是文本证据的卡
    if not isinstance(ev, dict):
        return False
    return not any(str(v).strip() for v in ev.values())


@router.put("/strategy-cards/{card_id}")
def update_card(card_id: int, body: CardIn, analyst: User = Depends(require_analyst),
                user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    card = db.get(StrategyCard, card_id)
    if card is None:
        raise HTTPException(404, "策略卡不存在")
    if body.status:
        if body.status not in ("unverified", "testing", "verified", "deprecated", "reference"):
            raise HTTPException(400, "非法状态")
        # 证据链必填:禁止无回测证据直接置 verified(README 红线)
        if body.status == "verified" and _evidence_empty(card):
            raise HTTPException(422, "证据链为空:置为 verified 前需先补齐回测证据(evidence)")
        card.status = body.status
    if body.review_note is not None:
        card.review_note = body.review_note
    db.commit()
    return {"ok": True}


# ---------- 任务中心 ----------

@router.get("/tasks")
def task_logs(limit: int = 50, analyst: User = Depends(require_analyst),
              db: Session = Depends(get_db)):
    limit = min(max(limit, 1), MAX_LIMIT)
    rows = db.execute(select(TaskLog).order_by(TaskLog.id.desc()).limit(limit)).scalars().all()
    from datetime import timedelta, timezone

    def _cn(dt):
        return (dt.replace(tzinfo=timezone.utc) +
                timedelta(hours=8)).replace(tzinfo=None).isoformat(sep=" ") if dt else ""

    def _quality(detail: str) -> dict:
        # data_quality/coverage:已是后台线程执行,日志行携带质量字段供前端区分
        # success 且 detail 含 degraded/部分完成 → degraded;failed → failed;其余 ok
        d = detail or ""
        if "degraded" in d or "部分完成" in d:
            return {"data_quality": "degraded", "coverage": {"note": d[:200]}}
        return {"data_quality": "ok", "coverage": {}}

    return [{"id": r.id, "task_name": r.task_name, "run_at": _cn(r.run_at),
             "status": r.status, "detail": r.detail, "duration_ms": r.duration_ms,
             **_quality(r.detail)}
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
