"""统一数据采集网关:akshare(主) + baostock(备) + efinance(快照)。

原则:
- 所有外部调用收敛在本模块;统一重试/限速/超时;接口变更只改这里
- 股票代码统一为 6 位字符串;baostock 等源的前缀在函数内部转换
- 任一源失败自动降级备源并返回 (data, source) 或抛出 last error 由上层记 task_log

单位与复权约定(落库前统一,下游可直接使用):
- 日线 OHLC/close: 各源均为**前复权**(akshare adjust="qfq" / baostock adjustflag="2" /
  腾讯/新浪 adjust="qfq");除权除息导致的跳空已由源端消除,禁止二次复权。
- volume(成交量): 统一为**股**。akshare 东财/新浪/腾讯各源返回**手**(1 手=100 股),
  本模块统一 ×100;baostock 返回**股**,保持不变。见 _normalise_volume。
- amount(成交额): 统一为**元**;total_mv/circ_mv 统一为**亿元**。见
  _normalise_amount/_normalise_market_cap,量级不符直接抛错不入库。
- pct_chg 缺失的源(sina/tx 个别版本)由同一前复权 close 推导,与源端口径一致(%)。

超时与退避约定(见 backend/app/config.py):
- resilient 源调用限制单次等待时间(fetch_timeout_seconds,默认 60s);
  超时计为一次失败并进入退避重试,不能终止底层调用或撤销其副作用。
- 每进程最多 8 个 daemon 工作线程,遗留调用占用容量直到结束,满时立即失败;
  嵌套调用在当前工作线程内执行,仅受外层等待期限约束,不再创建线程。
- 重试:fetch_retry_times × 指数退避(fetch_retry_wait_seconds × attempt)。
- 源级冷却:连续失败的源按 _SOURCE_DEFAULT_COOLDOWN 暂停调用(东财日线 10 分钟,
  其余 5 分钟),成功即清零;回填场景直走 tx/sina 不再逐股重试 akshare。
"""
import concurrent.futures
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ..config import settings

log = logging.getLogger("data_gateway")

TZ = ZoneInfo(settings.tz)


def now_tz() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now_tz().date()


# ---------------- 重试与限速 ----------------

_rate_lock = threading.Lock()
_last_request_ts = 0.0
_source_cooldown: dict[str, float] = {}  # 源级冷却:连续失败后暂停一段时间再用
# 各源冷却时长(秒):东财日线最严(限流重),其余默认 5 分钟;见模块 docstring
_SOURCE_DEFAULT_COOLDOWN = 300.0
_SOURCE_COOLDOWN_SECONDS = {
    "daily_hist_ak": 600.0,       # 东财限流后 10 分钟不再逐股重试,回填直走 tx/sina
    "industry_board_list": 600.0,  # 东财板块失败 10 分钟直接走新浪
    "concept_em": 600.0,     # 东财概念成分失败 10 分钟,直接走降级链(ths/sina/keyword)
    "concept_sina": 600.0,   # 新浪概念源失败同理,冷却期内跳过
    "spot_snapshot_ak": 300.0,
    "spot_snapshot_ef": 300.0,
    "spot_snapshot_sina": 300.0,
    "_index_daily_em": 300.0,
    "_index_daily_tx": 300.0,
    "_index_daily_sina": 300.0,
    "index_daily_em": 300.0,
    "index_daily_tx": 300.0,
    "index_daily_sina": 300.0,
}


def _rate_limit():
    global _last_request_ts
    with _rate_lock:
        wait = settings.fetch_rate_limit_seconds - (time.monotonic() - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.monotonic()


def _cooldown_check(source: str) -> bool:
    """该源处于冷却期返回 True(跳过本次调用)。"""
    until = _source_cooldown.get(source, 0.0)
    return time.monotonic() < until


def _cooldown_set(source: str, seconds: float | None = None):
    if seconds is None:
        seconds = _SOURCE_COOLDOWN_SECONDS.get(source, _SOURCE_DEFAULT_COOLDOWN)
    _source_cooldown[source] = time.monotonic() + seconds


def _cooldown_clear(source: str) -> None:
    """某源成功后清零冷却,下次可立即调用。"""
    _source_cooldown.pop(source, None)


_TIMEOUT_MAX_WORKERS = 8
_timeout_slots = threading.BoundedSemaphore(_TIMEOUT_MAX_WORKERS)
_timeout_context = threading.local()


def _run_with_timeout(fn, timeout: float, *args, **kwargs):
    """Bound the caller's wait, not the underlying source operation.

    Timed-out calls retain their slot until completion; capacity exhaustion
    fails immediately without queuing. Daemon threads do not delay process
    exit. Nested calls run inline under the outer wait limit to avoid deadlock;
    their own timeout is not independently enforced. None/<=0 disables timeout.
    """
    if timeout is None or timeout <= 0 or getattr(_timeout_context, "active", False):
        return fn(*args, **kwargs)
    slots = _timeout_slots
    if not slots.acquire(blocking=False):
        raise RuntimeError("source timeout worker capacity exhausted")
    fut = concurrent.futures.Future()

    def run():
        _timeout_context.active = True
        try:
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                fut.set_exception(exc)
            else:
                fut.set_result(result)
        finally:
            _timeout_context.active = False
            slots.release()

    try:
        worker = threading.Thread(target=run, name="data-gateway-timeout", daemon=True)
        worker.start()
    except BaseException:
        slots.release()
        raise
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        if fut.done():
            return fut.result()
        raise TimeoutError(f"source call timed out after {timeout}s; underlying call continues") from exc


def resilient(name: str, *, v8: bool = False):
    """统一重试装饰器:限速 + 有界等待超时 + 指数退避;全部失败抛最后一次异常。

    v8=True 的源会构造 py_mini_racer(V8)。V8 在两个线程并发初始化会让整个进程
    原生崩溃(异常无法被 Python 捕获),因此这类调用必须经 call_v8 全局串行。
    """

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last_err: Exception | None = None
            for attempt in range(1, settings.fetch_retry_times + 1):
                try:
                    _rate_limit()
                    if v8:
                        return call_v8(fn, *args, timeout=settings.fetch_timeout_seconds,
                                       **kwargs)
                    return _run_with_timeout(fn, settings.fetch_timeout_seconds,
                                            *args, **kwargs)
                except Exception as exc:  # noqa: BLE001 采集层必须吞掉任意源错误再重试
                    last_err = exc
                    wait = settings.fetch_retry_wait_seconds * attempt
                    log.warning("fetch %s failed (attempt %s/%s): %s, retry in %ss",
                                name, attempt, settings.fetch_retry_times, exc, wait)
                    time.sleep(wait)
            raise RuntimeError(f"[data_gateway] {name} 全部重试失败") from last_err

        wrapper.__v8_serialized__ = v8
        return wrapper

    return deco


# V8 调用全局串行锁:data_gateway 内部与驾驶舱等外部入口共用同一个锁,
# 否则跨模块并发仍可能同时构造 MiniRacer 并让进程崩溃。
_ak_serial = threading.RLock()


def ak_serialized() -> threading.RLock:
    """返回 V8 调用共享锁(供网关外的 akshare 直调入口复用)。"""
    return _ak_serial


def call_v8(fn, *args, timeout: float | None = None, **kwargs):
    """串行执行会构造 MiniRacer 的 akshare 调用。

    锁由工作线程持有直到底层调用真正结束(超时后也继续),因此一次超时的旧
    调用不会与后续调用并发构造 V8。等待锁与调用本身都受同一个 timeout 约束;
    无 timeout/<=0 时在当前线程内串行执行。
    """
    if timeout is None or timeout <= 0:
        with _ak_serial:
            return fn(*args, **kwargs)

    def locked():
        if not _ak_serial.acquire(timeout=timeout):
            raise TimeoutError(f"V8 serialization lock wait timed out after {timeout}s")
        try:
            return fn(*args, **kwargs)
        finally:
            _ak_serial.release()

    return _run_with_timeout(locked, timeout)


# ---------------- 交易日历 ----------------

@resilient("trade_calendar", v8=True)
def get_trade_calendar(start: str | None = None, end: str | None = None) -> list[date]:
    """返回升序交易日列表(date)。start/end: 'YYYYMMDD'。"""
    import akshare as ak

    start = start or (date(today().year - 1, 1, 1).strftime("%Y%m%d"))
    end = end or today().strftime("%Y%m%d")
    df = ak.tool_trade_date_hist_sina()
    days = pd.to_datetime(df["trade_date"]).dt.date
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    return [d for d in days if s <= d <= e]


def is_trade_day(d: date | None = None) -> bool:
    d = d or today()
    try:
        return d in set(get_trade_calendar(d.strftime("%Y%m%d"), d.strftime("%Y%m%d")))
    except Exception:  # 日历失败按工作日近似
        return d.weekday() < 5


def prev_trade_day(d: date | None = None, n: int = 1) -> date:
    days = get_trade_calendar()
    d = d or today()
    before = [x for x in days if x < d]
    return before[-n] if len(before) >= n else before[0] if before else d


def completed_trade_day(at: datetime | None = None) -> date:
    """Return the latest trading day whose close is actually available.

    Before the 15:00 close (including pre-market), today's quote is not
    complete; after close, today's quote is the completed business date.
    """
    now = at.astimezone(TZ) if at is not None and at.tzinfo else (at or now_tz())
    d = now.date()
    if is_trade_day(d) and now.time() >= datetime.strptime("15:00", "%H:%M").time():
        return d
    return prev_trade_day(d)


def validate_quote_date(quote_date: date, *, allow_today_completed: bool = True) -> date:
    """Reject dates that cannot be a completed quote date."""
    completed = completed_trade_day()
    if quote_date > completed or not is_trade_day(quote_date):
        raise ValueError(f"quote date {quote_date} is not a completed trading day (latest {completed})")
    return quote_date


def _normalise_amount(value, source: str) -> float:
    """All monetary amounts are stored in yuan; reject implausible units."""
    value = pd.to_numeric(value, errors="coerce")
    if pd.isna(value) or float(value) < 0:
        return 0.0
    value = float(value)
    # Eastmoney/efinance/sina amount fields are yuan. Do not silently accept
    # already-billion values, which would corrupt downstream ratios.
    if 0 < value < 1_000_000 and source in {"akshare", "efinance", "sina"}:
        raise ValueError(f"{source} amount appears not to be yuan: {value}")
    return value


def _normalise_market_cap(value, source: str) -> float:
    """All market caps are stored in 亿元, while providers return yuan."""
    value = pd.to_numeric(value, errors="coerce")
    if pd.isna(value) or float(value) < 0:
        return 0.0
    value = float(value)
    if value and value < 1_000_000:
        raise ValueError(f"{source} market cap appears not to be yuan: {value}")
    return value / 1e8


def _normalise_volume(value, source: str, *, lot_source: bool = True) -> float:
    """成交量统一为**股**。

    akshare 东财/新浪/腾讯返回**手**(1 手=100 股),调用方传 lot_source=True
    统一 ×100;baostock 返回**股**,传 lot_source=False 保持不变。
    负值/NaN 按缺失处理返回 0.0(由 _f 兜底写库,不抛错)。
    """
    value = pd.to_numeric(value, errors="coerce")
    if pd.isna(value) or float(value) < 0:
        return 0.0
    value = float(value)
    return value * 100.0 if lot_source else value


# ---------------- 股票基础信息与实时快照 ----------------

BOARD_RULES = (
    ("8", "北交所"), ("4", "北交所"), ("68", "科创板"), ("30", "创业板"), ("60", "主板"), ("00", "主板"),
)


def classify_board(code: str) -> str:
    for prefix, board in BOARD_RULES:
        if code.startswith(prefix):
            return board
    return "其他"


@resilient("spot_snapshot_ak")
def _spot_snapshot_ak() -> pd.DataFrame:
    """主源:akshare 东财快照。"""
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    col = {
        "代码": "code", "名称": "name", "最新价": "price", "最高价": "high", "最低价": "low",
        "今开": "open", "成交额": "amount", "换手率": "turnover", "涨跌幅": "pct_chg",
        "总市值": "total_mv", "流通市值": "circ_mv", "市盈率-动态": "pe", "市净率": "pb",
        "总股本": "total_shares",
    }
    df = df.rename(columns=col)
    return df


@resilient("spot_snapshot_ef")
def _spot_snapshot_ef() -> pd.DataFrame:
    """备源:efinance 东财快照(akshare 被限流时自动降级)。"""
    import efinance as ef

    df = ef.stock.get_realtime_quotes()
    col = {
        "股票代码": "code", "股票名称": "name", "最新价": "price", "最高": "high", "最低": "low",
        "今开": "open", "成交额": "amount", "换手率": "turnover", "涨跌幅": "pct_chg",
        "总市值": "total_mv", "流通市值": "circ_mv", "市盈率(动态)": "pe", "市净率": "pb",
    }
    df = df.rename(columns={k: v for k, v in col.items() if k in df.columns})
    return df


@resilient("spot_snapshot_sina")
def _spot_snapshot_sina() -> pd.DataFrame:
    """第三源:新浪全市场快照(东财双源被限流时的兜底,速度较慢)。"""
    import akshare as ak

    df = ak.stock_zh_a_spot()
    col = {
        "代码": "code", "名称": "name", "最新价": "price", "最高": "high", "最低": "low",
        "开盘价": "open", "成交额": "amount", "换手率": "turnover", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns={k: v for k, v in col.items() if k in df.columns})
    # 新浪代码带前缀 sh600519/sz000001/bj430047
    df["code"] = (df["code"].astype(str)
                  .str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6))
    df["pb"] = None  # 新浪源无PB,由下游容忍缺失
    return df


