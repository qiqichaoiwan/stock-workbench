"""服务端实时适配器:为 dual_core_v2 提供可核验的核心 / 情绪 / 日历证据。

数据域边界(刻意保持 fail-closed):
- cores 883957 / 883994: 同花顺指数日线(强制刷新),只有最后一根 bar 的
  trade_date == 扫描日才认作当日实时快照;用 realhead 补齐当日 bar 时还要求
  成交额为正,避免把集合竞价前的昨收价拼成今日 bar。否则该核心不进入快照。
- emotion: 东财涨停 / 炸板 / 跌停池 + 883957 当日成交额与之前 5 日均额。
  涨停池为空时不推断「0 家」(可能是当日数据尚未发布),全部情绪字段保持缺失。
- calendar: akshare 交易日历,只有源成功且给出当日明确结果才标记 verified。

industry / stock / account / holdings 没有服务端可信源,适配器绝不合成:
它们继续由用户手工台账提供,任何缺失都由 v2 引擎阻断。

时间语义:
- 各域 timestamp 使用扫描开始时间(带时区);`collect()` 返回采集结束后的
  `now`,保证 `0 <= now - timestamp <= 120s` 成立。采集超过 120 秒时引擎
  会按时间戳过期阻断,这是预期行为。
- 14:45 分钟绕过缓存,保证 final_snapshot 的时间戳属于 14:45。
"""
from __future__ import annotations

import logging
import math
import threading
import time as _time
from datetime import date, datetime, time, timedelta

import pandas as pd

from . import dual_core
from .dual_core_v2 import CN
from ..shared import data_gateway as dg

log = logging.getLogger("dual_core_feed")

CORE_A = "883957"
CORE_B = "883994"
SOURCE_CORE = "10jqka:index_daily"
SOURCE_EMOTION = "eastmoney:limit_pool"
SOURCE_CALENDAR = "akshare:tool_trade_date_hist_sina"

CACHE_TTL_SECONDS = 20.0
CALENDAR_TIMEOUT_SECONDS = 6.0
MARKET_TIMEOUT_SECONDS = 12.0
# 09:30 前只有集合竞价甚至尚未成交,realhead 的 OHLC/成交额不构成当日快照。
SESSION_START = time(9, 30)
SESSION_END = time(15, 10)
# 收盘后为复核生成“最近收盘快照”:数据只到收盘,明确标记为非实时,永不授权开仓。
REVIEW_CLOSE = time(15, 0)
REVIEW_CACHE_SECONDS = 600.0
# 并发请求时等首个复核采集完成并复用缓存,避免瞬间返回 None 又显示成 5 类服务端缺口。
REVIEW_WAIT_SECONDS = 20.0

_CACHE_LOCK = threading.Lock()
_CACHE: dict = {}
_COLLECT_LOCK = threading.Lock()
_REVIEW_CACHE: dict = {}
_REVIEW_LOCK = threading.RLock()

_REQUIRED_CORE = ("price", "prev_high", "prev_low", "ma5", "ma10", "ma20",
                  "ma5_prev", "wma5", "wma10", "wma20", "w_pct",
                  "below_ma5_days", "wma20_up4")


def reset_cache() -> None:
    """测试/运维用:清空进程内快照缓存(不触碰外部源)。"""
    with _CACHE_LOCK:
        _CACHE.clear()
    with _REVIEW_LOCK:
        _REVIEW_CACHE.clear()


def _in_session(now: datetime) -> bool:
    return now.weekday() < 5 and SESSION_START <= now.time() <= SESSION_END


def _empty(now: datetime, reason: str) -> dict:
    return {"now": now, "calendar": None, "snapshot": None,
            "source_verified": False, "reference": None,
            "market_precheck": None, "errors": [reason]}


def _review_key(now: datetime) -> tuple[str, bool]:
    return now.date().isoformat(), now.time() >= REVIEW_CLOSE


