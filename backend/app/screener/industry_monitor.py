"""行业与赛道监测引擎。

含 SC-U08 板块拥挤度反转(唯一带回测证据的策略卡):
  RATIO = 行业成交额/全市场成交额×100
  RATIO_SCORE = (RATIO - MA120(RATIO)) / MA120(RATIO) × 100
  RATIO_SCORE > 300 → 该行业列入 20 交易日回避名单(禁止新增持仓)
以及商品涨价周期跟踪(周期性 vs 短期波动:持续≥1月才标记启动有效)。
"""
import json
import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..shared import data_gateway as dg
from .models import CommodityPrice, IndustryDaily, StrategyCard, Track

log = logging.getLogger("industry_monitor")

CROWD_THRESHOLD = 300.0       # SC-U08 参数
CROWD_COOLDOWN_TRADE_DAYS = 20
AVOID_MAX = 3                 # 全市场同时拥挤>3个行业 → 整体降仓信号


# ---------------- SC-U08 拥挤度 ----------------

def compute_ratio_score(ratios: pd.Series) -> pd.Series:
    """输入按日升序的 RATIO 序列,输出 RATIO_SCORE(偏离自身 MA120 的百分比)。"""
    ma120 = ratios.rolling(120, min_periods=60).mean()
    return (ratios - ma120) / ma120 * 100


def _add_trade_days(days_list: list, from_idx: int, n: int) -> date | None:
    if from_idx >= 0 and from_idx + n < len(days_list):
        return days_list[from_idx + n]
    return None


def _cooldown_until(start: date, n: int = CROWD_COOLDOWN_TRADE_DAYS) -> date:
    """回避到期日:优先交易日历 +n 个交易日;日历不可用时退化为 +30 自然日。

    退化路径仅在上游日历接口失败时触发,调用方应在日志/卡片中标注口径。
    """
    try:
        cal = dg.get_trade_calendar(start.strftime("%Y%m%d"),
                                    (start + timedelta(days=90)).strftime("%Y%m%d"))
        later = [d for d in cal if d > start]
        if len(later) >= n:
            return later[n - 1]
    except Exception as exc:  # noqa: BLE001 日历失败则自然日兜底
        log.warning("trade calendar unavailable, cooldown fallback +30d: %s", exc)
    return start + timedelta(days=30)