def get_spot_snapshot() -> pd.DataFrame:
    """全市场实时快照,三源依序降级(东财akshare → 东财efinance → 新浪)。
    列:code,name,open,high,low,price,amount,turnover,pct_chg,total_mv(亿),circ_mv(亿),pe,pe_ttm,pb。"""
    df, _ = get_spot_snapshot_with_source()
    return df


def get_spot_snapshot_with_source() -> tuple[pd.DataFrame, str]:
    """全市场实时快照 + 数据来源(向后兼容:get_spot_snapshot 只返回 DataFrame)。
    返回 (df, source),source∈{"akshare","efinance","sina"}。三源均失败抛错。"""
    last_err: Exception | None = None
    source_names = {"_spot_snapshot_ak": "akshare", "_spot_snapshot_ef": "efinance",
                    "_spot_snapshot_sina": "sina"}
    for fn in (_spot_snapshot_ak, _spot_snapshot_ef, _spot_snapshot_sina):
        if _cooldown_check(fn.__name__):
            log.info("spot source %s in cooldown, skip", fn.__name__)
            continue
        try:
            df = fn()
            keep = ["code", "name", "open", "high", "low", "price", "amount",
                    "turnover", "pct_chg", "total_mv", "circ_mv", "pe", "pb"]
            df = df[[c for c in keep if c in df.columns]].copy()
            df["code"] = df["code"].astype(str).str.zfill(6)
            for c in ("price", "amount", "total_mv", "circ_mv", "pe", "pb",
                      "turnover", "pct_chg", "open", "high", "low"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            source_name = source_names.get(fn.__name__, "akshare")
            if "amount" in df.columns:
                df["amount"] = df["amount"].map(lambda x: _normalise_amount(x, source_name))
            for c in ("total_mv", "circ_mv"):
                if c in df.columns:
                    df[c] = df[c].map(lambda x: _normalise_market_cap(x, source_name))
            df = df.dropna(subset=["price"])
            if len(df) > 100:
                if "pe" in df.columns:
                    df["pe_ttm"] = df["pe"]
                _cooldown_clear(fn.__name__)
                return df, source_name
            last_err = RuntimeError(f"{fn.__name__}: only {len(df)} valid rows")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            _cooldown_set(fn.__name__)  # 该源冷却(默认5分钟),避免连环重试触发更严限流
            log.warning("spot source %s failed: %s", fn.__name__, exc)
    raise RuntimeError(f"[data_gateway] spot_snapshot 三源均失败: {last_err}")


def get_st_set() -> set[str]:
    try:
        df = get_spot_snapshot()
        return set(df.loc[df["name"].str.contains("ST", na=False), "code"])
    except Exception as exc:  # noqa: BLE001
        log.warning("get_st_set failed: %s", exc)
        return set()


# ---------------- 指数成分 ----------------

INDEX_NAMES = {
    "000300": "沪深300", "000905": "中证500", "399673": "创业板50",
    "000688": "科创50", "000698": "科创100",
}


@resilient("index_members")
def get_index_members(index_code: str) -> pd.DataFrame:
    """指数成分股。列:code,name。优先中证官网,降级新浪/东财/国证。"""
    import akshare as ak

    errors: list[str] = []
    for fn in (ak.index_stock_cons_csindex, ak.index_stock_cons_sina, ak.index_stock_cons):
        try:
            _rate_limit()
            df = fn(index_code)
            name_col = "成分券名称" if "成分券名称" in df.columns else ("名称" if "名称" in df.columns else None)
            code_col = "成分券代码" if "成分券代码" in df.columns else ("代码" if "代码" in df.columns else None)
            if code_col is None:
                continue
            out = pd.DataFrame({
                "code": df[code_col].astype(str).str.zfill(6),
                "name": df[name_col].astype(str) if name_col else "",
            })
            out = out.drop_duplicates("code")
            if len(out) > 10:
                return out
            errors.append(f"{fn.__name__}: only {len(out)} rows")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fn.__name__}: {exc}")
    # 国证指数(399xxx,如创业板50 399673)专用
    if index_code.startswith("399"):
        try:
            _rate_limit()
            df = ak.index_detail_cni(symbol=index_code)
            out = pd.DataFrame({
                "code": df["样本代码"].astype(str).str.zfill(6),
                "name": df["样本简称"].astype(str),
            }).drop_duplicates("code")
            if len(out) > 10:
                return out
            errors.append(f"index_detail_cni: only {len(out)} rows")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"index_detail_cni: {exc}")
    raise RuntimeError(f"[data_gateway] index_members({index_code}) 失败: {errors}")


# ---------------- 日线(主 akshare 东财 / 备 baostock) ----------------

def _is_bse_code(code: str) -> bool:
    """北交所代码:4/8 开头的老代码与 920 开头的新代码。

    沪深 A 股不会以 4/8/92 开头,故按前缀即可区分;北交所需 bj 前缀,
    各源用错前缀会直接报错/返回空(回填曾因此整单 degraded)。
    """
    return code.startswith(("4", "8", "92"))


def _bs_code(code: str) -> str:
    if _is_bse_code(code):
        return f"bj.{code}"
    if code.startswith("6"):
        return f"sh.{code}"
    return f"sz.{code}"