def _review_cache_read(now: datetime) -> dict | None:
    with _REVIEW_LOCK:
        entry = _REVIEW_CACHE.get("entry")
    if not entry or entry.get("key") != _review_key(now):
        return None
    if _time.monotonic() - entry.get("at", 0.0) > REVIEW_CACHE_SECONDS:
        return None
    payload = dict(entry.get("payload") or {})
    payload["now"] = now
    return payload


def _review_cache_write(now: datetime, payload: dict) -> None:
    with _REVIEW_LOCK:
        _REVIEW_CACHE["entry"] = {"at": _time.monotonic(), "key": _review_key(now),
                                  "payload": dict(payload)}


def _last_completed_trade_day(now: datetime) -> date | None:
    """最近一个已收盘的交易日;盘中/盘前返回上一交易日。"""
    start = (now.date() - timedelta(days=20)).strftime("%Y%m%d")
    end = now.date().strftime("%Y%m%d")
    days = dg.get_trade_calendar(start, end)
    completed = sorted({
        day for day in days
        if isinstance(day, date)
        and (day < now.date() or (day == now.date() and now.time() >= REVIEW_CLOSE))
    })
    return completed[-1] if completed else None


def collect_review(now: datetime | None = None) -> dict | None:
    """收盘后/非交易时段的最近收盘快照:只供复核,调用方必须标记非实时。

    与实时快照不同,这里显式使用最近已收盘交易日 15:00 作为证据时间;
    数据缺失、源失败仍返回缺失,绝不把盘中数据冒充实时。
    """
    now = (now or datetime.now(CN)).astimezone(CN)
    cached = _review_cache_read(now)
    if cached is not None:
        return cached
    if not _REVIEW_LOCK.acquire(timeout=REVIEW_WAIT_SECONDS):
        return _review_cache_read(now)
    try:
        cached = _review_cache_read(now)
        if cached is not None:
            return cached
        trade_date = _last_completed_trade_day(now)
        if trade_date is None:
            return None
        close_at = datetime.combine(trade_date, REVIEW_CLOSE, tzinfo=CN)
        payload = _collect_uncached(close_at, allow_frame_fallback=True)
        payload.update({"mode": "closed", "review_at": close_at.isoformat(),
                        "data_trade_date": trade_date.isoformat(), "now": now})
        if payload.get("source_verified"):
            _review_cache_write(now, payload)
        return payload
    except Exception:  # noqa: BLE001 复核快照失败同样降级为缺失
        log.exception("dual core review snapshot failed")
        return None
    finally:
        _REVIEW_LOCK.release()


def _cache_read(now: datetime, *, force: bool) -> dict | None:
    if force:
        return None
    with _CACHE_LOCK:
        entry = _CACHE.get("entry")
    if not entry or entry.get("trade_date") != now.date():
        return None
    if _time.monotonic() - entry.get("at", 0.0) > CACHE_TTL_SECONDS:
        return None
    return entry.get("payload")


def _cache_write(now: datetime, payload: dict) -> None:
    with _CACHE_LOCK:
        _CACHE["entry"] = {"at": _time.monotonic(), "trade_date": now.date(),
                           "payload": payload}


def collect(now: datetime | None = None) -> dict:
    """采集一次可核验快照;任何失败都降级为缺失,绝不补造数值。"""
    scan_at = (now or datetime.now(CN)).astimezone(CN)
    if not _in_session(scan_at):
        return _empty(scan_at, "outside_session")
    force = scan_at.hour == 14 and scan_at.minute == 45
    cached = _cache_read(scan_at, force=force)
    if cached is not None:
        out = dict(cached)
        out["now"] = (now or datetime.now(CN)).astimezone(CN)
        return out
    if not _COLLECT_LOCK.acquire(blocking=False):
        cached = _cache_read(scan_at, force=force)
        if cached is not None:
            out = dict(cached)
            out["now"] = (now or datetime.now(CN)).astimezone(CN)
            return out
        return _empty(scan_at, "collect_in_progress")
    try:
        payload = _collect_uncached(scan_at)
        # 采集结束时间必须 >= 各域时间戳,否则引擎会判成未来时间戳。
        payload["now"] = (now or datetime.now(CN)).astimezone(CN)
    finally:
        _COLLECT_LOCK.release()
    if payload.get("source_verified"):
        _cache_write(scan_at, payload)
    return payload


