"""策略引擎:四层漏斗(H01五维筛 + 估值 + 相对强弱 + 拥挤度回避)→ 总池/赛道池。

口径遵循规划:
- 质量五维是「筛子不是打分表」;行业内分位优于全市场绝对阈值
- 市场弱势 → 总池暂停新增入池;赛道池收紧门槛
- SC-U08 回避名单内的行业禁止新增入池
- 评纔回答:持有/买入/卖出/空仓(基于规则;AI 参考层另行叠加,不可覆盖硬规则)
"""
import json
import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..shared import data_gateway as dg
from .models import (DailyQuote, FinancialIndicator, IndustryDaily, MarketState,
                     StockBasic, StockPool, StrategyCard, StrategyParams, Valuation)
from .news_monitor import track_news_heat
from .technical_signal import latest_signals
from .universe_manager import get_track_map

log = logging.getLogger("strategy_engine")

PARAM_DEFAULTS: dict[str, str] = {
    "pool_top_n": "30",               # 总池容量
    "track_pool_top_n": "8",          # 每赛道池容量
    "min_mv_hard": "50",              # 硬性最小市值(亿)
    "mv_pref_low": "200",             # 优先区间下限
    "mv_pref_high": "800",            # 优先区间上限
    "mv_hard_high": "1500",           # 超过则剔除(千亿留观上限,可调)
    "min_list_years": "2",            # 次新过滤
    "board_allow": "主板,创业板,科创板",
    "roe_ind_pct_min": "70",          # ROE 行业内分位下限
    "gm_min": "25",                   # 毛利率绝对下限(参考H01:30%,放宽给中游制造)
    "cash_min": "0.7",                # 现金含量下限
    "debt_max": "60",                 # 资产负债率上限
    "pe_pct_max": "40",               # PE 5年分位上限(低于则加分)
    "peg_max": "1.5",                 # PEG 上限(加分项)
    "turnover_low": "1", "turnover_high": "15",
    "mom20_max": "30",                # 20日涨幅上限(不追暴涨末端)
    "track_score_min": "60",          # 赛道池最低分
}


def get_params(db: Session) -> dict[str, str]:
    rows = db.execute(select(StrategyParams)).scalars().all()
    merged = dict(PARAM_DEFAULTS)
    for r in rows:
        merged[r.key] = r.value
    return merged


def ensure_defaults(db: Session) -> None:
    """首次初始化:策略参数 + 策略卡(带证据链)。"""
    for k, v in PARAM_DEFAULTS.items():
        if db.get(StrategyParams, k) is None:
            db.add(StrategyParams(key=k, value=v, note=PARAM_NOTE.get(k, "")))
    cards = [
        StrategyCard(card_code="SC-U08", title="板块拥挤度反转(回避)", status="verified",
                     hypothesis="行业成交占比偏离自身MA120超300%后,该行业未来20交易日跑输",
                     source_note="Invest知识库 BT-U08; IS/OOS一致; bootstrap CI[-6.85%,-1.80%]",
                     params=json.dumps({"threshold": 300, "cooldown": 20})),
        StrategyCard(card_code="PM02", title="周期四阶段仓位分档", status="unverified",
                     hypothesis="用阶段决定总仓位:衰退80-100/复苏50-70/下行40-60/过热0-30",
                     source_note="Invest知识库 PM02(草案),对应看板U12-U14",
                     params=json.dumps({"hysteresis": "升级需连续2期确认+驻留60日"})),
        StrategyCard(card_code="G03", title="三阶段动态止损", status="unverified",
                     hypothesis="初始止损→1R或+3%后移保本价→创新高后动态抬升;禁止浮亏放宽",
                     source_note="Invest知识库 G03,对应看板U26",
                     params=json.dumps({"breakeven_triggers": ["1R", "+3%"], "trail": ["percent8", "atr1.5-2.5"]})),
        StrategyCard(card_code="H01", title="基本面质量五维筛", status="unverified",
                     hypothesis="盈利/运营/现金流/风险/成长五维筛子,行业内分位口径",
                     source_note="Invest知识库 H01,对应看板U15",
                     params=json.dumps({"roe_ind_pct_min": 70, "cash_min": 0.8})),
        StrategyCard(card_code="BUY-SETUP", title="底分型+连红+量能入场", status="unverified",
                     hypothesis="底分型确认后连红3-4天且量能偏离>50%,趋势中段入场",
                     source_note="会议纪要口径;待回测验证",
                     params=json.dumps({"streak": 3, "vol_dev": 50})),
    ]
    for c in cards:
        if db.execute(select(StrategyCard).where(StrategyCard.card_code == c.card_code)).scalar_one_or_none() is None:
            db.add(c)
    db.commit()


