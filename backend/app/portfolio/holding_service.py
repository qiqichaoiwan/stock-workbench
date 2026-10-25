"""持仓服务:录入、G03 三阶段止损引擎、卖出审计、每日巡检。

G03 引擎(代码硬规则,不可被 AI 覆盖):
  阶段A  初始止损(buy×(1-N%),默认N=10,对应会议纪要"最高点回撤10-15%必须卖出"),
         禁止上移、禁止浮亏放宽
  阶段B  浮盈≥1R 或 ≥保本价+3% → 止损上移至真实保本价(含全部摩擦成本)
  阶段C  之后再创新高 → 按规则(percent/atr)持续抬升止损,只上不下
  执行   收盘确认、次日开盘执行;跳空越过止损线按开盘价处理(T+1/涨跌停意识)
"""
import logging
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..shared import stock_ref
from .models import Holding, TradeRecord

log = logging.getLogger("holding")

# A股摩擦成本(2023-08 印花税减半后口径;佣金按万2.5估)
BUY_COMMISSION = 0.00025
SELL_COMMISSION = 0.00025
TRANSFER_FEE = 0.00001
STAMP_TAX = 0.0005

INITIAL_STOP_PCT = 10.0     # 阶段A初始止损百分比(会议纪要:回撤10-15%必须卖出)
TRAIL_PCT = 8.0             # 阶段C百分比跟踪(N=8,G03候选口径)
ATR_MULTIPLIER = 2.0        # 阶段C ATR吊灯倍数(候选集1.5/2/2.5,取2)


def breakeven_price(buy_price: float) -> float:
    """真实保本价 = 开仓价×(1+买入佣金+过户费)/(1−卖出佣金−印花税)。"""
    return round(buy_price * (1 + BUY_COMMISSION + TRANSFER_FEE)
                 / (1 - SELL_COMMISSION - STAMP_TAX), 4)


def _stage_b_reached(close: float, h: Holding) -> bool:
    r = h.risk_r if h.risk_r > 0 else h.buy_price * INITIAL_STOP_PCT / 100
    return close >= h.buy_price + r or close >= h.breakeven_price * 1.03


def _trail_stop(h: Holding, atr: float | None) -> float:
    """阶段C动态抬升候选:百分比跟踪 与 ATR吊灯 取高者。"""
    candidates = [h.highest_price * (1 - TRAIL_PCT / 100)]
    if atr and atr > 0:
        candidates.append(h.highest_price - ATR_MULTIPLIER * atr)
    return max(candidates)


def init_holding(db: Session, user_id: int, code: str, buy_price: float, quantity: int,
                 buy_date: date, stop_pct: float = INITIAL_STOP_PCT,
                 take_profit_price: float = 0.0, note: str = "") -> Holding:
    """录入持仓:计算保本价/初始止损/风险R。"""
    if not code.isdigit():
        raise ValueError("股票代码须为 6 位数字")
    if not stock_ref.stock_exists(db, code):
        raise ValueError(f"股票代码 {code} 不存在")
    if buy_date > date.today():
        raise ValueError(f"买入日期 {buy_date} 不能晚于今天")
    name = stock_ref.stock_name(db, code)
    be = breakeven_price(buy_price)
    initial_stop = round(buy_price * (1 - stop_pct / 100), 4)
    h = Holding(user_id=user_id, code=code, name=name, quantity=quantity,
                buy_price=buy_price, buy_date=buy_date,
                breakeven_price=be, initial_stop_price=initial_stop,
                stop_price=initial_stop, stop_stage="A", stop_rule="percent",
                trail_param=TRAIL_PCT, highest_price=buy_price,
                risk_r=round(buy_price - initial_stop, 4),
                take_profit_price=take_profit_price, note=note)
    db.add(h)
    db.commit()
    db.refresh(h)
    log.info("holding #%s created %s stage=A stop=%s be=%s", h.id, code, initial_stop, be)
    return h