def _collect_uncached(scan_at: datetime, *, allow_frame_fallback: bool = False) -> dict:
    errors: list[str] = []
    calendar = None
    try:
        calendar = _fetch_calendar(scan_at)
    except Exception as exc:  # noqa: BLE001 适配器必须把源错误降级为缺失
        errors.append(f"calendar:{type(exc).__name__}:{str(exc)[:120]}")
    if calendar and calendar.get("verified") is True and calendar.get("is_trade_day") is False:
        return {"now": datetime.now(CN), "calendar": calendar, "snapshot": None,
                "source_verified": False, "reference": None,
                "market_precheck": None, "errors": errors + ["not_trade_day"]}

    results: dict[str, dict] = {}

    def run(name, fn):
        try:
            results[name] = {"ok": True, "value": fn(scan_at)}
        except BaseException as exc:  # noqa: BLE001 采集线程不能带崩请求
            results[name] = {"ok": False,
                             "error": f"{type(exc).__name__}:{str(exc)[:120]}"}

    threads = [
        threading.Thread(target=run,
                         args=("cores", lambda at: _fetch_cores(
                             at, allow_frame_fallback=allow_frame_fallback)),
                         name="dual-core-feed-cores", daemon=True),
        threading.Thread(target=run, args=("pools", _fetch_pools),
                         name="dual-core-feed-pools", daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = _time.monotonic() + MARKET_TIMEOUT_SECONDS
    for thread in threads:
        thread.join(max(0.0, deadline - _time.monotonic()))
    for name in ("cores", "pools"):
        if name not in results:
            results[name] = {"ok": False, "error": "timeout"}
            errors.append(f"{name}:timeout")
        elif not results[name]["ok"]:
            errors.append(f"{name}:{results[name]['error']}")

    cores, frame_a = results["cores"].get("value", ({}, None)) if results["cores"]["ok"] else ({}, None)
    pools = results["pools"].get("value", {}) if results["pools"]["ok"] else {}
    snapshot: dict = {}
    if cores:
        snapshot["cores"] = cores
    emotion = _emotion_evidence(frame_a, pools, scan_at)
    if emotion:
        snapshot["emotion"] = emotion

    precheck = market_precheck(snapshot) if snapshot else None
    return {
        "now": datetime.now(CN),
        "calendar": calendar,
        "snapshot": snapshot or None,
        "source_verified": bool(snapshot),
        "reference": f"dual_core_feed:{scan_at:%Y%m%d-%H%M%S}",
        "market_precheck": precheck,
        # v2 在 industry/stock 缺失时会提前返回,不会逐项检查 emotion/cores 数值;
        # 这里显式列出服务端域自己的缺口,供上层合并与冻结判断。
        "server_missing": _server_missing(snapshot),
        "errors": errors,
    }


def _server_missing(snapshot: dict) -> list[str]:
    """服务端自有域(cores/emotion)的缺失键;不包含用户手工域。"""
    missing: list[str] = []
    cores = snapshot.get("cores") or {}
    for code in (CORE_A, CORE_B):
        core = cores.get(code)
        if not isinstance(core, dict):
            missing.append(f"cores.{code}")
            continue
        missing += [f"cores.{code}.{key}" for key in _REQUIRED_CORE
                    if core.get(key) is None]
        if code == CORE_B:
            missing += [f"cores.{code}.{key}" for key in ("high60", "macd_dead_cross")
                        if core.get(key) is None]
            hist = core.get("macd_hist")
            if (not isinstance(hist, list) or len(hist) < 3
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           for value in hist[-3:])):
                missing.append(f"cores.{code}.macd_hist")
    emotion = snapshot.get("emotion")
    if not isinstance(emotion, dict):
        missing.append("emotion")
    else:
        missing += [f"emotion.{key}" for key in
                    ("limit_up", "broken", "limit_down", "height", "amount", "amount_ma5")
                    if emotion.get(key) is None]
    return missing


# ---------------- 交易日历 ----------------

def _fetch_calendar(scan_at: datetime) -> dict:
    day = scan_at.date()
    days = dg.get_trade_calendar(day.strftime("%Y%m%d"), day.strftime("%Y%m%d"))
    return {"verified": True, "source": SOURCE_CALENDAR,
            "trade_date": day.isoformat(), "is_trade_day": day in set(days)}


# ---------------- 双核日线 ----------------

def _fetch_cores(scan_at: datetime, *, allow_frame_fallback: bool = False) -> tuple[dict, pd.DataFrame | None]:
    """返回 (code -> evidence, 883957 的当日 frame);单个核心失败不影响另一个。"""
    out: dict = {}
    frame_a: pd.DataFrame | None = None
    for code in (CORE_A, CORE_B):
        try:
            frame = dg.get_ths_index_daily(code, bars=1200, use_cache=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("dual-core feed %s daily failed: %s", code, exc)
            continue
        if frame is None or frame.empty:
            continue
        try:
            quote = dg.get_ths_index_quote(code, use_cache=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("dual-core feed %s quote failed: %s", code, exc)
            quote = None
        frame = _apply_quote(frame, quote, scan_at,
                             allow_frame_fallback=allow_frame_fallback)
        if frame is None or frame.empty:
            continue
        try:
            last_date = _iso_date(frame.iloc[-1]["trade_date"])
        except Exception:  # noqa: BLE001
            continue
        if last_date != scan_at.date().isoformat():
            log.warning("dual-core feed %s last bar %s is not today", code, last_date)
            continue
        evidence = _core_evidence(frame, scan_at)
        if evidence is None:
            continue
        out[code] = evidence
        if code == CORE_A:
            frame_a = frame
    return out, frame_a


def _apply_quote(frame: pd.DataFrame, quote: dict | None, scan_at: datetime,
                 *, allow_frame_fallback: bool = False) -> pd.DataFrame | None:
    """用 realhead 当日 OHLC 补齐/刷新 kline 末日;不一致时返回 None。

    一致性校验:quote.prev_close 必须等于 kline 的对应昨收,否则视为源不同步,
    拒绝拼接(不能把上一交易日或错误标的的报价当成今日)。
    ``allow_frame_fallback`` 仅供收盘复核:报价属于之后的新交易时段时,保留
    kline 已有目标交易日 bar,而不是把整段历史丢掉。
    """
    today = scan_at.date()
    dates = [_iso_date(value) for value in frame["trade_date"]]

    def fallback() -> pd.DataFrame | None:
        return frame if allow_frame_fallback and dates and dates[-1] == today.isoformat() else None

    if quote is None:
        return frame
    price = _num(quote.get("price"))
    high = _num(quote.get("high"))
    low = _num(quote.get("low"))
    prev_close = _num(quote.get("prev_close"))
    if None in (price, high, low, prev_close):
        return fallback()
    if min(price, high, low) <= 0 or high < price or low > price:
        return fallback()
    if not dates or dates[-1] > today.isoformat():
        return fallback()
    # 成交额为 0/缺失说明当日尚无可确认成交(集合竞价前),不能用昨收伪造今日 bar。
    amount = _num(quote.get("amount"))
    out = frame.copy()
    if dates[-1] == today.isoformat():
        if len(frame) < 2:
            return None
        expected = _num(frame["close"].iloc[-2])
        if expected is None or not _same_price(prev_close, expected):
            return fallback()
        if amount is None:
            # 已有当日 kline 本身就是有效证据,报价不完整时保留原 bar,不覆盖。
            return frame
        idx = out.index[-1]
        out.loc[idx, "open"] = _num(quote.get("open")) or price
        out.loc[idx, "high"] = max(_num(frame["high"].iloc[-1]) or high, high)
        out.loc[idx, "low"] = min(_num(frame["low"].iloc[-1]) or low, low)
        out.loc[idx, "close"] = price
        out.loc[idx, "amount"] = amount
        return out

    last_day = date.fromisoformat(dates[-1])
    if (today - last_day).days > 10:
        return fallback()
    expected = _num(frame["close"].iloc[-1])
    if expected is None or not _same_price(prev_close, expected):
        return fallback()
    if amount is None:
        # 当日 bar 尚不存在且没有成交额:保持缺失,等首次真实成交后再补。
        return None
    row = {column: None for column in out.columns}
    row.update({"trade_date": today,
                "open": _num(quote.get("open")) or price,
                "high": high, "low": low, "close": price,
                "amount": amount})
    return pd.concat([out, pd.DataFrame([row])], ignore_index=True)


def _same_price(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-6, abs(right) * 1e-6)


def _core_evidence(frame: pd.DataFrame, scan_at: datetime) -> dict | None:
    snapshot = dual_core._core_snapshot(frame)
    if not snapshot.get("ready"):
        return None
    weekly = dual_core.weekly_bars(frame)
    hist, dead_cross = _weekly_macd(weekly)
    below = _below_ma5_days(frame)
    up4 = _wma20_up4(weekly)
    prev_close = _num(frame["close"].iloc[-2])
    today_high = _num(frame["high"].iloc[-1])
    pct_chg_high = (_round((today_high / prev_close - 1) * 100, 2)
                    if prev_close and today_high is not None else None)
    return {
        "source": SOURCE_CORE,
        "timestamp": scan_at.isoformat(),
        "trade_date": _iso_date(snapshot.get("trade_date")),
        "price": snapshot.get("close"),
        "prev_high": snapshot.get("prev_high"),
        "prev_low": snapshot.get("prev_low"),
        "ma5": snapshot.get("ma5"),
        "ma10": snapshot.get("ma10"),
        "ma20": snapshot.get("ma20"),
        "ma5_prev": snapshot.get("ma5_prev"),
        "wma5": snapshot.get("wma5"),
        "wma10": snapshot.get("wma10"),
        "wma20": snapshot.get("wma20"),
        "w_pct": snapshot.get("w_pct"),
        "below_ma5_days": below,
        "wma20_up4": up4,
        "high60": snapshot.get("high60"),
        "macd_hist": hist,
        "macd_dead_cross": dead_cross,
        "high": today_high,
        "pct_chg": snapshot.get("pct_chg"),
        "pct_chg_high": pct_chg_high,
    }


def _weekly_macd(weekly: pd.DataFrame) -> tuple[list[float] | None, bool | None]:
    """周线 MACD(12,26,9) 最近 3 根柱 + 当前是否处于死叉(DIF<DEA)。"""
    if len(weekly) < 35:
        return None, None
    close = weekly["close"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2
    values = [_round(hist.iloc[-3], 4), _round(hist.iloc[-2], 4), _round(hist.iloc[-1], 4)]
    if any(value is None for value in values):
        return None, None
    return values, bool(dif.iloc[-1] < dea.iloc[-1])


def _below_ma5_days(frame: pd.DataFrame) -> int | None:
    """含当日在内、连续收盘低于当日 MA5 的天数(最多回看 20 日)。"""
    closes = frame["close"]
    if len(closes) < 5:
        return None
    ma5 = closes.rolling(5).mean()
    count = 0
    for idx in range(len(closes) - 1, max(len(closes) - 21, 0) - 1, -1):
        value, line = _num(closes.iloc[idx]), _num(ma5.iloc[idx])
        if value is None or line is None or value >= line:
            break
        count += 1
    return count


def _wma20_up4(weekly: pd.DataFrame) -> bool | None:
    """20 周均线连续 4 周向上(最近 4 个值严格递增)。"""
    ma20 = weekly["close"].rolling(20).mean()
    if len(ma20) < 23:
        return None
    tail = [_num(v) for v in ma20.iloc[-4:]]
    if any(v is None for v in tail):
        return None
    return bool(tail[0] < tail[1] < tail[2] < tail[3])


# ---------------- 情绪池 ----------------

def _fetch_pools(scan_at: datetime) -> dict:
    """涨停 / 炸板 / 跌停池。涨停池为空时视为当日数据未发布,不推断为 0。"""
    import akshare as ak  # 延迟导入,避免 API 启动时加载重型依赖

    day = scan_at.strftime("%Y%m%d")
    out = {"limit_up": None, "broken": None, "limit_down": None, "height": None}
    zt = _run_pool(ak.stock_zt_pool_em, day)
    if zt is None or len(zt) == 0:
        return out
    out["limit_up"] = int(len(zt))
    if "连板数" in zt.columns:
        values = pd.to_numeric(zt["连板数"], errors="coerce").dropna()
        out["height"] = int(values.max()) if len(values) else None
    zb = _run_pool(ak.stock_zt_pool_zbgc_em, day)
    if zb is not None:
        out["broken"] = int(len(zb))
    dt = _run_pool(ak.stock_zt_pool_dtgc_em, day)
    if dt is not None:
        out["limit_down"] = int(len(dt))
    return out


def _run_pool(fn, day: str):
    """单池调用失败返回 None(由上层保持缺失),空 DataFrame 是有效结果。"""
    try:
        frame = fn(date=day)
    except Exception as exc:  # noqa: BLE001
        log.warning("dual-core feed pool %s failed: %s", getattr(fn, "__name__", fn), exc)
        return None
    return frame if isinstance(frame, pd.DataFrame) else None


def _emotion_evidence(frame_a: pd.DataFrame | None, pools: dict, scan_at: datetime) -> dict:
    evidence = {"source": SOURCE_EMOTION, "timestamp": scan_at.isoformat()}
    for key in ("limit_up", "broken", "limit_down", "height"):
        evidence[key] = pools.get(key)
    amount, amount_ma5 = _amount_pair(frame_a)
    evidence["amount"] = amount
    evidence["amount_ma5"] = amount_ma5
    return evidence


def _amount_pair(frame: pd.DataFrame | None) -> tuple[float | None, float | None]:
    """当日成交额 vs 之前 5 个交易日日均额(盘中额未完整,仅作保守比较)。"""
    if frame is None or len(frame) < 6:
        return None, None
    amount = pd.to_numeric(frame["amount"], errors="coerce")
    today = _num(amount.iloc[-1])
    prior = [_num(v) for v in amount.iloc[-6:-1]]
    if today is None or today <= 0 or any(v is None or v <= 0 for v in prior):
        return None, None
    return _round(today, 0), _round(sum(prior) / 5, 0)


# ---------------- 仅市场域预检(不授权开仓) ----------------

def market_precheck(snapshot: dict) -> dict | None:
    """用 v2 同源公式给出市场域检查,供页面观察;不读取也不推断账户/持仓。"""
    cores = snapshot.get("cores") or {}
    emotion = snapshot.get("emotion") or {}
    a, b = cores.get(CORE_A), cores.get(CORE_B)
    if not a or not b:
        return None
    if any(a.get(key) is None for key in _REQUIRED_CORE):
        return None
    if any(b.get(key) is None for key in _REQUIRED_CORE + ("high60", "macd_dead_cross")):
        return None
    if not isinstance(b.get("macd_hist"), list) or len(b["macd_hist"]) < 3:
        return None
    if any(emotion.get(key) is None for key in
           ("limit_up", "broken", "limit_down", "height", "amount", "amount_ma5")):
        return None
    stamp = str(a.get("timestamp") or datetime.now(CN).isoformat())
    checks: list[dict] = []

    def check(key, step, actual, threshold, passed, reason):
        checks.append(dict(key=key, step=step, actual=actual, threshold=threshold,
                           **{"pass": passed},
                           status="missing" if passed is None else "pass" if passed else "fail",
                           reason=reason, timestamp=stamp, label=key, auto=True,
                           value=actual, hint=reason, veto=step == 0))
        return passed

    weekly = not (a["price"] < a["wma5"] or a["w_pct"] <= -8 or emotion["limit_down"] >= 50)
    check("weekly_veto", 1,
          {"price": a["price"], "wma5": a["wma5"], "w_pct": a["w_pct"],
           "limit_down": emotion["limit_down"]},
          "price >= wma5; weekly > -8%; limit_down <50",
          weekly, "weekly hard veto")
    ga = check("gun_a", 9, a["price"], a["prev_high"] * 1.003,
               a["price"] >= a["prev_high"] * 1.003,
               "current price, never intraday high")
    gb = check("gun_b", 9, b["price"], b["high60"] * 1.005,
               b["price"] >= b["high60"] * 1.005,
               "prior 60 sessions excluding current session")
    failed_rebound = a["below_ma5_days"] >= 2 and b["below_ma5_days"] >= 2
    check("failed_rebound", 1, [a["below_ma5_days"], b["below_ma5_days"]],
          "<2 for at least one core", not failed_rebound, "dual failed rebound veto")
    total_broken = emotion["limit_up"] + emotion["broken"]
    score = int(sum([
        emotion["limit_up"] >= 50,
        emotion["broken"] / total_broken <= 0.25 if total_broken else False,
        emotion["height"] >= 4,
        emotion["limit_down"] <= 10,
        emotion["amount"] >= emotion["amount_ma5"],
    ]))
    check("emotion", 1, score,
          ">=4 normal; 3 downgrade; <=2 two levels; <=1 no buys", True,
          "five-item score")
    bull = (a["price"] > a["wma5"] and a["price"] > a["wma10"]
            and a["price"] > a["wma20"] and b["wma5"] > b["wma10"] > b["wma20"]
            and b["wma20_up4"])
    tier = 2 if bull and b["ma5"] > b["ma5_prev"] else 1
    broken = {n: a["price"] < a["ma" + str(n)] * 0.997 for n in (5, 10, 20)}
    weak_b = b["price"] < b["wma5"] or b["w_pct"] <= -8
    tier = max(0, tier - int(broken[5]) - 2 * int(weak_b)
               - (1 if score == 3 else 2 if score <= 2 else 0))
    if b["price"] < b["ma5"] * 0.997:
        tier = 0
    hist = b["macd_hist"]
    divergence = bool(gb and (hist[-3] > hist[-2] > hist[-1] > 0 or b["macd_dead_cross"]))
    cap = [10, 50, 100][tier]
    if broken[10]:
        cap /= 2
    if divergence:
        cap = min(cap, 50)
    cap = min(cap, 50)
    if not weekly or not ga or not gb or failed_rebound:
        cap = 0
    for n in (5, 10, 20):
        check("ma" + str(n), 1, a["price"], a["ma" + str(n)] * 0.997,
              not broken[n], "cumulative downgrade / half / core-only")
    return {
        "cap": cap,
        "tier": tier,
        "weekly_ok": bool(weekly),
        "daily_ok": bool(ga and gb),
        "score": score,
        "divergence": divergence,
        "only_core": broken[20],
        "forbid_high_boards": weak_b or divergence or broken[20],
        "checks": checks,
        "note": "market-only precheck; account/holdings/industry/stock remain manual",
    }


# ---------------- helpers ----------------

def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _iso_date(value) -> str:
    """把 date / Timestamp / 字符串统一成 YYYY-MM-DD。"""
    if value is None:
        return ""
    if hasattr(value, "date") and not isinstance(value, str):
        return value.date().isoformat()
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else text


def _round(value, digits: int = 3):
    number = _num(value)
    return None if number is None else round(number, digits)