PARAM_NOTE = {
    "pool_top_n": "总池每日容量", "mv_pref_low": "200-800亿为会议纪要优先区间",
    "mv_hard_high": "千亿以上增长空间有限,超此值剔除", "roe_ind_pct_min": "H01:行业内前30%",
    "cash_min": "现金含量,会议口径0.7;H01口径0.8", "pe_pct_max": "相对自身历史分位",
    "track_score_min": "弱势时自动+10收紧",
}


def _ind_pct(group: pd.Series) -> pd.Series:
    return group.rank(pct=True) * 100


def build_candidates(db: Session, trade_date: date | None = None) -> pd.DataFrame:
    """汇总候选域截面数据:行情/财务/估值/信号/赛道,返回 DataFrame。"""
    trade_date = trade_date or dg.prev_trade_day()
    params = get_params(db)

    basics = pd.DataFrame(db.execute(
        select(StockBasic.code, StockBasic.name, StockBasic.industry, StockBasic.board,
               StockBasic.is_st, StockBasic.total_mv, StockBasic.list_date)).all(),
        columns=["code", "name", "industry", "board", "is_st", "total_mv", "list_date"])
    if basics.empty:
        return pd.DataFrame()

    quotes_now = pd.DataFrame(db.execute(
        select(DailyQuote.code, DailyQuote.close, DailyQuote.turnover, DailyQuote.amount)
        .where(DailyQuote.trade_date == trade_date)).all(),
        columns=["code", "close", "turnover", "amount"])
    start_d = trade_date - timedelta(days=400)
    hist = pd.DataFrame(db.execute(
        select(DailyQuote.code, DailyQuote.trade_date, DailyQuote.close)
        .where(DailyQuote.trade_date >= start_d).order_by(DailyQuote.code, DailyQuote.trade_date)).all(),
        columns=["code", "trade_date", "close"])

    # 财务:最新报告期 + 毛利率8期历史(定价权代理)
    fin_all = pd.DataFrame(db.execute(
        select(FinancialIndicator.code, FinancialIndicator.period, FinancialIndicator.roe,
               FinancialIndicator.gross_margin, FinancialIndicator.net_margin,
               FinancialIndicator.profit_yoy, FinancialIndicator.revenue_yoy,
               FinancialIndicator.ocf_to_profit, FinancialIndicator.debt_ratio)
        .order_by(FinancialIndicator.code, FinancialIndicator.period)).all(),
        columns=["code", "period", "roe", "gross_margin", "net_margin",
                 "profit_yoy", "revenue_yoy", "ocf_to_profit", "debt_ratio"])
    fin_latest = fin_all.sort_values("period").groupby("code").tail(1).set_index("code") \
        if not fin_all.empty else pd.DataFrame()
    gm_hist = fin_all.groupby("code")["gross_margin"].apply(list).to_dict() if not fin_all.empty else {}

    val = pd.DataFrame(db.execute(
        select(Valuation.code, Valuation.pe_ttm, Valuation.pb, Valuation.pe_pct_5y,
               Valuation.pb_pct_5y).where(Valuation.trade_date == trade_date)).all(),
        columns=["code", "pe_ttm", "pb", "pe_pct_5y", "pb_pct_5y"])

    df = basics.merge(quotes_now, on="code", how="inner")
    df = df.merge(val, on="code", how="left")
    for col in ("roe", "gross_margin", "net_margin", "profit_yoy", "revenue_yoy",
                "ocf_to_profit", "debt_ratio"):
        df[col] = df["code"].map(fin_latest[col] if not fin_latest.empty else {})
    if df.empty:
        return df

    # 行情衍生指标
    g = hist.groupby("code")["close"]
    ma60 = g.rolling(60, min_periods=50).mean().groupby(hist["code"]).last()
    ma120 = g.rolling(120, min_periods=100).mean().groupby(hist["code"]).last()
    close_20 = g.apply(lambda s: s.iloc[-21] if len(s) > 21 else None)
    df["ma60"] = df["code"].map(ma60)
    df["ma120"] = df["code"].map(ma120)
    df["mom20"] = df["code"].map(
        {c: (s.iloc[-1] / s.iloc[-21] - 1) * 100 if len(s) > 21 else None
         for c, s in g})
    close_20_map = {c: v for c, v in close_20.items()} if isinstance(close_20, pd.Series) else {}
    df["mom20"] = df.apply(
        lambda r: (r["close"] / close_20_map[r["code"]] - 1) * 100
        if close_20_map.get(r["code"]) else r["mom20"], axis=1)

    # 定价权代理:近2期毛利率均值 >= 全历史均值(稳定或抬升)
    def pricing_power(code: str) -> bool | None:
        gms = [x for x in gm_hist.get(code, []) if x is not None]
        if len(gms) < 3:
            return None
        return sum(gms[-2:]) / 2 >= sum(gms) / len(gms) - 1.0
    df["pricing_power"] = df["code"].map(lambda c: pricing_power(c))

    # 行业内分位(H01口径)
    df["roe_ind_pct"] = df.groupby("industry")["roe"].transform(lambda s: _ind_pct(s))
    df["gm_ind_pct"] = df.groupby("industry")["gross_margin"].transform(lambda s: _ind_pct(s))

    # 技术信号(今日 buy_setup / rs_weak)
    sig = latest_signals(db, trade_date=trade_date, limit=5000)
    df["buy_setup"] = df["code"].isin({s.code for s in sig if s.signal_type == "buy_setup"})
    df["rs_weak"] = df["code"].isin({s.code for s in sig if s.signal_type == "rs_weak"})
    df["rs_strong"] = df["code"].isin({s.code for s in sig if s.signal_type == "rs_strong"})

    # 拥挤度回避(SC-U08):行业名对齐东财板块名
    avoid = {r.industry_name for r in db.execute(select(IndustryDaily).where(
        IndustryDaily.trade_date == trade_date)).scalars()
        if r.avoid_until is not None and r.avoid_until >= trade_date}
    df["crowd_avoid"] = df["industry"].isin(avoid)

    df["tracks"] = df["code"].map(get_track_map(db))
    df["trade_date"] = trade_date
    return df