def update_daily(db: Session, user_id: int | None = None,
                 trade_date: date | None = None) -> list[dict]:
    """每日巡检(唯一写止损的入口):更新最高价、推进止损阶段、生成风险提醒。

    - user_id=None:全量扫描,供巡检任务(holding_patrol)使用;
      有值:只扫该用户持仓。GET/日报禁止调用本函数(避免读请求触发跨租户写入),
      日报一律走 report_service._alerts 只读计算。
    - 行情统一截止 completed_trade_day(经 stock_ref.latest_quote_date/
      latest_close/quote_history/atr14 的 as-of 过滤),禁止偷看未来;
      长期停牌(>5天无有效收盘,qd < trade_date-5d)跳过,不推进止损。
    G03 相位(代码硬规则,不可被 AI 覆盖):
    - A:初始止损,禁止上移、禁止放宽;仅 _stage_b_reached 命中才晋升 B。
    - B:止损=真实保本价;当 trail(percent/ATR 取高) > 保本价时晋升 C 并抬止损到 trail。
      (旧 bug:旧代码 B 分支 new_stop=max(stop, be),new_stop 恒 <=be,故
      "stop > be 则晋升 C" 永不成立,G03 卡死在 B;现改为直接比较 trail > be。)
    - C:按 trail 持续抬升,只上不下,永不回退 A/B。
    """
    trade_date = trade_date or stock_ref.latest_quote_date(db)
    if trade_date is None:
        return []
    alerts: list[dict] = []
    q = select(Holding).where(Holding.status == "holding")
    if user_id is not None:
        q = q.where(Holding.user_id == user_id)
    holdings = db.execute(q).scalars().all()
    for h in holdings:
        latest = stock_ref.latest_close(db, h.code, as_of=trade_date)
        if latest is None:
            continue
        qd, close = latest
        if qd < trade_date - timedelta(days=5):
            continue  # 长期停牌:无有效收盘,跳过不推进止损,避免脏数据误触发
        # 持仓期最高价只统计建仓日(含)以来的行情:若把买入前的前高算进来,
        # B/C 阶段的 trail 止损可能高于现价,导致"建仓即触发止损"的误报。
        since_buy = stock_ref.max_high_since(db, h.code, h.buy_date, as_of=trade_date)
        h.highest_price = max(h.buy_price, since_buy or 0.0, close)

        # 阶段A:止损保持初始价不动(禁上移/禁放宽);达标才晋升 B。
        if h.stop_stage == "A" and _stage_b_reached(close, h):
            # B 入口:止损一次性上移至真实保本价(含全部摩擦成本)。
            h.stop_stage = "B"
            h.stop_price = max(h.stop_price, h.breakeven_price)
        if h.stop_stage == "B":
            # B->C 晋升:trail(percent/ATR 取高)已超过保本价 → 晋升 C 并抬止损到 trail;
            # 否则停留在 B(止损保持保本价,不退回 A,更不下调)。
            atr = stock_ref.atr14(db, h.code, as_of=trade_date)
            trail = _trail_stop(h, atr)
            if trail > h.breakeven_price:
                h.stop_stage = "C"
                h.stop_price = round(max(h.stop_price, trail), 4)
        elif h.stop_stage == "C":
            # C 阶段:按 trail 持续抬升,只上不下;永不回退到 A/B。
            atr = stock_ref.atr14(db, h.code, as_of=trade_date)
            trail = _trail_stop(h, atr)
            if trail > h.stop_price:  # 只上不下(禁止浮亏放宽)
                h.stop_price = round(trail, 4)
        if close <= h.stop_price:
            alerts.append({
                "code": h.code, "name": h.name, "close": close,
                "stop_price": h.stop_price, "stage": h.stop_stage,
                "level": "danger",
                "message": (f"{h.code} {h.name} 收盘 {close} 已跌破止损价 {h.stop_price}"
                            f"(阶段{h.stop_stage}),按纪律次日开盘执行卖出;跳空按开盘价"),
            })
        elif close <= h.stop_price * 1.03:
            alerts.append({
                "code": h.code, "name": h.name, "close": close,
                "stop_price": h.stop_price, "stage": h.stop_stage, "level": "warning",
                "message": f"{h.code} {h.name} 距止损价仅3%以内,注意纪律",
            })
    db.commit()
    return alerts


