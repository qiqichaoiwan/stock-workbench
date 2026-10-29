"""Read-only cockpit feeds; bounded daemon singleflight caches, no persistence."""
from copy import deepcopy
from datetime import datetime, time as day_time
import hashlib
import math
import threading
import time
from zoneinfo import ZoneInfo

import pandas as pd

from ..shared.data_gateway import ak_serialized

TZ = ZoneInfo("Asia/Shanghai")
INDEXES = (("sh000001", "上证指数"), ("sz399001", "深证成指"),
           ("sz399006", "创业板指"), ("sh000688", "科创50"))

# py_mini_racer(V8)初始化不是线程安全的,驾驶舱多个 Feed 并发调用 akshare
# 会触发原生崩溃,因此这里的 akshare 调用统一串行。
_AK_LOCK = threading.Lock()
# 会构造 MiniRacer 的调用必须与 data_gateway 共用同一把锁,否则跨模块并发
# (定时任务 + 驾驶舱)仍会让进程崩溃;非 V8 的 HTTP 源继续用 _AK_LOCK,避免
# 长达数秒的网络等待阻塞全局 V8 锁。
_V8_LOCK = ak_serialized()


def _now():
    return datetime.now(TZ)


class Feed:
    def __init__(self, source, ttl=30):
        self.source = source
        self.ttl = ttl
        self.lock = threading.Lock()
        self.value = None
        self.error = None
        self.attempt = None
        self.started = None
        self.inflight = False

    def get(self, loader, *, active=True):
        with self.lock:
            clock = time.monotonic()
            ttl = self.ttl if active else max(self.ttl, 900)
            if not self.inflight and (self.attempt is None or clock - self.attempt >= ttl):
                self.inflight = True
                self.started = self.attempt = clock
                threading.Thread(target=self._refresh, args=(loader,), daemon=True).start()
            timed_out = self.inflight and clock - self.started >= 12
            error = "source timeout (refresh still running)" if timed_out else self.error
            status = ("stale" if self.value is not None else "unavailable") if error else (
                "ok" if self.value is not None else "loading")
            return {"value": deepcopy(self.value), "source": self.source,
                    "status": status, "error": error}

    def _refresh(self, loader):
        try:
            value = loader()
            if value is None:
                raise ValueError("empty feed")
            with self.lock:
                self.value, self.error = value, None
        except Exception as exc:
            with self.lock:
                self.error = f"{self.source}: {type(exc).__name__}"
        finally:
            with self.lock:
                self.inflight = False
                self.attempt = time.monotonic()


_calendar = Feed("新浪交易日历", 86400)
_snapshot = Feed("新浪指数快照")
_minutes = {code: (Feed("东财1分钟"), Feed("新浪1分钟")) for code, _ in INDEXES}
_flows = {kind: Feed("东方财富官网·今日主力净流入(亿元)") for kind in ("industry", "concept")}
_news = (Feed("东方财富快讯（含官网红色重点）", 60), Feed("财联社重点", 60))


def _direct(fn, *args, **kwargs):
    return getattr(fn, "__wrapped__", fn)(*args, **kwargs)


def _load_calendar():
    with _V8_LOCK:
        from ..shared import data_gateway as dg
        return set(_direct(dg.get_trade_calendar, start="19900101", end="21001231"))


def _session(now):
    calendar = _calendar.get(_load_calendar)
    days = calendar["value"]
    # An old calendar ending before today cannot verify a weekday holiday.
    verified = bool(days and max(days) >= now.date())
    trade_day = now.date() in days if verified else now.weekday() < 5
    t = now.time().replace(tzinfo=None)
    if not trade_day:
        state, label = "non_trading", "非交易日"
    elif t < day_time(9, 30):
        state, label = "preopen", "盘前"
    elif t < day_time(11, 30) or day_time(13) <= t < day_time(15):
        state, label = "trading", "交易中"
    elif t < day_time(13):
        state, label = "lunch", "午间休市"
    else:
        state, label = "closed", "已收盘"
    return {"state": state, "label": label, "calendar_verified": verified}


def _number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def _timestamp(value):
    if value is None or not str(value).strip():
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(TZ)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _points(frame):
    if frame is None or frame.empty:
        raise ValueError("empty minute feed")
    time_col = "时间" if "时间" in frame.columns else "day"
    value_col = "收盘" if "收盘" in frame.columns else "close"
    points = {}
    for row in frame.to_dict("records"):
        stamp, value = _timestamp(row.get(time_col)), _number(row.get(value_col))
        if stamp and value is not None:
            clock = stamp[11:]
            if "09:30:00" <= clock <= "11:30:00" or "13:00:00" <= clock <= "15:00:00":
                points[stamp] = value
    if not points:
        raise ValueError("no valid minute points")
    latest_day = max(points)[:10]
    return [{"time": t, "value": points[t]} for t in sorted(points) if t[:10] == latest_day]


def _load_minutes(code, provider):
    import akshare as ak
    if provider == "em":
        with _AK_LOCK:
            return _points(ak.index_zh_a_hist_min_em(symbol=code[2:], period="1"))
    # stock_zh_a_minute 内部调用 stock_zh_a_daily,会构造 MiniRacer。
    with _V8_LOCK:
        return _points(ak.stock_zh_a_minute(symbol=code, period="1", adjust=""))