def hard_filter(df: pd.DataFrame, params: dict[str, str]) -> pd.DataFrame:
    """第0层基础过滤+质量五维筛(H01:筛子口径,不合成打分)。"""
    today = dg.today()
    allow_boards = set(params["board_allow"].split(","))
    list_cut = today - timedelta(days=int(float(params["min_list_years"]) * 365.25))

    mask = (
        ~df["is_st"].fillna(False)
        & df["board"].isin(allow_boards)
        & df["close"].notna() & (df["close"] > 0)
        # 市值:未知(=0,数据源缺字段)时不拦截,仅在有值时执行区间过滤(优雅降级)
        & (df["total_mv"] <= 0
           | ((df["total_mv"] >= float(params["min_mv_hard"]))
              & (df["total_mv"] <= float(params["mv_hard_high"]))))
        & (df["list_date"].isna() | (df["list_date"] <= list_cut))
        & df["roe"].notna() & (df["roe"] > 0)
        & (df["profit_yoy"] > 0)                     # 最新报告期业绩为正增长
        & (df["ocf_to_profit"].isna() | (df["ocf_to_profit"] >= float(params["cash_min"])))
        & (df["debt_ratio"].isna() | (df["debt_ratio"] < float(params["debt_max"])))
        & (df["gross_margin"].isna() | (df["gross_margin"] >= float(params["gm_min"])))
        # 换手率=0 视为数据源缺字段(优雅降级),有值时才执行区间过滤
        & (df["turnover"].isna() | (df["turnover"] <= 0)
           | df["turnover"].between(float(params["turnover_low"]), float(params["turnover_high"])))
        & (df["mom20"].isna() | (df["mom20"] < float(params["mom20_max"])))
        & ~df["crowd_avoid"].fillna(False)
        # 质量五维之 ROE 行业内分位(组内样本不足时分位为 NaN → 放行,由绝对阈值兜底)
        & (df["roe_ind_pct"].isna() | (df["roe_ind_pct"] >= float(params["roe_ind_pct_min"])))
        # 趋势:中期多头(收盘>MA60>MA120)
        & (df["ma60"].isna() | (df["close"] > df["ma60"]))
        & (df["ma120"].isna() | (df["ma60"] > df["ma120"]))
    )
    return df[mask].copy()