@resilient("daily_hist_ak")
def _daily_hist_ak(code: str, start: str, end: str) -> pd.DataFrame:
    """东财个股日线:akshare 返回**前复权**(adjust="qfq")、成交量**手**。"""
    import akshare as ak

    df = ak.stock_zh_a_hist(symbol=code, period="daily",
                            start_date=start, end_date=end, adjust="qfq")
    df = df.rename(columns={
        "日期": "trade_date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount", "换手率": "turnover", "涨跌幅": "pct_chg",
    })
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["volume"] = df["volume"].map(lambda x: _normalise_volume(x, "akshare", lot_source=True))
    return df[["trade_date", "open", "high", "low", "close", "volume", "amount",
               "turnover", "pct_chg"]]


@resilient("daily_hist_bs")
def _daily_hist_bs(code: str, start: str, end: str) -> pd.DataFrame:
    """baostock 个股日线:adjustflag="2" 为**前复权**、成交量**股**(无需×100)。

    baostock 仅覆盖沪深,北交所(920xxx/4x/8x)直接返回空,避免无意义报错。
    """
    if _is_bse_code(code):
        return pd.DataFrame()
    import baostock as bs

    lg = bs.login()
    try:
        rs = bs.query_history_k_data_plus(
            _bs_code(code),
            "date,open,high,low,close,volume,amount,turn,pctChg",
            start_date=f"{start[:4]}-{start[4:6]}-{start[6:]}",
            end_date=f"{end[:4]}-{end[4:6]}-{end[6:]}",
            frequency="d", adjustflag="2",  # 前复权
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=rs.fields)
    finally:
        bs.logout()
    if df.empty:
        return df
    df = df.rename(columns={"date": "trade_date", "turn": "turnover", "pctChg": "pct_chg"})
    for c in ("open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # baostock volume 已是股(保持不变);其余数值列维持元/%口径
    df["volume"] = df["volume"].map(lambda x: _normalise_volume(x, "baostock", lot_source=False))
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def _sina_symbol(code: str) -> str:
    if _is_bse_code(code):
        return f"bj{code}"
    if code.startswith(("6", "5")):
        return f"sh{code}"
    return f"sz{code}"


@resilient("daily_hist_sina", v8=True)
def _daily_hist_sina(code: str, start: str, end: str) -> pd.DataFrame:
    """第三源:新浪个股日线(**前复权**,adjust="qfq";成交量**手**→统一为股)。

    本机对东财限流时的主力回填源。新浪 variants 可能缺 pct_change 列,
    此时由同一前复权 close 推导(与源端口径一致,%)。"""
    import akshare as ak

    df = ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust="qfq")
    df = df.rename(columns={"date": "trade_date", "volume": "volume", "amount": "amount",
                            "turnover": "turnover", "pct_change": "pct_chg", "涨跌幅": "pct_chg"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    # 新浪 variants may omit pct_change; derive it from the same adjusted close.
    if "pct_chg" not in df.columns or df["pct_chg"].isna().all():
        df["pct_chg"] = pd.to_numeric(df["close"], errors="coerce").pct_change().fillna(0.0) * 100
    df["volume"] = df["volume"].map(lambda x: _normalise_volume(x, "sina", lot_source=True))
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    df = df[(df["trade_date"] >= s) & (df["trade_date"] <= e)]
    return df[["trade_date", "open", "high", "low", "close", "volume", "amount",
               "turnover", "pct_chg"]]


@resilient("daily_hist_tx")
def _daily_hist_tx(code: str, start: str, end: str) -> pd.DataFrame:
    """第二源:腾讯个股日线(**前复权**,adjust="qfq";无换手率;成交量**手**→统一为股,
    用原 amount 列近似,pct_chg 自算)。"""
    import akshare as ak

    sym = (f"bj{code}" if _is_bse_code(code)
           else ("sh" if code.startswith(("6", "5")) else "sz") + code)
    df = ak.stock_zh_a_hist_tx(symbol=sym, start_date=start, end_date=end, adjust="qfq")
    df = df.rename(columns={"date": "trade_date", "amount": "volume"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["volume"] = df["volume"].map(lambda x: _normalise_volume(x, "tx", lot_source=True))
    df["amount"] = 0.0
    df["turnover"] = 0.0
    df["pct_chg"] = df["close"].astype(float).pct_change().fillna(0.0) * 100
    return df[["trade_date", "open", "high", "low", "close", "volume", "amount",
               "turnover", "pct_chg"]]


def get_daily_history(code: str, start: str, end: str | None = None,
                      source: str = "auto") -> tuple[pd.DataFrame, str]:
    """日线(前复权)。start/end: 'YYYYMMDD'。返回 (df, source)。

    source:
    - auto: 东财akshare → 腾讯tx → 新浪sina → baostock
    - akshare/tx/sina: 指定首选源,失败继续降级
    - backfill: 回填专用,跳过易被限流的东财(sina → tx → baostock);
      历史回填逐股请求时不再为东财重试白等 30s+。
    """
    end = end or today().strftime("%Y%m%d")
    if source == "backfill":
        try:
            return _daily_hist_sina(code, start, end), "sina"
        except Exception as exc:  # noqa: BLE001
            log.warning("daily sina failed for %s: %s", code, exc)
        try:
            df = _daily_hist_tx(code, start, end)
            if not df.empty:
                return df, "tx"
        except Exception as exc:  # noqa: BLE001
            log.warning("daily tx failed for %s: %s", code, exc)
        return _daily_hist_bs(code, start, end), "baostock"
    if source in ("auto", "akshare") and not _cooldown_check("daily_hist_ak"):
        try:
            df = _daily_hist_ak(code, start, end)
            _cooldown_clear("daily_hist_ak")
            return df, "akshare"
        except Exception as exc:  # noqa: BLE001
            log.warning("daily akshare failed for %s: %s", code, exc)
            _cooldown_set("daily_hist_ak")  # 东财限流后冷却(见 _SOURCE_COOLDOWN_SECONDS)
    elif source == "akshare":
        raise RuntimeError("[data_gateway] daily akshare 指定源不可用(冷却中)")
    if source in ("auto", "tx"):
        try:
            df = _daily_hist_tx(code, start, end)
            if not df.empty:
                return df, "tx"
        except Exception as exc:  # noqa: BLE001
            log.warning("daily tx failed for %s: %s", code, exc)
    if source in ("auto", "sina"):
        try:
            return _daily_hist_sina(code, start, end), "sina"
        except Exception as exc:  # noqa: BLE001
            log.warning("daily sina failed for %s: %s", code, exc)
    return _daily_hist_bs(code, start, end), "baostock"


@resilient("index_daily_em")
def _index_daily_em(index_code: str, start: str, end: str) -> pd.DataFrame:
    """主源:东财指数日线(含成交额)。index_code 形如 sh000001。"""
    import akshare as ak

    df = ak.stock_zh_index_daily_em(symbol=index_code, start_date=start, end_date=end)
    df = df.rename(columns={"date": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["trade_date", "open", "high", "low", "close", "amount"]]


@resilient("index_daily_tx")
def _index_daily_tx(index_code: str, start: str, end: str) -> pd.DataFrame:
    """备源一:腾讯指数日线(历史长,但只有成交量没有成交额)。

    注意:akshare 返回的第 6 列虽名为 amount,实际是成交量(手);与新浪
    volume 恰好相差 100 倍可验证。成交额只认东财源,这里置 0 避免把
    "成交量"当"成交额"写库(否则页面会少 3 个数量级,并污染市场总量)。
    """
    import akshare as ak

    df = ak.stock_zh_index_daily_tx(symbol=index_code)
    df = df.rename(columns={"date": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    df = df[(df["trade_date"] >= s) & (df["trade_date"] <= e)].copy()
    df["amount"] = 0.0
    return df[["trade_date", "open", "high", "low", "close", "amount"]]


@resilient("index_daily_sina", v8=True)
def _index_daily_sina(index_code: str, start: str, end: str) -> pd.DataFrame:
    """备源二:新浪指数日线(无成交额,amount 置 0)。"""
    import akshare as ak

    df = ak.stock_zh_index_daily(symbol=index_code)
    df = df.rename(columns={"date": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    df = df[(df["trade_date"] >= s) & (df["trade_date"] <= e)].copy()
    if "amount" not in df.columns:
        df["amount"] = 0.0
    return df[["trade_date", "open", "high", "low", "close", "amount"]]


def get_index_daily(index_code: str, start: str, end: str | None = None) -> pd.DataFrame:
    """指数日线,三源降级(东财→腾讯→新浪)。列:trade_date,open,high,low,close,amount。"""
    df, _ = get_index_daily_with_source(index_code, start, end)
    return df


def get_index_daily_with_source(index_code: str, start: str,
                                end: str | None = None) -> tuple[pd.DataFrame, str]:
    """指数日线 + 数据来源(向后兼容:get_index_daily 只返回 DataFrame)。
    返回 (df, source),source∈{"em","tx","sina"}。三源均失败抛错。"""
    end = end or today().strftime("%Y%m%d")
    last_err: Exception | None = None
    source_names = {"_index_daily_em": "em", "_index_daily_tx": "tx",
                    "_index_daily_sina": "sina"}
    for fn in (_index_daily_em, _index_daily_tx, _index_daily_sina):
        if _cooldown_check(fn.__name__):
            continue
        try:
            df = fn(index_code, start, end)
            if len(df) >= 30:
                _cooldown_clear(fn.__name__)
                return df, source_names.get(fn.__name__, fn.__name__)
            last_err = RuntimeError(f"{fn.__name__}: only {len(df)} rows")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            _cooldown_set(fn.__name__)
            log.warning("index daily source %s failed: %s", fn.__name__, exc)
    raise RuntimeError(f"[data_gateway] index_daily({index_code}) 三源均失败: {last_err}")


def get_market_total_amount(start: str, end: str | None = None) -> pd.DataFrame:
    """沪深京三市日成交额(元):sh000001 + sz399001 + bj899050(缺则跳过)。

    只有东财源提供成交额;腾讯/新浪备源 amount=0 会被跳过。沪、深任一
    缺失时抛错而不是用单市/混单位数据凑总市场分母——否则行业成交额占比、
    融资余额/成交额等相对指标会被放大约 2 倍甚至 1000 倍。北证数据可选。
    """
    frames = {}
    for idx in ("sh000001", "sz399001", "bj899050"):
        try:
            df = get_index_daily(idx, start, end)
            if df["amount"].sum() > 0:  # 新浪/腾讯源 amount=0,无法用
                frames[idx] = df.set_index("trade_date")["amount"]
        except Exception as exc:  # noqa: BLE001
            log.warning("index amount failed %s: %s", idx, exc)
    missing = [idx for idx in ("sh000001", "sz399001") if idx not in frames]
    if missing:
        raise RuntimeError(f"market total amount unavailable: missing {','.join(missing)}")
    total = pd.concat(frames.values(), axis=1).fillna(0).sum(axis=1)
    return total.rename("amount").reset_index()


# ---------------- 指数模块(P0-2:仪表盘指数条) ----------------

INDEX_DISPLAY: list[tuple[str, str]] = [
    ("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"),
    ("sh000688", "科创50"), ("sh000300", "沪深300"), ("sh000905", "中证500"),
]


def get_index_spot() -> pd.DataFrame:
    """指数实时点位兜底(ak.stock_zh_index_spot);失败返回空 df 不抛错。"""
    try:
        import akshare as ak

        _rate_limit()
        return ak.stock_zh_index_spot()
    except Exception as exc:  # noqa: BLE001
        log.warning("index spot failed: %s", exc)
        return pd.DataFrame()


# ---------------- 双核共振指数(同花顺 883957 / 883994) ----------------

# 东财/新浪都不提供这两个同花顺指数,只能直连 10jqka 公开 kline 接口(JSONP)。
# last1200.js 一次返回近 1200 根日线(883994 自 2022-05-25 共 1073 根即全历史),
# 足够算 10/20 周均线、5 日线与"历史最高点",无需分年拉取。
DUAL_CORE_DISPLAY: list[tuple[str, str]] = [
    ("883957", "同花顺全A(沪深京)"),
    ("883994", "昨日打首板表现"),
]

_THS_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_THS_CACHE_TTL = 300.0
_THS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://q.10jqka.com.cn/",
}


def _parse_ths_jsonp(text: str) -> dict:
    """解析 10jqka JSONP:`quotebridge_v6_line_48_883957_01_last1200({...})`。"""
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        raise ValueError("invalid ths jsonp payload")
    return json.loads(text[start + 1:end])


def get_ths_index_daily(code: str, bars: int = 1200,
                        use_cache: bool = True) -> pd.DataFrame:
    """同花顺指数日线:列 trade_date/open/high/low/close/volume/amount/name。

    数据源格式:`date,open,high,low,close,volume,amount`,单位元/股。
    行数不足 30 视为失败并抛错(避免把半截数据当完整历史入库);缓存 5 分钟,
    同一页面刷新/任务重跑只打一次接口。
    """
    key = f"{code}:{bars}"
    if use_cache and key in _THS_CACHE:
        ts, cached = _THS_CACHE[key]
        if time.time() - ts < _THS_CACHE_TTL:
            return cached.copy()
    import requests

    suffix = f"last{bars}" if bars else "last"
    url = f"https://d.10jqka.com.cn/v6/line/48_{code}/01/{suffix}.js"
    _rate_limit()
    resp = requests.get(url, headers=_THS_HEADERS, timeout=(3, 12))
    resp.raise_for_status()
    payload = _parse_ths_jsonp(resp.text)
    raw = payload.get("data") or ""
    rows: list[tuple] = []
    for line in raw.split(";"):
        parts = line.strip().split(",")
        if len(parts) < 5:
            continue
        try:
            d = pd.to_datetime(parts[0], format="%Y%m%d").date()
            o, h, l, c = (float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
            amount = float(parts[6]) if len(parts) > 6 else 0.0
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        rows.append((d, o, h, l, c, amount))
    if len(rows) < 30:
        raise ValueError(f"insufficient ths index bars for {code}: {len(rows)}")
    df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "amount"])
    df = df.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)
    df["name"] = str(payload.get("name") or code)
    _THS_CACHE[key] = (time.time(), df)
    return df.copy()


_THS_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}
_THS_QUOTE_TTL = 5.0


def get_ths_index_quote(code: str, use_cache: bool = True) -> dict:
    """同花顺指数实时报价(realhead):price/open/high/low/prev_close/amount/pct_chg。

    日线 kline 在收盘前后可能还没有当日 bar(实测 883994),realhead 已提供
    当日 OHLC 与成交额。字段编号来自同花顺公开 JSONP:10=现价、7=开盘、
    8=最高、9=最低、6=昨收、19=成交额(元)、199112=涨跌幅%。
    源返回不完整/非正数时抛错,由上层保持缺失,绝不补零。
    """
    cached = _THS_QUOTE_CACHE.get(code)
    if use_cache and cached and time.time() - cached[0] < _THS_QUOTE_TTL:
        return dict(cached[1])
    import requests

    url = f"https://d.10jqka.com.cn/v6/realhead/48_{code}/last.js"
    _rate_limit()
    resp = requests.get(url, headers=_THS_HEADERS, timeout=(3, 8))
    resp.raise_for_status()
    payload = _parse_ths_jsonp(resp.text)
    items = payload.get("items") or {}

    def number(key: str) -> float | None:
        try:
            return float(items.get(key))
        except (TypeError, ValueError):
            return None

    price, open_, high, low = (number(k) for k in ("10", "7", "8", "9"))
    prev_close, pct_chg, amount = (number(k) for k in ("6", "199112", "19"))
    if price is None or high is None or low is None:
        raise ValueError(f"incomplete ths quote for {code}")
    if min(price, high, low) <= 0 or high < price or low > price:
        raise ValueError(f"invalid ths quote range for {code}")
    quote = {
        "code": code, "price": price,
        "open": open_ if open_ is not None and open_ > 0 else price,
        "high": high, "low": low, "prev_close": prev_close,
        "amount": amount if amount is not None and amount > 0 else None,
        "pct_chg": pct_chg, "time": str(items.get("time") or ""),
        "source": "10jqka:realhead",
    }
    _THS_QUOTE_CACHE[code] = (time.time(), dict(quote))
    return quote


def get_index_spot_amount(codes: list[str] | None = None) -> dict[str, float]:
    """指数快照成交额(元),用于补齐日线备源缺失的 amount。

    腾讯/新浪指数日线只有成交量、没有成交额,收盘后写库的 amount 会是 0。
    新浪指数快照(stock_zh_index_spot_sina)的「成交额」为元、收盘后即当日
    全额(实测 sh000001 6724 亿 + sz399001 7885 亿 ≈ 全市场 1.46 万亿),
    可交叉验证市场总览。失败返回空 dict,不抛错(amount 保持 0 显示 -)。
    """
    try:
        import akshare as ak

        _rate_limit()
        df = ak.stock_zh_index_spot_sina()
    except Exception as exc:  # noqa: BLE001
        log.warning("index spot amount failed: %s", exc)
        return {}
    if df is None or df.empty or "成交额" not in df.columns:
        return {}
    want = set(codes or [c for c, _ in INDEX_DISPLAY])
    out: dict[str, float] = {}
    for row in df.to_dict("records"):
        code = str(row.get("代码") or "").strip()
        if code not in want:
            continue
        val = row.get("成交额")
        try:
            amount = float(val)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            out[code] = amount
    return out


# ---------------- 行业板块 ----------------

# 同花顺概念名单映射(本地可用,已验证 375 行):
# 固态电池308294/存储芯片307940/PCB概念308832/共封装光学(CPO)309049/
# 中芯国际概念308690/东数西算308828(≈AI算力)/算力租赁309068/减速器309000(≈电机)。
# THS 无成分函数,只能做名单存在性核验+占位,真实成分由本地展开(种子/行业/关键词)。
TRACK_THS_MAP: dict[str, str] = {
    # 键同时收录赛道名与 concept_board 名(两者不一致的赛道都要能命中):
    # 半导体/中芯概念→308690, 光模块/CPO概念→309049, 电机/减速器→309000,
    # AI算力/算力概念→308828(东数西算)。
    "固态电池": "308294",
    "半导体": "308690", "中芯概念": "308690",
    "PCB": "308832",
    "光模块": "309049", "CPO概念": "309049",
    "存储芯片": "307940",
    "电机": "309000", "减速器": "309000",
    "AI算力": "308828", "算力概念": "308828",
}

# 同花顺概念名单缓存(P2:东财被墙时逐赛道 sync 都要拉名单,TTL 内只拉一次)
_ths_cache_lock = threading.Lock()
_THS_LIST_CACHE: tuple[float, list[str], list[str]] | None = None  # (ts, names, codes)
_THS_LIST_TTL = 3600.0


def _ths_concept_list() -> tuple[list[str], list[str]]:
    """同花顺全量概念名单 → (names, codes),模块级缓存 TTL 3600s。

    名单拉取失败不缓存,异常上抛由 resilient 重试/降级。"""
    global _THS_LIST_CACHE
    with _ths_cache_lock:
        if _THS_LIST_CACHE is not None and \
                time.monotonic() - _THS_LIST_CACHE[0] < _THS_LIST_TTL:
            return _THS_LIST_CACHE[1], _THS_LIST_CACHE[2]
    import akshare as ak

    names_df = ak.stock_board_concept_name_ths()
    # 兼容列名变体:名字列含"名称"/"概念"/"板块"/"name",代码列含"代码"/"code"
    name_col = next((c for c in names_df.columns
                     if any(k in c for k in ("名称", "概念", "板块", "name"))), None)
    code_col = next((c for c in names_df.columns if "代码" in c or c == "code"), None)
    if name_col is None:
        raise RuntimeError(f"ths concept name list columns missing: {list(names_df.columns)}")
    names = names_df[name_col].astype(str).tolist()
    codes = names_df[code_col].astype(str).tolist() if code_col else [""] * len(names)
    with _ths_cache_lock:
        _THS_LIST_CACHE = (time.monotonic(), names, codes)
    return names, codes


@resilient("concept_board_ths", v8=True)
def _concept_board_cons_ths(board_name: str) -> pd.DataFrame:
    """同花顺名单核验:ak.stock_board_concept_name_ths() 名单存在即返回占位标记行。

    列:code,name,ths_code;code 取 "THS:"+ths_code 特殊标记(如 "THS:308294"),
    name 取 board_name。名单未命中抛错,由上游降级到 sina/keyword/local。
    真实成分由 universe_manager 按 TRACK_SEED_MAP 展开,网关层只管核验。
    名单拉取带模块级 3600s 缓存(见 _ths_concept_list),东财被墙批量同步时只拉一次。
    """
    names, codes = _ths_concept_list()
    ths_code: str | None = None
    # ① 内置映射优先:board_name 在 TRACK_THS_MAP 且该 code 在名单内
    mapped = TRACK_THS_MAP.get(board_name)
    if mapped and any(mapped in str(c) for c in codes):
        ths_code = mapped
    else:
        # ② 名字包含匹配(board_name 与名单名双向包含)
        for n, c in zip(names, codes):
            if board_name in n or n in board_name:
                ths_code = str(c)
                break
    if not ths_code:
        raise RuntimeError(f"ths concept board list misses {board_name!r}")
    return pd.DataFrame([{"code": f"THS:{ths_code}", "name": board_name,
                          "ths_code": ths_code}])


@resilient("concept_board_members")
def _concept_board_cons_em(board_name: str) -> pd.DataFrame:
    """东财概念板块成分(主源)。列:code,name。"""
    import akshare as ak

    df = ak.stock_board_concept_cons_em(symbol=board_name)
    return pd.DataFrame({
        "code": df["代码"].astype(str).str.zfill(6),
        "name": df["名称"].astype(str),
    })


@resilient("concept_board_sina")
def _concept_board_cons_sina(board_name: str) -> pd.DataFrame:
    """新浪降级:全概念列表名字包含匹配后,再调一次 cons_em。列:code,name。"""
    import akshare as ak

    names_df = ak.stock_board_concept_name_em()
    name_col = next((c for c in ("板块名称", "概念名称", "名称", "板块")
                     if c in names_df.columns), None)
    if name_col is None:
        raise RuntimeError("concept name list columns missing: "
                           f"{list(names_df.columns)}")
    candidates = names_df[name_col].astype(str).tolist()
    matched: str | None = None
    if board_name in candidates:
        matched = board_name
    else:
        for c in candidates:
            if board_name in c or c in board_name:
                matched = c
                break
    if matched is None:
        raise RuntimeError(f"no concept board name matches {board_name!r}")
    df = ak.stock_board_concept_cons_em(symbol=matched)
    return pd.DataFrame({
        "code": df["代码"].astype(str).str.zfill(6),
        "name": df["名称"].astype(str),
    })


# 关键词兜底用的全市场快照缓存(P2:批量赛道逐个 sync 时复用,避免重复全市场拉取)
_snap_cache_lock = threading.Lock()
_SNAP_CACHE: tuple[float, pd.DataFrame] | None = None  # (monotonic_ts, df)
_SNAP_CACHE_TTL = 120.0


def _cached_spot_snapshot() -> pd.DataFrame:
    """get_spot_snapshot 模块级 120s 缓存:concept keyword 兜底批量复用。"""
    global _SNAP_CACHE
    with _snap_cache_lock:
        if _SNAP_CACHE is not None and time.monotonic() - _SNAP_CACHE[0] < _SNAP_CACHE_TTL:
            return _SNAP_CACHE[1]
    df = get_spot_snapshot()
    with _snap_cache_lock:
        _SNAP_CACHE = (time.monotonic(), df)
    return df


# 选股池行情优先走新浪批量报价:一次 HTTP 请求即可拿到几十只股票的现价/涨跌幅,
# 比全市场快照(akshare 三源降级,单次常超 8 秒)快得多;全市场快照仅作降级。
_SINA_QUOTE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://finance.sina.com.cn",
}


def _sina_symbol(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("4", "8", "92")):  # 北交所(含 920 新号段)
        return f"bj{code}"
    if code[0] in "69":
        return f"sh{code}"
    return f"sz{code}"


def _quote_age(snapshot_at: str | None) -> float | None:
    """行情时间到现在的秒数;时间不可解析返回 None(不冒充实时)。"""
    try:
        at = datetime.fromisoformat(snapshot_at)
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=TZ)
    return max(0.0, round((now_tz() - at.astimezone(TZ)).total_seconds(), 1))


def _fetch_sina_batch_quotes(codes: set[str]) -> tuple[dict[str, dict], str | None]:
    """新浪批量报价:返回 ({code: quote}, 行情时间)。价格缺失(停牌/无数据)的代码跳过。"""
    import requests

    ordered = sorted(codes)
    quotes: dict[str, dict] = {}
    stamps: set[str] = set()
    for start in range(0, len(ordered), 50):
        chunk = ordered[start:start + 50]
        symbols = {_sina_symbol(code): code for code in chunk}
        _rate_limit()
        resp = requests.get("https://hq.sinajs.cn/list=" + ",".join(symbols),
                            headers=_SINA_QUOTE_HEADERS, timeout=(3, 6))
        resp.raise_for_status()
        resp.encoding = "gbk"
        for line in resp.text.strip().splitlines():
            if not line.startswith("var hq_str_") or '="' not in line:
                continue
            symbol, payload = line[len("var hq_str_"):].split('="', 1)
            code = symbols.get(symbol)
            fields = payload.rsplit('";', 1)[0].split(",")
            if code is None or len(fields) < 32:
                continue
            try:
                price, prev_close = float(fields[3]), float(fields[2])
                high, low = float(fields[4]), float(fields[5])
                amount = float(fields[9])
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            pct_chg = round((price / prev_close - 1) * 100, 2) if prev_close > 0 else None
            quotes[code] = {
                "price": round(price, 3),
                "pct_chg": pct_chg,
                "high": round(high, 3) if high > 0 else None,
                "low": round(low, 3) if low > 0 else None,
                "amount": round(amount, 0) if amount > 0 else None,
                "turnover": None,  # 新浪批量报价不含换手率
            }
            if fields[30] and fields[31]:
                stamps.add(f"{fields[30]}T{fields[31]}+08:00")
    if not quotes:
        raise RuntimeError("sina batch quotes returned no valid rows")
    # 取最新一条行情时间,避免停牌股残留的旧时间把整批行情标成过期
    return quotes, max(stamps) if stamps else None


# 选股池实时行情:全市场快照较重,进程内复用;过期后先返回旧快照并在后台刷新
_SPOT_QUOTE_LOCK = threading.Lock()
_SPOT_QUOTE_CACHE: tuple[float, str, pd.DataFrame, str] | None = None  # (monotonic, 快照时间, df, source)
_SPOT_QUOTE_REFRESHING = False
_SPOT_QUOTE_TTL = 15.0        # 15 秒内视为「实时」缓存
_SPOT_QUOTE_STALE_MAX = 900.0  # 超过 15 分钟不再当行情用,降级为已落库日线


def _refresh_spot_quotes() -> None:
    """后台刷新全市场快照;失败保留旧缓存,由下次调用继续尝试。"""
    global _SPOT_QUOTE_CACHE, _SPOT_QUOTE_REFRESHING
    try:
        df, source = get_spot_snapshot_with_source()
    except Exception as exc:  # noqa: BLE001 刷新失败不能影响已有快照
        log.warning("spot quote refresh failed: %s", exc)
        with _SPOT_QUOTE_LOCK:
            _SPOT_QUOTE_REFRESHING = False
        return
    with _SPOT_QUOTE_LOCK:
        _SPOT_QUOTE_CACHE = (time.monotonic(), now_tz().isoformat(), df, source)
        _SPOT_QUOTE_REFRESHING = False


def _first_spot_snapshot():
    """无缓存时同步拉一次快照;已有并发刷新则立即失败,不叠加外部请求。"""
    global _SPOT_QUOTE_CACHE, _SPOT_QUOTE_REFRESHING
    with _SPOT_QUOTE_LOCK:
        entry = _SPOT_QUOTE_CACHE
        if entry is not None:
            return entry
        if _SPOT_QUOTE_REFRESHING:
            raise RuntimeError("spot snapshot refresh in progress")
        _SPOT_QUOTE_REFRESHING = True
    try:
        df, source = get_spot_snapshot_with_source()
    except Exception:
        with _SPOT_QUOTE_LOCK:
            _SPOT_QUOTE_REFRESHING = False
        raise
    entry = (time.monotonic(), now_tz().isoformat(), df, source)
    with _SPOT_QUOTE_LOCK:
        _SPOT_QUOTE_CACHE = entry
        _SPOT_QUOTE_REFRESHING = False
    return entry


def get_spot_quotes(codes) -> tuple[dict[str, dict], str, str | None, float | None]:
    """按代码返回实时行情子集与快照元信息。

    返回 ({code: quote}, source, snapshot_at, age_seconds)。
    quote 字段:price/pct_chg/high/low/amount/turnover。
    优先新浪批量报价(单次请求,通常亚秒级);失败再降级为全市场快照缓存:
    已有缓存时立即返回(即使超过 15 秒 TTL),同时后台刷新,避免每次轮询
    都卡在一次 8 秒以上的全市场拉取上;只有完全无缓存时才同步拉取。
    全部源失败且无缓存时抛错,由调用方降级为已落库日线;价格/涨跌幅缺失
    (停牌等)的代码不出现在结果里,由调用方标记缺口。
    """
    global _SPOT_QUOTE_REFRESHING
    wanted = {str(code).zfill(6) for code in (codes or []) if str(code).strip()}
    if not wanted:
        return {}, "none", None, None

    try:
        quotes, snapshot_at = _fetch_sina_batch_quotes(wanted)
        return quotes, "sina", snapshot_at, _quote_age(snapshot_at)
    except Exception as exc:  # noqa: BLE001 批量源失败才降级全市场快照
        log.warning("sina batch quotes failed, fallback to full snapshot: %s", exc)

    with _SPOT_QUOTE_LOCK:
        entry = _SPOT_QUOTE_CACHE
        expired = entry is not None and time.monotonic() - entry[0] > _SPOT_QUOTE_TTL
        kick = expired and not _SPOT_QUOTE_REFRESHING
        if kick:
            _SPOT_QUOTE_REFRESHING = True
    if kick:
        threading.Thread(target=_refresh_spot_quotes,
                         name="spot-quote-refresh", daemon=True).start()
    if entry is None:
        entry = _first_spot_snapshot()
    age = time.monotonic() - entry[0]
    if age > _SPOT_QUOTE_STALE_MAX:
        raise RuntimeError(f"spot snapshot stale for {age:.0f}s")
    df = entry[2]

    def _rounded(value, digits):
        return None if value is None or pd.isna(value) else round(float(value), digits)

    quotes: dict[str, dict] = {}
    for row in df.loc[df["code"].isin(wanted)].to_dict("records"):
        price = row.get("price")
        if price is None or pd.isna(price) or float(price) <= 0:
            continue
        quotes[str(row["code"]).zfill(6)] = {
            "price": round(float(price), 3),
            "pct_chg": _rounded(row.get("pct_chg"), 2),
            "high": _rounded(row.get("high"), 3),
            "low": _rounded(row.get("low"), 3),
            "amount": _rounded(row.get("amount"), 0),
            "turnover": _rounded(row.get("turnover"), 2),
        }
    return quotes, entry[3], entry[1], round(age, 1)


def _concept_board_keyword(board_name: str, track=None, db=None) -> pd.DataFrame:
    """关键词兜底:全市场快照按名称/代码包含过滤。列:code,name。

    三层命中(任一即收录):① track.keywords 逗号切词逐词 name/industry 包含
    (大小写不敏感,str.contains case=False regex=False);② track.industry_index
    等值(industry == industry_index);③ board_name 自身。track 为 None 时保持原
    board_name 行为。
    快照本身无 industry 列:传入 db 时查 StockBasic 构建 code→industry 映射
    (import 放函数内避免循环依赖);无 db 时按空行业降级(行业匹配不生效)。"""
    snap = _cached_spot_snapshot()
    terms: list[str] = []
    if track is not None and getattr(track, "keywords", None):
        terms = [w.strip() for w in str(track.keywords).replace("，", ",")
                 .replace("、", ",").split(",") if w.strip()]
    if db is not None:
        from ..screener.models import StockBasic  # 函数内 import 避免循环依赖
        from sqlalchemy import select as _sa_select

        mapping = {c: (ind or "") for c, ind in db.execute(
            _sa_select(StockBasic.code, StockBasic.industry)).all()}
        ifc = snap["code"].map(mapping).fillna("")
    else:
        ifc = snap.get("industry", pd.Series("", index=snap.index))
    ifc = ifc.astype(str)
    name_s = snap["name"].astype(str)
    mask_kw = pd.Series(False, index=snap.index)
    for w in terms:
        mask_kw = (mask_kw
                   | name_s.str.contains(w, na=False, case=False, regex=False)
                   | ifc.str.contains(w, na=False, case=False, regex=False))
    mask_ind = pd.Series(False, index=snap.index)
    idx = getattr(track, "industry_index", "") if track is not None else ""
    if idx:
        mask_ind = ifc == idx
    mask_board = (name_s.str.contains(board_name, na=False, regex=False)
                  | snap["code"].astype(str).str.contains(board_name, na=False,
                                                          regex=False))
    mask = mask_kw | mask_ind | mask_board
    out = snap.loc[mask, ["code", "name"]].drop_duplicates("code").copy()
    if out.empty:
        raise RuntimeError(f"keyword fallback matched nothing for {board_name!r}")
    return out


def get_concept_board_members_with_source(board_name: str, track=None,
                                          db=None) -> tuple[pd.DataFrame, str]:
    """概念板块成分四源降级(东财→同花顺名单核验→新浪名字匹配→全市场关键词过滤)。

    ths 源只做"名单存在性核验+占位":返回 THS: 标记行(列 code,name,ths_code),
    真实成分由 universe_manager 按 TRACK_SEED_MAP 展开。
    东财/新浪源带源级冷却("concept_em"/"concept_sina",600s):失败进入冷却,
    冷却期内直接跳过该源走降级链,避免东财被墙时逐板块连环重试。
    db 透传给 keyword 兜底(查 StockBasic.industry,快照本身无该列)。
    返回 (df, source),source∈{"eastmoney","ths","sina","keyword"};四源皆失败才抛错。
    """
    last_err: Exception | None = None
    if _cooldown_check("concept_em"):
        log.info("concept source eastmoney in cooldown, skip for %s", board_name)
    else:
        try:
            df = _concept_board_cons_em(board_name)
            if len(df) > 0:
                _cooldown_clear("concept_em")
                return df, "eastmoney"
            last_err = RuntimeError("_concept_board_cons_em: empty result")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            _cooldown_set("concept_em")
            log.warning("concept board eastmoney failed for %s: %s", board_name, exc)
    try:
        df = _concept_board_cons_ths(board_name)
        if len(df) > 0:
            return df, "ths"
        last_err = RuntimeError("_concept_board_cons_ths: empty result")
    except Exception as exc:  # noqa: BLE001
        last_err = exc
        log.warning("concept board ths fallback failed for %s: %s", board_name, exc)
    if _cooldown_check("concept_sina"):
        log.info("concept source sina in cooldown, skip for %s", board_name)
    else:
        try:
            df = _concept_board_cons_sina(board_name)
            if len(df) > 0:
                _cooldown_clear("concept_sina")
                return df, "sina"
            last_err = RuntimeError("_concept_board_cons_sina: empty result")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            _cooldown_set("concept_sina")
            log.warning("concept board sina fallback failed for %s: %s", board_name, exc)
    try:
        df = _concept_board_keyword(board_name, track, db)
        return df, "keyword"
    except Exception as exc:  # noqa: BLE001
        last_err = exc
        log.warning("concept board keyword fallback failed for %s: %s", board_name, exc)
    raise RuntimeError(f"[data_gateway] concept_board({board_name}) 四源均失败: {last_err}")


def get_concept_board_members(board_name: str, track=None, db=None) -> pd.DataFrame:
    """东财概念板块成分(向后兼容:只返回 DataFrame,来源见 with_source 版本)。

    列:code,name(供赛道成分同步;四源皆失败时抛错由上层转友好提示)。"""
    df, _ = get_concept_board_members_with_source(board_name, track, db)
    return df


@resilient("industry_spot_sina")
def get_industry_spot_sina() -> pd.DataFrame:
    """新浪行业板块实时行情(东财被限流/被墙时的兜底,一次拿全部行业)。
    列:board_code,board_name,close,pct_chg,amount。"""
    import akshare as ak

    df = ak.stock_sector_spot(indicator="新浪行业")
    return pd.DataFrame({
        "board_code": df["label"].astype(str),
        "board_name": df["板块"].astype(str),
        "close": pd.to_numeric(df["平均价格"], errors="coerce"),
        "pct_chg": pd.to_numeric(df["涨跌幅"], errors="coerce"),
        "amount": pd.to_numeric(df["总成交额"], errors="coerce"),
    })


def get_industry_board_list() -> pd.DataFrame:
    """行业板块列表,东财优先(带源冷却)→ 新浪兜底。列:board_code,board_name。"""
    if not _cooldown_check("industry_board_list"):
        try:
            import akshare as ak

            df = ak.stock_board_industry_name_em()
            _cooldown_clear("industry_board_list")
            return pd.DataFrame({
                "board_code": df["板块代码"].astype(str),
                "board_name": df["板块名称"].astype(str),
            })
        except Exception as exc:  # noqa: BLE001
            _cooldown_set("industry_board_list")  # 东财失败冷却,直接走新浪
            log.warning("industry board em failed, fallback to sina: %s", exc)
    spot = get_industry_spot_sina()
    return spot[["board_code", "board_name"]]


@resilient("industry_hist")
def get_industry_hist(board_name: str, start: str, end: str | None = None) -> pd.DataFrame:
    """东财行业指数日线。列:trade_date,close,pct_chg,amount。"""
    import akshare as ak

    end = end or today().strftime("%Y%m%d")
    df = ak.stock_board_industry_hist_em(symbol=board_name, start_date=start,
                                         end_date=end, period="日线", adjust="")
    df = df.rename(columns={"日期": "trade_date", "收盘": "close", "涨跌幅": "pct_chg", "成交额": "amount"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["trade_date", "close", "pct_chg", "amount"]]


def _board_fund_flow(kind: str) -> pd.DataFrame:
    """官网排行图同源接口，f62 为今日主力净流入(元)，不是普通净流入。"""
    import requests

    code = {"industry": "m:90+s:4", "concept": "m:90+t:3"}[kind]
    response = requests.get(
        "https://data.eastmoney.com/dataapi/bkzj/getbkzj",
        params={"key": "f62", "code": code},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/bkzj/hy.html"},
        timeout=(3, 8),
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    rows = data.get("diff")
    if payload.get("rc") != 0 or not isinstance(rows, list) or not rows:
        raise ValueError("empty Eastmoney board fund flow")
    if len(rows) != int(data.get("total", -1)):
        raise ValueError("incomplete Eastmoney board fund flow")
    frame = pd.DataFrame(rows)
    if not {"f12", "f14", "f62"}.issubset(frame.columns):
        raise ValueError("Eastmoney fund flow columns missing")
    amounts = pd.to_numeric(frame["f62"], errors="coerce")
    if (amounts.isna() | amounts.isin([float("inf"), float("-inf")])).any():
        raise ValueError("invalid Eastmoney fund flow amounts")
    out = pd.DataFrame({"board_code": frame["f12"].astype(str),
                        "board_name": frame["f14"].astype(str),
                        "fund_flow": amounts / 1e8})
    if frame["f14"].isna().any() or out["board_name"].str.strip().eq("").any() or out["board_code"].duplicated().any():
        raise ValueError("invalid Eastmoney board identities")
    return out.sort_values("fund_flow", ascending=False).reset_index(drop=True)


@resilient("industry_fund_flow")
def get_industry_fund_flow() -> pd.DataFrame:
    """东财行业主力资金流(今日)。列:board_name,board_code,fund_flow(亿)。"""
    return _board_fund_flow("industry")


@resilient("concept_fund_flow")
def get_concept_fund_flow() -> pd.DataFrame:
    """东财概念主力资金流(今日)。列:board_name,board_code,fund_flow(亿)。"""
    return _board_fund_flow("concept")


@resilient("market_stat_spot")
def get_market_stat_spot() -> dict:
    """大盘统计实时聚合(go-stock MarketStatistic 思路):涨跌/涨跌停家数与成交额。

    优先 ak.stock_market_activity_legu(),失败降级 stock_zh_a_spot() 全市场聚合;
    字段名做兼容:含 "涨跌幅" 列时 advance=(pct>0).sum() 等;涨停用 pct>=9.9 近似
    并标注 source_note="limit_up≈pct>=9.9"。失败抛错由上层处理。"""
    import akshare as ak

    # 首选:乐咕市场活跃度(结构化涨跌家数)
    try:
        _rate_limit()
        df = ak.stock_market_activity_legu()
        cols = list(df.columns)
        item_col = next((c for c in cols if "项" in c or "名称" in c or "指标" in c), cols[0])
        num_col = next((c for c in cols if "数" in c or "值" in c), cols[-1])
        items = {str(r[0]).strip(): r[1] for r in df[[item_col, num_col]].itertuples(index=False)}

        def _pick(*keys: str) -> float:
            for k in keys:
                for name, val in items.items():
                    if k in name:
                        try:
                            return float(val)
                        except (TypeError, ValueError):
                            continue
            return 0.0

        advance = _pick("上涨")
        decline = _pick("下跌")
        limit_up = _pick("涨停")
        limit_down = _pick("跌停")
        amount = _pick("成交额", "成交金额")
        if advance or decline:
            return {"advance": int(advance), "decline": int(decline),
                    "limit_up": int(limit_up), "limit_down": int(limit_down),
                    "amount_total": float(amount) / 1e8 if amount > 1e6 else float(amount),
                    "source": "legu", "source_note": ""}
    except Exception as exc:  # noqa: BLE001 降级到全市场快照聚合
        log.warning("market stat legu failed, fallback to spot: %s", exc)

    _rate_limit()
    df = ak.stock_zh_a_spot()
    pct_col = next((c for c in ("涨跌幅", "涨跌幅(%)", "pct_chg") if c in df.columns), None)
    if pct_col is None:
        raise RuntimeError("market stat columns missing: no 涨跌幅 column")
    pct = pd.to_numeric(df[pct_col], errors="coerce").fillna(0.0)
    amt_col = next((c for c in ("成交额", "成交金额", "amount") if c in df.columns), None)
    amount_total = (pd.to_numeric(df[amt_col], errors="coerce").fillna(0.0).sum() / 1e8
                    if amt_col else 0.0)
    return {"advance": int((pct > 0).sum()), "decline": int((pct < 0).sum()),
            "limit_up": int((pct >= 9.9).sum()), "limit_down": int((pct <= -9.9).sum()),
            "amount_total": float(amount_total),
            "source": "spot", "source_note": "limit_up≈pct>=9.9"}


# ---------------- 财务(业绩报表批量, point-in-time 公告日) ----------------

@resilient("yjbb")
def get_earnings_by_period(period: str) -> pd.DataFrame:
    """全市场业绩报表。period='YYYYMMDD'(报告期,如 20240930)。
    列:code,name,ann_date,revenue_yoy,profit_yoy,roe,gross_margin,industry。"""
    import akshare as ak

    df = ak.stock_yjbb_em(date=period)
    df = df.rename(columns={
        "股票代码": "code", "股票简称": "name", "最新公告日期": "ann_date",
        "营业收入-同比增长": "revenue_yoy", "净利润-同比增长": "profit_yoy",
        "净资产收益率": "roe", "销售毛利率": "gross_margin", "所处行业": "industry",
        "每股经营现金流量": "ocf_per_share", "每股收益": "eps",
    })
    out = pd.DataFrame({
        "code": df["code"].astype(str).str.zfill(6),
        "name": df["name"].astype(str),
        "ann_date": pd.to_datetime(df["ann_date"], errors="coerce").dt.date,
        "revenue_yoy": pd.to_numeric(df.get("revenue_yoy"), errors="coerce"),
        "profit_yoy": pd.to_numeric(df.get("profit_yoy"), errors="coerce"),
        "roe": pd.to_numeric(df.get("roe"), errors="coerce"),
        "gross_margin": pd.to_numeric(df.get("gross_margin"), errors="coerce"),
        "industry": df.get("industry", "").astype(str),
        "ocf_per_share": pd.to_numeric(df.get("ocf_per_share"), errors="coerce"),
        "eps": pd.to_numeric(df.get("eps"), errors="coerce"),
    })
    # 现金含量代理:每股经营现金流/每股收益(≈经营现金流/净利润)
    out["ocf_to_profit"] = out.apply(
        lambda r: round(r["ocf_per_share"] / r["eps"], 3)
        if pd.notna(r["ocf_per_share"]) and pd.notna(r["eps"]) and abs(r["eps"]) > 1e-6 else None,
        axis=1,
    )
    return out


@resilient("zcfz")
def get_balance_sheet_by_period(period: str) -> pd.DataFrame:
    """全市场资产负债表摘要。列:code,debt_ratio(资产负债率%)。"""
    import akshare as ak

    df = ak.stock_zcfz_em(date=period)
    df = df.rename(columns={"股票代码": "code", "负债合计": "liab", "资产总计": "asset"})
    for c in ("liab", "asset"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["debt_ratio"] = (df["liab"] / df["asset"] * 100).round(2)
    return df[["code", "debt_ratio"]].assign(code=lambda x: x["code"].astype(str).str.zfill(6))


# ---------------- 估值(乐咕乐股个股 PE/PB/股息率 历史, 单股调用) ----------------

@resilient("stock_indicator_lg")
def get_stock_valuation_hist(code: str) -> pd.DataFrame:
    """个股 PE_TTM/PB/股息率 历史。列:trade_date,pe_ttm,pb,dv_ratio。"""
    import akshare as ak

    df = ak.stock_a_indicator_lg(symbol=code)
    df = df.rename(columns={"trade_date": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["trade_date", "pe_ttm", "pb", "dv_ratio"]]


# ---------------- 两融 ----------------

@resilient("margin_balance")
def get_margin_balance(days: int = 30) -> pd.DataFrame:
    """两融余额(沪深合计,亿)。列:trade_date,margin_balance。"""
    import akshare as ak

    start = (today() - timedelta(days=days * 2)).strftime("%Y%m%d")
    sse = ak.stock_margin_sse(start_date=start, end_date=today().strftime("%Y%m%d"))
    sse = sse.rename(columns={"信用交易日期": "trade_date", "融资余额": "margin_balance"})
    sse["trade_date"] = pd.to_datetime(sse["trade_date"]).dt.date
    sse["margin_balance"] = pd.to_numeric(sse["margin_balance"], errors="coerce") / 1e8
    out = sse[["trade_date", "margin_balance"]].groupby("trade_date").sum().reset_index()
    return out


# ---------------- 新闻(低频甄别式) ----------------

def get_cls_telegraph() -> pd.DataFrame:
    """财经快讯(财联社优先,东财全球快讯兜底)。列:publish_time,title,summary。"""
    import akshare as ak

    attempts = []
    if hasattr(ak, "stock_telegraph_cls"):
        attempts.append(lambda: ak.stock_telegraph_cls(symbol="全部"))
    attempts.append(ak.stock_info_global_em)     # 东财快讯:快且稳,优先
    if hasattr(ak, "stock_info_global_cls"):
        attempts.append(ak.stock_info_global_cls)

    last_err: Exception | None = None
    for fetch in attempts:
        try:
            _rate_limit()
            df = fetch()
            colmap = {"发布时间": "publish_time", "标题": "title",
                      "内容": "summary", "摘要": "summary"}
            rename = {k: v for k, v in colmap.items() if k in df.columns}
            df = df.rename(columns=rename)
            if "publish_time" not in df.columns or "title" not in df.columns:
                continue
            df["publish_time"] = pd.to_datetime(df["publish_time"], errors="coerce")
            df = df.dropna(subset=["title"])
            if len(df) > 0:
                return df[["publish_time", "title", "summary"] if "summary" in df.columns
                          else ["publish_time", "title"]]
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("news source %s failed: %s", getattr(fetch, "__name__", fetch), exc)
    raise RuntimeError(f"[data_gateway] cls_telegraph 全部源失败: {last_err}")


@resilient("stock_news")
def get_stock_news(code: str) -> pd.DataFrame:
    """东财个股新闻。列:publish_time,title,summary。"""
    import akshare as ak

    df = ak.stock_news_em(symbol=code)
    df = df.rename(columns={"发布时间": "publish_time", "新闻标题": "title", "新闻内容": "summary"})
    df["publish_time"] = pd.to_datetime(df["publish_time"], errors="coerce")
    return df[["publish_time", "title", "summary"]].head(20)


# ---------------- 龙虎榜(游资席位映射) ----------------

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def load_hot_money_seats() -> dict[str, dict]:
    """游资席位映射表:seat(营业部全称) → {trader, style, risk, kind}。

    kind 缺省按 trader/席位推断(游资/机构/北向),页面据此过滤机构与北向。
    读 backend/data/hot_money_seats.json;缺文件返回 {} 不抛错(调用方降级为空匹配)。"""
    path = _DATA_DIR / "hot_money_seats.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("hot_money_seats.json missing at %s, match disabled", path)
        return {}
    except Exception as exc:  # noqa: BLE001 脏 JSON 同样降级不抛错
        log.warning("hot_money_seats.json parse failed: %s", exc)
        return {}
    out: dict[str, dict] = {}
    for it in items if isinstance(items, list) else []:
        seat = str(it.get("seat", "")).strip()
        if seat:
            trader = str(it.get("trader", ""))
            out[seat] = {"trader": trader,
                         "style": str(it.get("style", "")),
                         "risk": str(it.get("risk", "")),
                         "kind": str(it.get("kind", "")).strip() or seat_kind(seat, trader)}
    return out


def seat_kind(seat: str, trader: str) -> str:
    """席位类别:游资 / 机构 / 北向(仅用于展示过滤,不影响匹配)。"""
    if trader == "北向资金" or seat in ("沪股通专用", "深股通专用"):
        return "北向"
    if trader in ("公募/私募机构", "机构席位") or seat == "机构专用":
        return "机构"
    return "游资"


def _coerce_trade_date_str(trade_date) -> str:
    """ak.stock_lhb_detail_em 的日期参数:统一为 YYYYMMDD 字符串。"""
    if trade_date is None:
        return today().strftime("%Y%m%d")
    if isinstance(trade_date, (datetime, date)):
        return trade_date.strftime("%Y%m%d")
    s = str(trade_date).strip().replace("-", "")
    return s if len(s) == 8 and s.isdigit() else today().strftime("%Y%m%d")


@resilient("lhb_bill")
def get_lhb_bill(trade_date=None) -> pd.DataFrame:
    """龙虎榜榜单(东财,单次请求,轻量):列 code/name/trade_date/close/pct_chg/lhb_net(万)。

    与席位明细拆层:榜单一次拉全,席位逐股调 get_lhb_seats——不再把
    "逐股×买卖 2 次"套进同一个 @resilient 60s 线程超时(上榜股 ≥30 只时
    整体必超时,3 次重试全废)。失败抛错由上层处理。"""
    import akshare as ak

    ds = _coerce_trade_date_str(trade_date)
    detail = ak.stock_lhb_detail_em(start_date=ds, end_date=ds)
    detail = detail.rename(columns={"代码": "code", "名称": "name", "收盘价": "close",
                                    "涨跌幅": "pct_chg", "龙虎榜净买额": "lhb_net"})
    detail["code"] = detail["code"].astype(str).str.zfill(6)
    # 上榜日列名在不同版本为"上榜日"/"上榜日期",缺失则用请求日
    date_col = next((c for c in ("上榜日", "上榜日期", "trade_date") if c in detail.columns), None)
    td = pd.to_datetime(detail[date_col], errors="coerce").dt.date if date_col else None
    rows = []
    for _, r in detail.iterrows():
        tdate = td.loc[r.name] if td is not None and pd.notna(td.loc[r.name]) \
            else datetime.strptime(ds, "%Y%m%d").date()
        if hasattr(tdate, "date"):
            tdate = tdate.date()
        rows.append({"code": r["code"], "name": str(r.get("name", "")),
                     "trade_date": tdate, "close": float(r.get("close") or 0.0),
                     "pct_chg": float(r.get("pct_chg") or 0.0),
                     "lhb_net": float(r.get("lhb_net") or 0.0) / 1e4})
    return pd.DataFrame(rows, columns=["code", "name", "trade_date", "close",
                                       "pct_chg", "lhb_net"])


def get_lhb_seats(code: str, trade_date, flag: str = "买入") -> list[str]:
    """单股席位明细(东财,逐股调用,不套外层 resilient 整体超时)。

    flag: "买入"/"卖出"。失败抛错,调用方逐股 try/except 隔离。"""
    import akshare as ak

    _rate_limit()
    seat_df = _run_with_timeout(
        ak.stock_lhb_stock_detail_em, settings.fetch_timeout_seconds,
        symbol=code, date=_coerce_trade_date_str(trade_date), flag=flag)
    if seat_df is not None and "交易营业部名称" in seat_df.columns:
        return seat_df["交易营业部名称"].astype(str).str.strip().tolist()
    return []


def get_lhb_daily(trade_date=None) -> pd.DataFrame:
    """龙虎榜日明细 = 榜单(get_lhb_bill) + 逐股席位(get_lhb_seats)。

    逐股席位失败只留空列表不拖垮整批;整体不包 @resilient(60s 超时
    覆盖逐股循环在繁忙交易日必超时,是结构性缺陷,见审计 P1#7)。"""
    detail = get_lhb_bill(trade_date)
    rows = []
    for _, r in detail.iterrows():
        buy_seats: list[str] = []
        sell_seats: list[str] = []
        for flag, bucket in (("买入", buy_seats), ("卖出", sell_seats)):
            try:
                bucket.extend(get_lhb_seats(r["code"], r["trade_date"], flag))
            except Exception as exc:  # noqa: BLE001 单股席位失败不拖垮整批
                log.warning("lhb seats %s %s failed: %s", r["code"], flag, exc)
        rows.append({**r.to_dict(), "buy_seats": buy_seats, "sell_seats": sell_seats})
    return pd.DataFrame(rows, columns=["code", "name", "trade_date", "close", "pct_chg",
                                       "lhb_net", "buy_seats", "sell_seats"])


# ---------------- 商品期货主力连续(涨价周期跟踪) ----------------

DEFAULT_COMMODITY_SERIES: list[dict] = [
    {"series_name": "碳酸锂", "sina_symbol": "LC0", "related_track": "固态电池"},
    {"series_name": "电解铜", "sina_symbol": "CU0", "related_track": "有色金属"},
    {"series_name": "电解铝", "sina_symbol": "AL0", "related_track": "有色金属"},
    {"series_name": "螺纹钢", "sina_symbol": "RB0", "related_track": "建材"},
    {"series_name": "甲醇", "sina_symbol": "MA0", "related_track": "化工"},
    {"series_name": "PTA", "sina_symbol": "TA0", "related_track": "化工"},
    {"series_name": "生猪", "sina_symbol": "LM0", "related_track": "养殖"},
]


@resilient("commodity_main")
def get_commodity_history(sina_symbol: str, days: int = 260) -> pd.DataFrame:
    """期货主力连续日线。列:trade_date,close。"""
    import akshare as ak

    start = (today() - timedelta(days=days)).strftime("%Y%m%d")
    df = ak.futures_main_sina(symbol=sina_symbol, start_date=start,
                              end_date=today().strftime("%Y%m%d"))
    df = df.rename(columns={"日期": "trade_date", "收盘价": "close"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["trade_date", "close"]].dropna()


# ---------------- 同花顺行业指数(参照系,备选) ----------------

@resilient("ths_industry_index", v8=True)
def get_ths_industry_daily(ths_code: str, start: str, end: str | None = None) -> pd.DataFrame:
    """同花顺行业指数日线(如 881121 半导体)。列:trade_date,close,amount。"""
    import akshare as ak

    end = end or today().strftime("%Y%m%d")
    df = ak.stock_board_industry_index_ths(symbol=ths_code, start_date=start, end_date=end)
    df = df.rename(columns={"日期": "trade_date", "收盘价": "close", "成交额": "amount"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["trade_date", "close", "amount"]]
