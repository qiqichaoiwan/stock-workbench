"""市场状态引擎:趋势标签 + PM02 四阶段档位(草案)+ 建议仓位(滞回)。

口径红线(Invest 回测教训):
- 全部采用相对/偏离度口径;绝对阈值不作主信号
- 市场宽度用于趋势「确认」(U07:高宽度后是延续,不是反转)
- PM02 分档为 unverified 草案,输出带「待验证」标记
"""
import json
import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..shared import data_gateway as dg
from .models import DailyQuote, MarketState

log = logging.getLogger("market_regime")

# PM02 周期分档仓位表(草案,unverified)
PM02_POSITIONS = {
    "衰退": "80%-100%", "复苏": "50%-70%", "下行": "40%-60%", "过热": "0%-30%",
}
MIN_DWELL_DAYS = 60      # 最小驻留期(约一个季度,升级需满足)
UPGRADE_CONFIRM_N = 2    # 升级(加风险)需连续确认次数


def _pct_rank(series: pd.Series, value: float) -> float:
    s = series.dropna()
    if s.empty:
        return 50.0
    return float((s < value).mean() * 100)


def _benchmark_daily(start: str) -> pd.DataFrame:
    """基准指数日线:中证全指 sh000985,失败降级沪深300 sh000300。"""
    for symbol in ("sh000985", "sh000300"):
        try:
            df = dg.get_index_daily(symbol, start)
            if len(df) >= 60:
                df["symbol"] = symbol
                return df
        except Exception as exc:  # noqa: BLE001
            log.warning("benchmark %s failed: %s", symbol, exc)
    raise RuntimeError("benchmark index unavailable")


def _breadth_from_quotes(db: Session) -> float:
    """市场宽度:候选域个股收盘价站上自身 MA200 的占比%(趋势确认项)。"""
    latest = db.execute(select(DailyQuote.trade_date).order_by(DailyQuote.trade_date.desc())).first()
    if latest is None:
        return 50.0
    latest_d = latest[0]
    start = (latest_d - timedelta(days=400)).isoformat().replace("-", "")
    rows = db.execute(
        select(DailyQuote.code, DailyQuote.trade_date, DailyQuote.close)
        .where(DailyQuote.trade_date >= pd.Timestamp(start).date())
    ).all()
    if not rows:
        return 50.0
    df = pd.DataFrame(rows, columns=["code", "trade_date", "close"])
    df = df.sort_values(["code", "trade_date"])
    ma200 = df.groupby("code")["close"].rolling(200, min_periods=200).mean().reset_index(level=0, drop=True)
    df = df.assign(ma200=ma200)
    latest_df = df[df["trade_date"] == latest_d].dropna(subset=["ma200"])
    if latest_df.empty:
        return 50.0
    above = (latest_df["close"] > latest_df["ma200"]).mean() * 100
    return round(float(above), 2)


def _all_a_valuation_pct() -> float:
    """市场估值分位%(相对位置口径):沪深300 PE_TTM 5年分位作代理(全A口径接口缺失时)。"""
    try:
        import akshare as ak

        dg._rate_limit()
        df = ak.stock_index_pe_lg(symbol="沪深300")
        df = df.rename(columns={"日期": "d", "市盈率TTM": "pe"})
        df["d"] = pd.to_datetime(df["d"])
        cut = pd.Timestamp.today() - pd.DateOffset(years=5)
        recent = df[df["d"] >= cut]["pe"].dropna()
        if recent.empty:
            return 50.0
        return round(_pct_rank(recent, float(recent.iloc[-1])), 2)
    except Exception as exc:  # noqa: BLE001
        log.warning("market valuation failed: %s", exc)
        return 50.0