def score_candidates(df: pd.DataFrame, params: dict[str, str],
                     valuation_pct_guard: bool = True) -> pd.DataFrame:
    """综合评分(入围后排序用,透明加权)。"""
    if df.empty:
        return df
    s = df.copy()
    # 评分输入 NaN 免疫(缺数据按中性值处理,不让 NaN 污染得分)
    s["mom20"] = s["mom20"].fillna(0.0)
    s["pe_ttm"] = s["pe_ttm"].fillna(0.0)
    s["profit_yoy"] = s["profit_yoy"].fillna(0.0)
    s["total_mv"] = s["total_mv"].fillna(0.0)
    s["val_score"] = s.apply(
        lambda r: max(0.0, 100 - float(r["pe_pct_5y"])) if pd.notna(r["pe_pct_5y"]) else 50.0,
        axis=1)
    s["peg"] = s.apply(
        lambda r: (float(r["pe_ttm"]) / float(r["profit_yoy"]))
        if pd.notna(r["pe_ttm"]) and pd.notna(r["profit_yoy"]) and r["profit_yoy"] > 0 else None,
        axis=1)
    lo, hi = float(params["mv_pref_low"]), float(params["mv_pref_high"])

    def _mv_fit(mv: float) -> float:
        if mv is None or mv <= 0:
            return 50.0  # 市值未知,中性
        return 100.0 if lo <= mv <= hi else 0.0

    s["mv_fit"] = s["total_mv"].map(_mv_fit)

    def _norm(col: str) -> pd.Series:
        vals = s[col].fillna(0.0)
        rng = vals.max() - vals.min()
        return (vals - vals.min()) / rng * 100 if rng and rng > 0 else vals * 0 + 50

    s["score"] = (
        0.30 * s["roe_ind_pct"].fillna(50)
        + 0.15 * s["gm_ind_pct"].fillna(50)
        + 0.20 * s["val_score"]
        + 0.10 * _norm("mom20")
        + 0.10 * s["mv_fit"]
        + 0.05 * s["pricing_power"].fillna(False).astype(float) * 100
        + 0.10 * s["buy_setup"].astype(float) * 100
        + s.apply(lambda r: 5.0 if r["rs_strong"] else 0, axis=1)
        - s.apply(lambda r: 15.0 if r["rs_weak"] else 0, axis=1)
        + s.apply(lambda r: 5.0 if (r["peg"] is not None and r["peg"] < float(params["peg_max"]))
                  else 0, axis=1)
    )
    s["score"] = s["score"].fillna(0.0)
    return s.sort_values("score", ascending=False)