def _load_snapshot():
    with _AK_LOCK:
        import akshare as ak
        df = ak.stock_zh_index_spot_sina()
    result = {}
    for row in df.to_dict("records"):
        code = str(row.get("代码", ""))
        if code in dict(INDEXES):
            result[code] = {"last": _number(row.get("最新价")),
                            "pct_chg": _number(row.get("涨跌幅")),
                            "prev_close": _number(row.get("昨收"))}
    if not result:
        raise ValueError("empty snapshot")
    return result


def _load_flow(kind):
    with _AK_LOCK:
        from ..shared import data_gateway as dg
        fn = dg.get_industry_fund_flow if kind == "industry" else dg.get_concept_fund_flow
        df = _direct(fn)
        boards = [{"name": str(r["board_name"]), "net_inflow": _number(r["fund_flow"])}
                  for r in df.to_dict("records") if _number(r["fund_flow"]) is not None]
        if not boards:
            raise ValueError("empty fund flow")
        return {"boards": sorted(boards, key=lambda r: r["net_inflow"], reverse=True),
                "data_time": None}  # Provider exposes no source timestamp.


def _choose(primary, secondary, first, second, active=True):
    a = primary.get(first, active=active)
    if a["status"] not in ("unavailable", "stale"):
        return a
    b = secondary.get(second, active=active)
    if b["value"] is not None:
        return b
    return a


def market():
    now = _now()
    session = _session(now)
    active = session["state"] == "trading"
    snapshot = _snapshot.get(_load_snapshot, active=active)
    indexes = []
    for code, name in INDEXES:
        feed = _choose(*_minutes[code], lambda c=code: _load_minutes(c, "em"),
                       lambda c=code: _load_minutes(c, "sina"), active=active)
        points = feed["value"] or []
        quote = (snapshot["value"] or {}).get(code, {})
        stamp = points[-1]["time"] if points else None
        status, error = feed["status"], feed["error"]
        if quote and snapshot["status"] == "stale":
            status, error = "stale", snapshot["error"]
        if stamp:
            age = (now.replace(tzinfo=None) - datetime.fromisoformat(stamp)).total_seconds()
            if (active and age > 180) or (stamp[:10] < now.date().isoformat() and
                                         session["state"] in ("trading", "lunch", "closed")):
                status, error = "stale", error or "minute data is behind current session"
        indexes.append({"code": code, "name": name,
                        "last": quote.get("last") if quote else (points[-1]["value"] if points else None),
                        "pct_chg": quote.get("pct_chg"), "prev_close": quote.get("prev_close"),
                        "points": points, "data_time": stamp,
                        "source": feed["source"] + (" / " + snapshot["source"] if quote else ""),
                        "status": status, "error": error})
    flows = {}
    for kind, cache in _flows.items():
        feed = cache.get(lambda k=kind: _load_flow(k), active=active)
        flows[kind] = {**(feed["value"] or {"boards": [], "data_time": None}),
                       **{k: feed[k] for k in ("source", "status", "error")}}
    return {"session": session, "indexes": indexes, "fund_flow": flows,
            "server_time": now.isoformat()}


def _parse_news(df, important):
    items = {}
    source = "财联社" if important else "东方财富"
    for row in df.to_dict("records"):
        raw = row.get("发布时间")
        if row.get("发布日期") is not None:
            raw = f"{row['发布日期']} {raw}"
        stamp = _timestamp(raw)
        if not stamp or len(str(raw)) < 10:
            continue
        title = str(row.get("标题", "") or "")
        content = str(row.get("内容", row.get("摘要", "")) or "")
        if not title and not content:
            continue
        key = hashlib.sha256(f"{source}|{stamp}|{title}|{content}".encode()).hexdigest()[:24]
        url = row.get("链接")
        items[key] = {"id": key, "title": title, "content": content,
                      "published_at": stamp, "source": source,
                      "url": str(url) if isinstance(url, str) and url.startswith(("https://", "http://")) else None,
                      # Official font_2/font_3 styles are red; font_1 is bold only.
                      "important": important or _number(row.get("titleColor")) in (2, 3)}
    if not items:
        raise ValueError("empty news")
    ordered = sorted(items.values(), key=lambda r: (r["published_at"], r["id"]), reverse=True)[:200]
    return {"items": ordered, "data_time": ordered[0]["published_at"]}


def _load_news(important):
    if important:
        with _AK_LOCK:
            import akshare as ak
            return _parse_news(ak.stock_info_global_cls(symbol="重点"), True)
    import requests
    response = requests.get(
        "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
        params={"client": "web", "biz": "web_724", "fastColumn": "102",
                "sortEnd": "", "pageSize": "200", "req_trace": str(int(time.time() * 1000))},
        timeout=(3, 8))
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("code")) != "1" or not isinstance(payload.get("data"), dict):
        raise ValueError("invalid Eastmoney news response")
    rows = payload["data"].get("fastNewsList")
    if not isinstance(rows, list) or not rows:
        raise ValueError("empty Eastmoney news")
    df = pd.DataFrame(rows).rename(columns={"title": "标题", "summary": "摘要", "showTime": "发布时间"})
    df["链接"] = [f"https://finance.eastmoney.com/a/{r['code']}.html"
                if str(r.get("code", "")).isdigit() else None for r in rows]
    return _parse_news(df, False)


def news():
    feed = _choose(*_news, lambda: _load_news(False), lambda: _load_news(True))
    return {**(feed["value"] or {"items": [], "data_time": None}),
            **{k: feed[k] for k in ("source", "status", "error")},
            "server_time": _now().isoformat()}