def sync_industry_daily(db: Session, trade_date: date | None = None,
                        board_limit: int | None = None) -> int:
    """同步东财行业板块日度监测(收盘后任务)。trade_date 缺省为最近已收盘交易日。"""
    trade_date = trade_date or dg.completed_trade_day()
    start = (trade_date - timedelta(days=240)).strftime("%Y%m%d")

    boards = dg.get_industry_board_list()
    if board_limit:
        boards = boards.head(board_limit)

    # 先探测东财行业历史接口;被墙时降级为新浪快照模式——
    # 降级路径不依赖指数日线(避免其重试拖慢),分母改用行业成交额合计(口径自洽)
    hist_ok = True
    if len(boards):
        try:
            dg.get_industry_hist(str(boards.iloc[0]["board_name"]), start)
        except Exception as exc:  # noqa: BLE001
            hist_ok = False
            log.warning("industry hist unavailable (%s); degrade to spot-only mode",
                        str(exc)[:80])
    if not hist_ok:
        return _sync_industry_spot_only(db, trade_date)

    try:
        market_amount = dg.get_market_total_amount(start).set_index("trade_date")["amount"]
    except Exception as exc:  # noqa: BLE001
        log.warning("market total amount failed: %s", exc)
        market_amount = None
    if market_amount is None:
        return _sync_industry_spot_only(db, trade_date)

    flow_map: dict[str, float] = {}
    try:
        flow = dg.get_industry_fund_flow()
        flow_map = dict(zip(flow["board_name"], flow["fund_flow"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("industry fund flow failed: %s", exc)

    trade_days_sorted = sorted(market_amount.index)
    written = 0
    consecutive_fail = 0
    for _, brow in boards.iterrows():
        board_name, board_code = str(brow["board_name"]), str(brow["board_code"])
        try:
            hist = dg.get_industry_hist(board_name, start)
            consecutive_fail = 0
        except Exception as exc:  # noqa: BLE001 单板块失败不阻断
            consecutive_fail += 1
            log.warning("industry hist %s failed (%d): %s", board_name, consecutive_fail, exc)
            if consecutive_fail >= 5:
                log.warning("industry hist 5 consecutive failures, abort remaining boards")
                break
            continue
        hist = hist[hist["trade_date"].isin(market_amount.index)]
        if hist.empty:
            continue
        hist = hist.merge(market_amount.rename("market_amount"), on="trade_date", how="left")
        hist["ratio"] = hist["amount"] / hist["market_amount"] * 100
        hist["ratio_score"] = compute_ratio_score(hist["ratio"])
        last = hist.iloc[-1]

        row = db.execute(select(IndustryDaily).where(
            IndustryDaily.industry_code == board_code,
            IndustryDaily.trade_date == trade_date)).scalar_one_or_none()
        if row is None:
            row = IndustryDaily(industry_code=board_code, industry_name=board_name,
                                trade_date=trade_date)

        # 回避名单:触发→20交易日后解除;已生效则沿用 max(旧值,新触发)。
        # 旧值必须跨日继承:新建当日行时 row.avoid_until 必为空,须回查最近一条历史行;
        # 同日重跑则当日行已有值,两者取 max 保证幂等。
        prev_row = db.execute(select(IndustryDaily).where(
            IndustryDaily.industry_code == board_code,
            IndustryDaily.trade_date < trade_date,
        ).order_by(IndustryDaily.trade_date.desc()).limit(1)).scalar_one_or_none()
        carried_dates = [d for d in (
            row.avoid_until if row else None,
            prev_row.avoid_until if prev_row else None,
        ) if d and d >= trade_date]
        avoid_until = max(carried_dates) if carried_dates else None
        if last["ratio_score"] > CROWD_THRESHOLD:
            idx = trade_days_sorted.index(last["trade_date"]) if last["trade_date"] in trade_days_sorted else -1
            # 口径统一:优先交易日历 +20 交易日,日历缺失退化 +30 自然日(见 _cooldown_until)。
            fresh = _add_trade_days(trade_days_sorted, idx, CROWD_COOLDOWN_TRADE_DAYS)
            if fresh is None and idx >= 0:
                fresh = _cooldown_until(last["trade_date"])
            elif fresh is None:
                fresh = trade_date + timedelta(days=30)
            candidates = [d for d in (avoid_until, fresh) if d is not None]
            avoid_until = max(candidates) if candidates else None

        mom20 = (last["close"] / hist["close"].iloc[-21] - 1) * 100 if len(hist) > 21 else 0.0
        mom60 = (last["close"] / hist["close"].iloc[-61] - 1) * 100 if len(hist) > 61 else 0.0

        # 连续净流入天数(基于历史落库记录回溯)
        prev_rows = db.execute(select(IndustryDaily).where(
            IndustryDaily.industry_code == board_code,
            IndustryDaily.trade_date < trade_date,
        ).order_by(IndustryDaily.trade_date.desc()).limit(30)).scalars().all()
        today_flow = flow_map.get(board_name, 0.0)
        series = sorted(
            ((r.trade_date, r.fund_flow or 0.0) for r in prev_rows),
            key=lambda x: x[0])
        streak = 0
        if today_flow > 0:
            streak = 1
            for _d, f in reversed(series):
                if f > 0:
                    streak += 1
                else:
                    break

        row.close = float(last["close"])
        row.pct_chg = float(last["pct_chg"])
        row.amount = float(last["amount"])
        row.market_amount = float(last["market_amount"])
        row.ratio = round(float(last["ratio"]), 4)
        row.ratio_ma120 = round(float(hist["ratio"].rolling(120, min_periods=60).mean().iloc[-1]), 4)
        row.ratio_score = round(float(last["ratio_score"]), 2) if pd.notna(last["ratio_score"]) else 0.0
        row.avoid_until = avoid_until
        row.fund_flow = round(today_flow, 2)
        row.flow_streak = streak
        row.mom_20 = round(float(mom20), 2)
        row.mom_60 = round(float(mom60), 2)
        db.merge(row)
        written += 1
    db.commit()

    _apply_heat_scores(db, trade_date)
    _update_crowd_card(db, db.execute(select(IndustryDaily).where(
        IndustryDaily.trade_date == trade_date)).scalars().all())
    log.info("industry daily synced: %s boards @ %s", written, trade_date)
    return written


def _sync_industry_spot_only(db: Session, trade_date: date) -> int:
    """降级模式:新浪行业快照写当日行(东财历史接口不可用时)。

    mom/ratio_score 从库内已积累的 IndustryDaily 历史滚动计算——
    降级首日指标为空,数据逐日增厚,不阻塞热力图可用性。
    分母用同源的行业成交额合计,保证 RATIO 时间序列口径稳定(SC-U08 只看相对偏离)。
    """
    spot = dg.get_industry_spot_sina()
    mkt_today = float(spot["amount"].sum()) if len(spot) else 0.0
    written = 0
    for _, brow in spot.iterrows():
        if pd.isna(brow["close"]):
            continue
        code, name = str(brow["board_code"]), str(brow["board_name"])
        hist_rows = db.execute(select(IndustryDaily).where(
            IndustryDaily.industry_code == code).order_by(
            IndustryDaily.trade_date.desc()).limit(121)).scalars().all()
        hist_asc = sorted(hist_rows, key=lambda r: r.trade_date)

        row = db.execute(select(IndustryDaily).where(
            IndustryDaily.industry_code == code,
            IndustryDaily.trade_date == trade_date)).scalar_one_or_none()
        if row is None:
            row = IndustryDaily(industry_code=code, industry_name=name, trade_date=trade_date)
        row.close = float(brow["close"])
        row.pct_chg = float(brow["pct_chg"]) if pd.notna(brow["pct_chg"]) else 0.0
        row.amount = float(brow["amount"]) if pd.notna(brow["amount"]) else 0.0
        row.market_amount = mkt_today
        row.ratio = round(row.amount / mkt_today * 100, 4) if mkt_today else 0.0
        row.fund_flow = 0.0

        # 回避名单跨日继承:新建当日行时 row.avoid_until 为空,须回查最近一条历史行;
        # 同日重跑取当日已写值,二者取 max 保证幂等。
        prev_row = next((r for r in hist_rows if r.trade_date < trade_date), None)
        carried_dates = [d for d in (
            row.avoid_until if row else None,
            prev_row.avoid_until if prev_row else None,
        ) if d and d >= trade_date]
        carried = max(carried_dates) if carried_dates else None
        row.avoid_until = carried

        ratios = pd.Series([r.ratio for r in hist_asc] + [row.ratio])
        if len(ratios) >= 60:
            # 含当日在内的 MA120:与 full 路径同口径(分母皆为当日成交占比 RATIO)
            ma120 = ratios.rolling(120, min_periods=60).mean().iloc[-1]
            if pd.notna(ma120) and ma120 > 0:
                row.ratio_ma120 = round(float(ma120), 4)
                score = (ratios.iloc[-1] - ma120) / ma120 * 100
                row.ratio_score = round(float(score), 2)
                if score > CROWD_THRESHOLD:
                    fresh = _cooldown_until(trade_date)
                    row.avoid_until = max([d for d in (carried, fresh) if d is not None])
            elif pd.notna(ma120):
                row.ratio_ma120 = round(float(ma120), 4)
        closes = [r.close for r in hist_asc] + [row.close]
        if len(closes) > 21 and closes[-21]:
            row.mom_20 = round((closes[-1] / closes[-21] - 1) * 100, 2)
        if len(closes) > 61 and closes[-61]:
            row.mom_60 = round((closes[-1] / closes[-61] - 1) * 100, 2)
        db.merge(row)
        written += 1
    db.commit()
    _apply_heat_scores(db, trade_date)
    _update_crowd_card(db, db.execute(select(IndustryDaily).where(
        IndustryDaily.trade_date == trade_date)).scalars().all())
    log.info("industry daily (spot-only degraded): %s boards @ %s", written, trade_date)
    return written


# ---------------- 新兴赛道雷达 ----------------

def _expected_trade_day(db: Session) -> date:
    """应为的最新数据日:优先取指数日线的最近数据日(同一条收盘流水线),
    无指数数据时按工作日 16:00 粗略推断(纯本地判断,不在请求里访问网络)。"""
    from .models import IndexDaily

    latest_idx = db.execute(select(IndexDaily.trade_date).order_by(
        IndexDaily.trade_date.desc()).limit(1)).scalar_one_or_none()
    if latest_idx is not None:
        return latest_idx
    now = dg.now_tz()
    d = now.date()
    if now.hour < 16 or d.weekday() >= 5:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def industry_summary(db: Session) -> dict:
    """行业截面元信息:数据日、覆盖度、历史积累天数、回避名单数。

    降级路径(东财行业日线不可用→新浪行业快照)会把动量/拥挤度/资金写成 0,
    直接渲染会与「真实 0」混淆:前端据此把对应列显示为「积累中/-」。
    """
    latest = db.execute(select(IndustryDaily.trade_date).order_by(
        IndustryDaily.trade_date.desc()).limit(1)).scalar_one_or_none()
    expected = _expected_trade_day(db)
    history_days = int(db.execute(
        select(func.count(func.distinct(IndustryDaily.trade_date)))).scalar_one() or 0)
    if latest is None:
        return {"as_of": "", "rows": 0, "history_days": 0, "avoid_count": 0,
                "expected": expected.isoformat(), "stale": False, "heat_mode": "empty",
                "coverage": {"momentum": False, "flow": False,
                             "crowding": False, "valuation": False}}
    rows = db.execute(select(IndustryDaily).where(
        IndustryDaily.trade_date == latest)).scalars().all()
    coverage = {
        "momentum": any(abs(r.mom_20 or 0) > 1e-9 or abs(r.mom_60 or 0) > 1e-9 for r in rows),
        "flow": any(abs(r.fund_flow or 0) > 1e-9 for r in rows),
        "crowding": any(abs(r.ratio_score or 0) > 1e-9 for r in rows),
        "valuation": len({r.pe_pct for r in rows}) > 1,
    }
    # heat 口径:无动量时 _apply_heat_scores 用涨跌幅+成交占比兜底(degraded);
    # 有动量但无 PE 分位时估值维度被降权(partial)。
    heat_mode = "degraded" if not coverage["momentum"] else (
        "ok" if coverage["valuation"] else "partial")
    return {
        "as_of": latest.isoformat(), "rows": len(rows),
        "history_days": history_days,
        "avoid_count": sum(1 for r in rows
                           if r.avoid_until is not None and r.avoid_until >= latest),
        "expected": expected.isoformat(),
        "stale": latest < expected,
        "heat_mode": heat_mode,
        "coverage": coverage,
    }


def emerging_tracks(db: Session, top: int = 12) -> list[dict]:
    """新兴赛道发现:优先「启动期」(低位 + 动量转正 + 未拥挤),而非已经涨完的高位行业。

    口径(识别而非预测,全部截面相对值;统一 20 交易日口径,自然日仅用于新闻回看窗):
    - 启动期: mom60 低于全行业中位数(尚未大涨) 且 mom20>0(近期转强) 且未在回避名单
    - 发酵期: mom20 进入前 30%(强动量) 且未拥挤
    - 回避:   ratio_score≥300 或已在回避名单(SC-U08 兑现期)——排序硬置底,不参与启动/发酵
    - 数据缺失(如降级首日 mom 全空):不伪造动量,mom 置空并标 data_validity=low
    新闻突增系数 = 近3日日均 / 前7日日均(等速率口径,上界截断),≥2 视为显著。
    排序分 = 0.4×阶段(启动=1,发酵=0.6) + 0.35×动量加速分位 + 0.25×min(新闻突增/4,1)。
    """
    latest = db.execute(select(IndustryDaily.trade_date).order_by(
        IndustryDaily.trade_date.desc()).limit(1)).scalar_one_or_none()
    if latest is None:
        return []
    rows = db.execute(select(IndustryDaily).where(
        IndustryDaily.trade_date == latest)).scalars().all()
    if not rows:
        return []
    df = pd.DataFrame([{
        "code": r.industry_code, "name": r.industry_name,
        "mom20": r.mom_20 or 0.0, "mom60": r.mom_60 or 0.0,
        "ratio_score": r.ratio_score or 0.0,
        "avoid": r.avoid_until is not None and r.avoid_until >= latest,
        "pct_chg": r.pct_chg or 0.0,
    } for r in rows])
    # 降级首日无历史动量时:不伪造动量。mom 字段置空、下游标 data_validity=low,
    # 阶段一律判 watch,待数据增厚后自动切换完整公式(原 pct_chg 冒充 mom 已删除)。
    data_validity = "ok"
    first_day = df["mom20"].abs().sum() == 0 and df["mom60"].abs().sum() == 0
    if first_day:
        data_validity = "low:首日快照,动量缺失"
    df["accel"] = df["mom20"] - df["mom60"]
    med60 = df["mom60"].median()
    top30 = df["mom20"].quantile(0.7)

    # 新闻热度突增(近3日 vs 前7日):按行业名关键词包含匹配
    # (新闻打的是赛道/概念标签,与行业分类名不完全一致,包含匹配才能对上)
    from datetime import datetime, time as dtime

    from .models import NewsItem

    recent_from = datetime.combine(latest - timedelta(days=3), dtime.min)
    base_from = datetime.combine(latest - timedelta(days=10), dtime.min)
    base_to = datetime.combine(latest - timedelta(days=3), dtime.min)
    # NOTE:新闻回看窗用自然日(发布时间属性,非交易信号);动量/回避统一用交易日历口径。
    news_rows = db.execute(
        select(NewsItem.related_industry, NewsItem.title, NewsItem.publish_time)
        .where(NewsItem.publish_time >= base_from)).all()
    keys = {r["name"]: str(r["name"]).replace("行业", "") for _, r in df.iterrows()}
    recent: dict[str, int] = {}
    base: dict[str, int] = {}
    for rel, title, pt in news_rows:
        if not pt:
            continue
        text = f"{rel or ''}|{title or ''}"
        is_recent = pt >= recent_from
        for name, key in keys.items():
            if key and key in text:
                if is_recent:
                    recent[name] = recent.get(name, 0) + 1
                elif pt < base_to:
                    base[name] = base.get(name, 0) + 1

    def _stage(r):
        if r["avoid"] or r["ratio_score"] >= CROWD_THRESHOLD:
            return "avoid", "拥挤兑现(SC-U08 回避)"
        if first_day:
            return "watch", "观察·首日快照(动量缺失)"
        if r["mom60"] < med60 and r["mom20"] > 0:
            return "starting", "低位转强(启动期)"
        if r["mom20"] >= top30:
            return "rising", "强动量(发酵期)"
        return "watch", "观察"

    out = []
    for _, r in df.iterrows():
        stage, stage_text = _stage(r)
        news3 = float(recent.get(r["name"], 0))
        news3_rate = news3 / 3.0
        news7 = float(base.get(r["name"], 0))  # base 窗为严格前 7 天(见 base_from/base_to)
        news7_rate = news7 / 7.0
        surge = min((news3_rate + 0.1) / (news7_rate + 0.1), 10.0)
        out.append({
            "code": r["code"], "name": r["name"], "stage": stage, "stage_text": stage_text,
            "mom_20": round(r["mom20"], 2), "mom_60": round(r["mom60"], 2),
            "accel": round(r["accel"], 2),
            "ratio_score": round(r["ratio_score"], 1),
            "news_3d": int(news3), "news_surge": round(surge, 2),
            "data_validity": data_validity,
        })
    odf = pd.DataFrame(out)
    odf["accel_pct"] = odf["accel"].rank(pct=True)
    odf["stage_w"] = odf["stage"].map({"starting": 1.0, "rising": 0.6,
                                       "watch": 0.2, "avoid": 0.0})
    odf["score"] = (0.4 * odf["stage_w"] + 0.35 * odf["accel_pct"]
                    + 0.25 * (odf["news_surge"] / 4).clip(upper=1.0))
    # avoid 硬置底:回避行业不参与启动/发酵排序,直接沉底(权重为0不够,高动量仍会排前面)。
    odf = odf.sort_values(["stage_w", "score"], ascending=[False, False]).head(top)
    # 保险:若 top 截断后仍混入 avoid(极端拥挤日),将其排到末尾
    avoid_mask = odf["stage"] == "avoid"
    odf = pd.concat([odf[~avoid_mask], odf[avoid_mask]])
    tracked = {t.name for t in db.execute(select(Track)).scalars().all()}
    return [{"name": r["name"], "code": r["code"], "stage": r["stage"],
             "stage_text": r["stage_text"],
             "mom_20": r["mom_20"], "mom_60": r["mom_60"], "accel": r["accel"],
             "ratio_score": r["ratio_score"], "news_3d": r["news_3d"],
             "news_surge": r["news_surge"],
             "score": round(float(r["score"]), 3),
             "data_validity": r["data_validity"], "as_of": latest.isoformat(),
             "tracked": r["name"] in tracked}
            for _, r in odf.iterrows()]


def _apply_heat_scores(db: Session, trade_date: date) -> None:
    """景气综合评分 = 各成分当日截面分位加权(相对口径)。
    0.35*mom60 + 0.25*mom20 + 0.2*flow_streak + 0.2*(100-pe_pct);回避名单减 20 分。

    返回/落库均带 data_quality 标注:degraded 首日用涨跌幅+成交占比兜底,
    与完整公式不可比,调用方(API/看板)须展示该标记。
    """
    rows = db.execute(select(IndustryDaily).where(
        IndustryDaily.trade_date == trade_date)).scalars().all()
    if not rows:
        return
    df = pd.DataFrame([{
        "id": r.id, "mom_20": r.mom_20, "mom_60": r.mom_60, "flow_streak": r.flow_streak,
        "pe_pct": r.pe_pct, "pct_chg": r.pct_chg or 0.0, "ratio": r.ratio or 0.0,
        "avoid": r.avoid_until is not None and r.avoid_until >= trade_date,
    } for r in rows])
    for col in ("mom_20", "mom_60"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if df[col].notna().any():
            df[col] = df[col].fillna(df[col].median())
        else:
            df[col] = 0.0
    df["flow_streak"] = pd.to_numeric(df["flow_streak"], errors="coerce").fillna(0)
    df["pe_pct"] = pd.to_numeric(df["pe_pct"], errors="coerce")
    if df["mom_20"].abs().sum() == 0 and df["mom_60"].abs().sum() == 0:
        # 降级首日无历史动量(截面无区分度):用当日涨跌幅+成交占比兜底,数据积累后自动切换完整公式
        df["heat"] = (0.6 * df["pct_chg"].rank(pct=True) * 100 +
                      0.4 * df["ratio"].rank(pct=True) * 100)
        data_quality = "degraded:首日快照,heat 口径为涨跌幅+成交占比兜底,与完整公式不可比"
    else:
        # IndustryDaily.pe_pct 当前无数据源,库里恒为默认值 0.0:
        # 无区分度(nunique<=1)时估值维度权重归零,避免 20% 权重被常数占满,
        # 同时防止 rank(全 NaN)传染使 heat_score 变成 NaN/None。
        has_pe = df["pe_pct"].nunique(dropna=True) > 1
        if has_pe:
            df["pe_pct"] = df["pe_pct"].fillna(df["pe_pct"].median())
        else:
            df["pe_pct"] = 50.0
        data_quality = "ok" if has_pe else "degraded:行业PE分位缺失,估值维度已降权"
        for col in ("mom_20", "mom_60", "flow_streak", "pe_pct"):
            df[f"{col}_r"] = df[col].rank(pct=True) * 100
        weights = {"mom60": 0.35, "mom20": 0.25, "flow": 0.2, "val": 0.2 if has_pe else 0.0}
        total = sum(weights.values())
        df["heat"] = (weights["mom60"] * df["mom_60_r"] + weights["mom20"] * df["mom_20_r"]
                      + weights["flow"] * df["flow_streak_r"]
                      + weights["val"] * (100 - df["pe_pct_r"])) / total
        df["heat"] = df["heat"].fillna(df["heat"].median())
    df.loc[df["avoid"], "heat"] -= 20
    for _, r in df.iterrows():
        obj = db.get(IndustryDaily, int(r["id"]))
        obj.heat_score = round(float(r["heat"]), 2)
        obj.news_heat = 0.0  # news_heat 字段保留兼容,当前热度即 heat_score,data_quality 见卡片 evidence
    db.commit()
    log.info("heat scores @%s: %s", trade_date, data_quality)
    return data_quality


def _update_crowd_card(db: Session, rows) -> None:
    """把 SC-U08 当日状态写进策略卡(生命周期管理:证据随运行更新)。"""
    card = db.execute(select(StrategyCard).where(
        StrategyCard.card_code == "SC-U08")).scalar_one_or_none()
    if card is None:
        card = StrategyCard(
            card_code="SC-U08", title="板块拥挤度反转(回避)",
            hypothesis="行业成交占比偏离自身MA120超300%后,该行业未来20交易日跑输,回避获相对收益",
            status="unverified",
            source_note="Invest知识库 BT-U08 草案,待回测验证;先按 unverified 执行回避",
        )
    avoid = [r.industry_name for r in rows
             if r.avoid_until is not None and r.avoid_until >= (r.trade_date)]
    card.status = "unverified"
    card.evidence = json.dumps({
        "threshold": CROWD_THRESHOLD, "cooldown_days": CROWD_COOLDOWN_TRADE_DAYS,
        "current_avoid": avoid, "market_wide_crowded": len(avoid) > AVOID_MAX,
        "unverified": True,
        "note": "SC-U08 为待验证草案:回避名单照常执行,但卡片状态标 unverified,不冒充已验证",
    }, ensure_ascii=False)
    card.review_note = ("事件驱动行业(政策/商品暴涨)触发需人工剔除;全市场同时拥挤>3个行业时整体降仓。")
    db.merge(card)
    db.commit()


def crowd_avoid_industries(db: Session, trade_date: date | None = None) -> list[str]:
    """当前回避行业名单(供策略引擎禁止入池)。"""
    trade_date = trade_date or dg.completed_trade_day()
    rows = db.execute(select(IndustryDaily).where(
        IndustryDaily.trade_date == trade_date)).scalars().all()
    return [r.industry_name for r in rows
            if r.avoid_until is not None and r.avoid_until >= trade_date]


def market_wide_crowded(db: Session, trade_date: date | None = None) -> bool:
    return len(crowd_avoid_industries(db, trade_date)) > AVOID_MAX


# ---------------- 商品涨价周期 ----------------

def sync_commodities(db: Session, trade_date: date | None = None) -> int:
    """同步商品主力连续价格并生成涨价信号。持续≥1月(约20交易日)才算周期性启动。"""
    trade_date = trade_date or dg.completed_trade_day()
    written = 0
    for spec in dg.DEFAULT_COMMODITY_SERIES:
        try:
            hist = dg.get_commodity_history(spec["sina_symbol"], days=300)
        except Exception as exc:  # noqa: BLE001
            log.warning("commodity %s failed: %s", spec["series_name"], exc)
            continue
        if len(hist) < 25:
            continue
        close = hist["close"].astype(float).reset_index(drop=True)
        last = hist.iloc[-1]
        mom20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 21 else 0.0
        up_days = 0
        for i in range(len(close) - 1, 0, -1):
            if close.iloc[i] >= close.iloc[i - 1]:
                up_days += 1
            else:
                break
        # 周期性 vs 短期波动:动量>5% 且持续天数≥10 才标「启动/持续」;<-5% 回落
        if mom20 > 5 and up_days >= 10:
            signal = "持续" if up_days > 25 else "启动"
        elif mom20 > 5:
            signal = "观察"  # 方向对但持续时间不足,可能只是短期波动
        elif mom20 < -5:
            signal = "回落"
        else:
            signal = "无"
        row = db.execute(select(CommodityPrice).where(
            CommodityPrice.series_name == spec["series_name"],
            CommodityPrice.trade_date == last["trade_date"])).scalar_one_or_none()
        if row is None:
            row = CommodityPrice(series_name=spec["series_name"],
                                 related_track=spec.get("related_track", ""),
                                 trade_date=last["trade_date"])
        row.price = float(last["close"])
        row.mom_20 = round(float(mom20), 2)
        row.up_days = up_days
        row.signal = signal
        db.merge(row)
        written += 1
    db.commit()
    return written


def rising_commodities(db: Session) -> list[dict]:
    """当前处于启动/持续/观察状态的商品(涨价主题线索)。"""
    rows = db.execute(select(CommodityPrice).order_by(
        CommodityPrice.series_name, CommodityPrice.trade_date.desc())).scalars().all()
    seen: dict[str, CommodityPrice] = {}
    for r in rows:
        seen.setdefault(r.series_name, r)
    return [{"series_name": r.series_name, "related_track": r.related_track,
             "price": r.price, "mom_20": r.mom_20, "up_days": r.up_days,
             "signal": r.signal, "trade_date": r.trade_date.isoformat()}
            for r in seen.values() if r.signal in ("启动", "持续", "观察")]