def sell(db: Session, user_id: int, holding_id: int, sell_price: float,
         sell_date: date, exit_trigger: str = "manual", note: str = "") -> TradeRecord:
    """点击「已卖出」:含费盈亏入审计库,持仓关闭。"""
    # Conditional update is the serialization point: only one concurrent request can claim it.
    claimed = db.execute(update(Holding).where(
        Holding.id == holding_id, Holding.user_id == user_id,
        Holding.status == "holding").values(status="sold")).rowcount
    if not claimed:
        db.rollback()
        raise ValueError("该持仓已卖出")
    h = db.get(Holding, holding_id)
    if h is None:
        db.rollback()
        raise ValueError("持仓不存在")
    # 业务日期必须落在 [买入日, 今天]:否则收益/持有天数会算出荒谬值
    if sell_date < h.buy_date:
        db.rollback()
        raise ValueError(f"卖出日期 {sell_date} 早于买入日期 {h.buy_date}")
    if sell_date > date.today():
        db.rollback()
        raise ValueError(f"卖出日期 {sell_date} 不能晚于今天")
    net_buy = h.buy_price * h.quantity * (1 + BUY_COMMISSION + TRANSFER_FEE)
    net_sell = sell_price * h.quantity * (1 - SELL_COMMISSION - STAMP_TAX)
    pnl = net_sell - net_buy
    pnl_pct = pnl / net_buy * 100 if net_buy else 0
    days = max((sell_date - h.buy_date).days, 0)
    if exit_trigger == "manual":
        if sell_price <= h.stop_price:
            exit_trigger = "trailing" if h.stop_stage == "C" else "stop_loss"
    rec = TradeRecord(user_id=user_id, holding_id=h.id, code=h.code, name=h.name,
                      buy_price=h.buy_price, buy_date=h.buy_date,
                      sell_price=sell_price, sell_date=sell_date,
                      quantity=h.quantity, pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                      holding_days=days, exit_trigger=exit_trigger, note=note or h.note)
    h.status = "sold"
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def holdings_view(db: Session, user_id: int) -> list[dict]:
    """持仓视图:现价/浮盈/止损线/阶段/池内评级引用。"""
    from ..screener.models import PoolPerformance  # noqa: F401 只读引用
    result = []
    for h in db.execute(select(Holding).where(
            Holding.user_id == user_id, Holding.status == "holding")).scalars():
        latest = stock_ref.latest_close(db, h.code)
        close = latest[1] if latest else h.buy_price
        unrealized = (close / h.breakeven_price - 1) * 100 if h.breakeven_price else 0
        rating_ref = stock_ref.pool_rating(db, h.code)
        result.append({
            "id": h.id, "code": h.code, "name": h.name, "quantity": h.quantity,
            "buy_price": h.buy_price, "buy_date": h.buy_date.isoformat(),
            "breakeven_price": h.breakeven_price, "initial_stop_price": h.initial_stop_price,
            "stop_price": h.stop_price, "stop_stage": h.stop_stage, "stop_rule": h.stop_rule,
            "highest_price": h.highest_price, "risk_r": h.risk_r,
            "take_profit_price": h.take_profit_price,
            "close": close,
            "unrealized_pct": round((close / h.buy_price - 1) * 100, 2),
            "unrealized_vs_breakeven_pct": round(unrealized, 2),
            "at_risk": bool(latest and close <= h.stop_price),
            "pool_rating": rating_ref,
            "note": h.note,
        })
    return result


def trade_history(db: Session, user_id: int, limit: int = 200) -> list[dict]:
    rows = db.execute(select(TradeRecord).where(TradeRecord.user_id == user_id)
                      .order_by(TradeRecord.sell_date.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "code": r.code, "name": r.name, "quantity": r.quantity,
             "buy_price": r.buy_price, "buy_date": r.buy_date.isoformat(),
             "sell_price": r.sell_price, "sell_date": r.sell_date.isoformat(),
             "pnl": r.pnl, "pnl_pct": r.pnl_pct, "holding_days": r.holding_days,
             "exit_trigger": r.exit_trigger, "note": r.note} for r in rows]


def trade_stats(db: Session, user_id: int) -> dict:
    """交易复盘统计:胜率/盈亏比/平均持有天数(绝对收益口径)。"""
    rows = db.execute(select(TradeRecord).where(TradeRecord.user_id == user_id)).scalars().all()
    if not rows:
        return {"count": 0}
    wins = [r for r in rows if r.pnl > 0]
    losses = [r for r in rows if r.pnl <= 0]
    avg_win = sum(r.pnl for r in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(r.pnl for r in losses) / len(losses)) if losses else 0
    total_pnl = sum(r.pnl for r in rows)
    return {
        "count": len(rows), "win_rate": round(len(wins) / len(rows) * 100, 1),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "profit_factor": round(avg_win * len(wins) / (avg_loss * len(losses)), 2)
        if losses and avg_loss else None,
        "total_pnl": round(total_pnl, 2),
        "avg_holding_days": round(sum(r.holding_days for r in rows) / len(rows), 1),
        "by_trigger": {t: sum(1 for r in rows if r.exit_trigger == t)
                       for t in {r.exit_trigger for r in rows}},
    }