def _rating(row: pd.Series) -> tuple[str, list[str]]:
    """评级:buy/hold/sell/avoid(规则层)。"""
    reasons = []
    if row.get("crowd_avoid"):
        return "avoid", ["SC-U08拥挤度回避行业"]
    if row.get("profit_yoy") is not None and row["profit_yoy"] < 0:
        return "sell", ["最新报告期业绩转负"]
    if row.get("pe_pct_5y") is not None and row["pe_pct_5y"] > 80:
        return "sell", ["估值升至5年80%分位以上"]
    if row.get("rs_weak"):
        return "sell", ["跑输所属行业(相对强弱弱势)"]
    if row.get("buy_setup"):
        return "buy", ["底分型+连红+量能放大"]
    if row.get("rs_strong"):
        reasons.append("强于行业与基准")
    reasons.append("通过质量与估值筛")
    return "hold", reasons


def run_strategy(db: Session, trade_date: date | None = None) -> dict:
    """每日策略主流程:漏斗→总池/赛道池→出池留档→评级。返回统计。"""
    from .industry_monitor import market_wide_crowded
    from .market_regime import latest_state, risk_guard_active

    trade_date = trade_date or dg.prev_trade_day()
    params = get_params(db)
    ensure_defaults(db)
    df = build_candidates(db, trade_date)
    if df.empty:
        return {"error": "候选截面为空,请先完成数据回填"}
    passed = hard_filter(df, params)
    scored = score_candidates(passed, params)

    state = latest_state(db)
    weak = risk_guard_active(db)
    heat = track_news_heat(db)
    top_n = int(params["pool_top_n"])
    track_n = int(params["track_pool_top_n"])
    score_min = float(params["track_score_min"]) + (10 if weak else 0)

    new_entries, out_count, main_in = 0, 0, []

    def reasons_of(row) -> list[str]:
        base = []
        tracks = row.get("tracks")
        if isinstance(tracks, list) and tracks:
            base.append("赛道:" + ",".join(tracks[:2]))
        if row.get("pricing_power"):
            base.append("定价权(毛利率稳定)")
        if pd.notna(row.get("pe_pct_5y")) and row["pe_pct_5y"] <= float(params["pe_pct_max"]):
            base.append(f"PE分位{row['pe_pct_5y']:.0f}%")
        return base

    # ---- 总池 ----
    open_main = db.execute(select(StockPool).where(
        StockPool.pool_type == "main", StockPool.status == "in")).scalars().all()
    open_codes = {p.code for p in open_main}

    if not weak:
        for _, row in scored.head(top_n).iterrows():
            rating, rreasons = _rating(row)
            full_reasons = reasons_of(row) + rreasons
            if row["code"] in open_codes:
                obj = next(p for p in open_main if p.code == row["code"])
                obj.score, obj.rating = float(row["score"]), rating
                obj.reasons = json.dumps(full_reasons, ensure_ascii=False)
            else:
                db.add(StockPool(trade_date=trade_date, pool_type="main", code=row["code"],
                                 name=row["name"], in_price=float(row["close"]),
                                 score=float(row["score"]), rating=rating,
                                 reasons=json.dumps(full_reasons, ensure_ascii=False)))
                new_entries += 1
            main_in.append(row["code"])
    # 出池:在池但已跌出筛选(含行业拥挤、业绩转负、相对弱势)
    for p in open_main:
        if p.code in main_in:
            continue
        row = scored[scored["code"] == p.code]
        if row.empty:
            continue
        r = row.iloc[0]
        rating, rreasons = _rating(r)
        if rating == "avoid" or rating == "sell":
            p.status, p.out_date = "out", trade_date
            p.out_reason = ";".join(rreasons)[:200]
            out_count += 1

    # ---- 赛道池 ----
    from .universe_manager import list_tracks
    for track in list_tracks(db):
        open_track = db.execute(select(StockPool).where(
            StockPool.pool_type == "track", StockPool.track_id == track.id,
            StockPool.status == "in")).scalars().all()
        open_t = {p.code for p in open_track}
        member_codes = {c for c, ts in get_track_map(db).items() if track.name in ts}
        picks = scored[scored["code"].isin(member_codes)].head(track_n)
        in_codes = []
        for _, row in picks.iterrows():
            if float(row["score"]) < score_min:
                continue
            rating, rreasons = _rating(row)
            heat_bonus = heat.get(track.name, 0) / 10
            if row["code"] in open_t:
                obj = next(p for p in open_track if p.code == row["code"])
                obj.score, obj.rating = float(row["score"]) + heat_bonus, rating
                obj.reasons = json.dumps(reasons_of(row) + rreasons, ensure_ascii=False)
            else:
                db.add(StockPool(trade_date=trade_date, pool_type="track", track_id=track.id,
                                 code=row["code"], name=row["name"],
                                 in_price=float(row["close"]),
                                 score=float(row["score"]) + heat_bonus, rating=rating,
                                 reasons=json.dumps(reasons_of(row) + rreasons, ensure_ascii=False)))
                new_entries += 1
            in_codes.append(row["code"])
        for p in open_track:
            if p.code not in in_codes:
                p.status, p.out_date, p.out_reason = "out", trade_date, "跌出赛道池排序"
                out_count += 1

    db.commit()
    result = {
        "trade_date": trade_date.isoformat(), "scanned": int(len(df)),
        "passed": int(len(passed)), "new_entries": new_entries, "outs": out_count,
        "weak_guard": weak, "market_stage": state.cycle_stage if state else "",
        "market_wide_crowded": market_wide_crowded(db, trade_date),
        "pool_main_count": len(main_in),
    }
    log.info("strategy run: %s", result)
    return result