def _margin_dev(start: str) -> tuple[float, float]:
    """融资余额/成交额 及其相对自身 MA120 的偏离度(相对口径)。单位:亿元/亿元。"""
    try:
        margin = dg.get_margin_balance(days=200)          # 亿元
        amount = dg.get_market_total_amount(start)        # 元
        amount = amount.copy()
        amount["amount"] = amount["amount"] / 1e8          # → 亿元
        merged = margin.merge(amount, on="trade_date", suffixes=("", "_amt"))
        merged = merged[merged["amount"] > 0]
        if merged.empty:
            return 0.0, 0.0
        merged["ratio"] = merged["margin_balance"] / merged["amount"]
        latest_ratio = float(merged["ratio"].iloc[-1])
        ma120 = merged["ratio"].tail(120).mean()
        dev = (latest_ratio - ma120) / ma120 * 100 if ma120 else 0.0
        return round(latest_ratio, 4), round(dev, 2)
    except Exception as exc:  # noqa: BLE001
        log.warning("margin data failed: %s", exc)
        return 0.0, 0.0


def _decide_cycle_stage(eco_pos: bool, liq_pos: bool, val_dir_pos: bool,
                        valuation_pct: float) -> tuple[str, str]:
    """PM02 口径(代理因子):经济=基准60日动量,流动性=成交额偏离,估值方向=20日变化。

    过热档内含估值减仓细则:PE分位>60 减仓、>80 清仓(风险降低,即时生效)。
    """
    if eco_pos:  # 经济仍强
        if liq_pos and val_dir_pos:
            if valuation_pct > 80:
                return "过热", "0%-10%"
            if valuation_pct > 60:
                return "过热", "10%-20%"
            return "过热", "20%-30%"
        if not liq_pos:
            return "下行", PM02_POSITIONS["下行"]
        return "复苏", PM02_POSITIONS["复苏"]
    # 经济低迷
    if liq_pos or val_dir_pos:
        return "复苏", PM02_POSITIONS["复苏"]
    return "衰退", PM02_POSITIONS["衰退"]


def _apply_hysteresis(prev: MarketState | None, new_stage: str, new_range: str,
                      today_d: date) -> tuple[str, str, str, int]:
    """滞回:降风险(往低仓位档)即时生效;升风险(往高仓位档)需连续2期确认+驻留期。

    仓位档位高→低排序:衰退 > 复苏 > 下行 > 过热。
    返回 (生效stage, 生效range, pending_stage, pending_count)。
    """
    order = {"过热": 0, "下行": 1, "复苏": 2, "衰退": 3}
    if prev is None:
        return new_stage, new_range, "", 0
    prev_stage = prev.cycle_stage or new_stage
    if new_stage == prev_stage:
        return prev_stage, prev.position_range or new_range, "", 0
    if order[new_stage] < order[prev_stage]:
        # 新档位仓位更低 → 降风险,立即生效
        return new_stage, new_range, "", 0
    # 升风险方向:需连续 UPGRADE_CONFIRM_N 次确认 + 最小驻留期
    if prev.stage_pending == new_stage:
        pending_count = prev.pending_count + 1
    else:
        pending_count = 1
    if pending_count >= UPGRADE_CONFIRM_N:
        since = prev.created_at.date() if prev.created_at else today_d
        if (today_d - since).days >= MIN_DWELL_DAYS:
            return new_stage, new_range, "", 0
    return prev_stage, prev.position_range, new_stage, pending_count