def update_pool_performance(db: Session, trade_date: date | None = None) -> int:
    """更新在池股票自入选以来的累计表现(绝对+相对基准双口径,回测红线)。"""
    from .models import PoolPerformance

    trade_date = trade_date or dg.prev_trade_day()
    open_pools = db.execute(select(StockPool).where(StockPool.status == "in")).scalars().all()
    if not open_pools:
        return 0
    codes = {p.code for p in open_pools}
    entry_dates = {p.code: (p.in_date if hasattr(p, "in_date") else p.trade_date) for p in open_pools}

    bench = None
    try:
        bench = dg.get_index_daily("sh000985",
                                   (trade_date - timedelta(days=400)).strftime("%Y%m%d"))
        bench = bench.set_index("trade_date")["close"].astype(float)
    except Exception as exc:  # noqa: BLE001 基准不可用时只记绝对收益,超额留0
        log.warning("benchmark unavailable for pool perf: %s", exc)

    rows = db.execute(select(DailyQuote.code, DailyQuote.trade_date, DailyQuote.close).where(
        DailyQuote.code.in_(codes), DailyQuote.trade_date >= trade_date - timedelta(days=400))
    ).all()
    q = pd.DataFrame(rows, columns=["code", "trade_date", "close"])

    written = 0
    for code, grp in q.groupby("code"):
        entry = entry_dates.get(code)
        if entry is None:
            continue
        base = grp[grp["trade_date"] >= entry].sort_values("trade_date")
        if base.empty:
            continue
        last = base.iloc[-1]
        if last["trade_date"] != trade_date:
            continue
        entry_close = float(base.iloc[0]["close"])
        cum = (float(last["close"]) / entry_close - 1) * 100
        bench_cum = 0.0
        if bench is not None:
            b0 = bench[bench.index >= entry]
            bench_cum = (float(bench.iloc[-1]) / float(b0.iloc[0]) - 1) * 100 \
                if not b0.empty else 0.0
        exists = db.execute(select(PoolPerformance).where(
            PoolPerformance.code == code, PoolPerformance.pool_entry_date == entry,
            PoolPerformance.trade_date == trade_date)).scalar_one_or_none()
        if exists is None:
            db.add(PoolPerformance(code=code, pool_entry_date=entry, trade_date=trade_date,
                                   close=float(last["close"]), cum_pct=round(cum, 2),
                                   benchmark_pct=round(bench_cum, 2)))
        else:
            exists.close, exists.cum_pct, exists.benchmark_pct = \
                float(last["close"]), round(cum, 2), round(bench_cum, 2)
        written += 1
    db.commit()
    return written