def compute_market_state(db: Session, trade_date: date | None = None) -> MarketState:
    """计算并落库当日市场状态(幂等,同日覆盖)。

    任一外部组件失败按中性值处理并记录 missing,绝不整体失败——
    状态行必须每天产出,供下游与仪表盘使用。
    """
    trade_date = trade_date or dg.today()
    start = (trade_date - timedelta(days=500)).strftime("%Y%m%d")
    missing: list[str] = []

    try:
        bench = _benchmark_daily(start)
        close = bench["close"].astype(float).reset_index(drop=True)
        last_close = float(close.iloc[-1])
        ma20 = close.tail(20).mean()
        ma60 = close.tail(60).mean()
        ma200 = close.tail(200).mean() if len(close) >= 200 else ma60
        mom60 = (last_close / close.iloc[-61] - 1) * 100 if len(close) > 61 else 0.0
    except Exception as exc:  # noqa: BLE001
        log.warning("benchmark unavailable, use neutral: %s", exc)
        missing.append("benchmark")
        last_close = ma20 = ma60 = ma200 = 0.0
        mom60 = 0.0

    try:
        amount = dg.get_market_total_amount(start)
        amt_series = amount.set_index("trade_date")["amount"].astype(float)
        last_amount = float(amt_series.iloc[-1])
        amount_ma60 = float(amt_series.tail(60).mean())
        amount_dev = (last_amount / amount_ma60 - 1) * 100 if amount_ma60 else 0.0
        amount_pct_60d = _pct_rank(amt_series.tail(60), last_amount)
    except Exception as exc:  # noqa: BLE001
        log.warning("market amount unavailable, use neutral: %s", exc)
        missing.append("market_amount")
        amount_dev, amount_pct_60d = 0.0, 50.0

    breadth = _breadth_from_quotes(db)
    valuation_pct = _all_a_valuation_pct()
    margin_ratio, margin_dev = _margin_dev(start)

    # ---- 趋势打分(相对口径) ----
    score = 0
    ma_signal = 0
    if missing.count("benchmark") == 0:
        if last_close > ma200 and ma60 > ma200:
            ma_signal = 1
        elif last_close < ma20 and ma20 < ma60:
            ma_signal = -1
    score += ma_signal
    breadth_signal = 1 if mom60 > 0 else -1  # 宽度动量方向(延续口径)
    score += breadth_signal
    score += 1 if amount_dev > 5 else (-1 if amount_dev < -5 else 0)
    score += 1 if valuation_pct < 30 else (-1 if valuation_pct > 70 else 0)

    if valuation_pct <= 10 and score <= -1:
        trend_label = "极端底部"
    elif score >= 2:
        trend_label = "强势"
    elif score <= -2:
        trend_label = "弱势"
    else:
        trend_label = "震荡"

    # ---- PM02 四阶段 ----
    eco_pos = mom60 > 0
    liq_pos = amount_dev > 0
    val_dir_pos = valuation_pct >= 30  # 估值方向代理:分位上/下半区
    raw_stage, raw_range = _decide_cycle_stage(eco_pos, liq_pos, val_dir_pos, valuation_pct)

    prev = db.execute(select(MarketState).order_by(
        MarketState.trade_date.desc())).scalars().first()
    stage, position_range, pending, pending_count = _apply_hysteresis(
        prev, raw_stage, raw_range, trade_date)

    row = db.get(MarketState, trade_date) or MarketState(trade_date=trade_date)
    row.trend_label = trend_label
    row.cycle_stage = stage
    row.stage_pending = pending
    row.pending_count = pending_count
    row.position_range = position_range
    row.breadth = breadth
    row.amount_pct_60d = round(amount_pct_60d, 2)
    row.valuation_pct = valuation_pct
    row.margin_ratio = margin_ratio
    row.score_json = json.dumps({
        "score": score, "ma_signal": ma_signal, "mom60": round(mom60, 2),
        "amount_dev": round(amount_dev, 2), "margin_dev": margin_dev,
        "raw_stage": raw_stage, "raw_range": raw_range,
        "pm02_status": "unverified_draft",
        "missing_components": missing,
    }, ensure_ascii=False)
    db.merge(row)
    db.commit()
    log.info("market state %s: %s / %s (%s) missing=%s", trade_date, trend_label,
             stage, position_range, missing)
    return db.get(MarketState, trade_date)


def latest_state(db: Session) -> MarketState | None:
    return db.execute(select(MarketState).order_by(
        MarketState.trade_date.desc())).scalars().first()


def risk_guard_active(db: Session) -> bool:
    """弱势/极端底部时收紧入池(总池暂停)。"""
    state = latest_state(db)
    return state is not None and state.trend_label in ("弱势", "极端底部")
