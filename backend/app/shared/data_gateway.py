"""统一数据网关:akshare(主) + baostock(备) + efinance(快照)。

原则:
- 所有外部调用收敛在本模块;统一重试/限速;接口变更只改这里
- 股票代码统一为 6 位字符串;baostock 等源的前缀在函数内部转换
- 任一源失败自动降级备源并返回 (data, source) 或抛出 last error 由上层记 task_log
"""
import logging
import threading
import time
from datetime import date, datetime, timedelta
from functools import wraps
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


def _cooldown_set(source: str, seconds: float = 300.0):
    _source_cooldown[source] = time.monotonic() + seconds


def resilient(name: str):
    """统一重试装饰器:限速 + 指数退避;全部失败抛最后一次异常。"""

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last_err: Exception | None = None
            for attempt in range(1, settings.fetch_retry_times + 1):
                try:
                    _rate_limit()
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 采集层必须吞掉任意源错误再重试
                    last_err = exc
                    wait = settings.fetch_retry_wait_seconds * attempt
                    log.warning("fetch %s failed (attempt %s/%s): %s, retry in %ss",
                                name, attempt, settings.fetch_retry_times, exc, wait)
                    time.sleep(wait)
            raise RuntimeError(f"[data_gateway] {name} 全部重试失败") from last_err

        return wrapper

    return deco


# ---------------- 交易日历 ----------------

@resilient("trade_calendar")
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
    last_err: Exception | None = None
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
            df = df.dropna(subset=["price"])
            if len(df) > 100:
                if "pe" in df.columns:
                    df["pe_ttm"] = df["pe"]
                _source_cooldown.pop(fn.__name__, None)
                return df
            last_err = RuntimeError(f"{fn.__name__}: only {len(df)} valid rows")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            _cooldown_set(fn.__name__, 300.0)  # 该源冷却5分钟,避免连环重试触发更严限流
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

def _bs_code(code: str) -> str:
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith(("8", "4")):
        return f"bj.{code}"
    return f"sz.{code}"


@resilient("daily_hist_ak")
def _daily_hist_ak(code: str, start: str, end: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_a_hist(symbol=code, period="daily",
                            start_date=start, end_date=end, adjust="qfq")
    df = df.rename(columns={
        "日期": "trade_date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount", "换手率": "turnover", "涨跌幅": "pct_chg",
    })
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["trade_date", "open", "high", "low", "close", "volume", "amount",
               "turnover", "pct_chg"]]


@resilient("daily_hist_bs")
def _daily_hist_bs(code: str, start: str, end: str) -> pd.DataFrame:
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
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def _sina_symbol(code: str) -> str:
    if code.startswith(("6", "5")):
        return f"sh{code}"
    return f"sz{code}"


@resilient("daily_hist_sina")
def _daily_hist_sina(code: str, start: str, end: str) -> pd.DataFrame:
    """第三源:新浪个股日线(前复权)。本机对东财限流时的主力回填源。"""
    import akshare as ak

    df = ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust="qfq")
    df = df.rename(columns={"date": "trade_date", "volume": "volume", "amount": "amount",
                            "turnover": "turnover", "pct_change": "pct_chg"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    df = df[(df["trade_date"] >= s) & (df["trade_date"] <= e)]
    return df[["trade_date", "open", "high", "low", "close", "volume", "amount",
               "turnover", "pct_chg"]]


@resilient("daily_hist_tx")
def _daily_hist_tx(code: str, start: str, end: str) -> pd.DataFrame:
    """第二源:腾讯个股日线(前复权;无换手率,成交量以 amount 列近似,pct_chg 自算)。"""
    import akshare as ak

    sym = ("sh" if code.startswith(("6", "5")) else "sz") + code
    df = ak.stock_zh_a_hist_tx(symbol=sym, start_date=start, end_date=end, adjust="qfq")
    df = df.rename(columns={"date": "trade_date", "amount": "volume"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["amount"] = 0.0
    df["turnover"] = 0.0
    df["pct_chg"] = df["close"].astype(float).pct_change().fillna(0.0) * 100
    return df[["trade_date", "open", "high", "low", "close", "volume", "amount",
               "turnover", "pct_chg"]]


def get_daily_history(code: str, start: str, end: str | None = None,
                      source: str = "auto") -> tuple[pd.DataFrame, str]:
    """日线(前复权)。start/end: 'YYYYMMDD'。返回 (df, source)。四源降级:
    东财akshare → 腾讯tx → 新浪sina → baostock(东财连续失败自动冷却,回填场景直走tx)。"""
    end = end or today().strftime("%Y%m%d")
    if source in ("auto", "akshare") and not _cooldown_check("daily_hist_ak"):
        try:
            df = _daily_hist_ak(code, start, end)
            _source_cooldown.pop("daily_hist_ak", None)
            return df, "akshare"
        except Exception as exc:  # noqa: BLE001
            log.warning("daily akshare failed for %s: %s", code, exc)
            _cooldown_set("daily_hist_ak", 600.0)  # 东财限流后10分钟内不再逐股重试
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
    """备源一:腾讯指数日线(含成交额,历史长)。"""
    import akshare as ak

    df = ak.stock_zh_index_daily_tx(symbol=index_code)
    df = df.rename(columns={"date": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    df = df[(df["trade_date"] >= s) & (df["trade_date"] <= e)]
    return df[["trade_date", "open", "high", "low", "close", "amount"]]


@resilient("index_daily_sina")
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
    end = end or today().strftime("%Y%m%d")
    last_err: Exception | None = None
    for fn in (_index_daily_em, _index_daily_tx, _index_daily_sina):
        if _cooldown_check(fn.__name__):
            continue
        try:
            df = fn(index_code, start, end)
            if len(df) >= 30:
                return df
            last_err = RuntimeError(f"{fn.__name__}: only {len(df)} rows")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            _cooldown_set(fn.__name__, 300.0)
            log.warning("index daily source %s failed: %s", fn.__name__, exc)
    raise RuntimeError(f"[data_gateway] index_daily({index_code}) 三源均失败: {last_err}")


def get_market_total_amount(start: str, end: str | None = None) -> pd.DataFrame:
    """沪深京三市日成交额(元):sh000001 + sz399001 + bj899050(缺则跳过)。"""
    frames = []
    for idx in ("sh000001", "sz399001", "bj899050"):
        try:
            df = get_index_daily(idx, start, end)
            if df["amount"].sum() > 0:  # 新浪源 amount=0,无法用
                frames.append(df.set_index("trade_date")["amount"])
        except Exception as exc:  # noqa: BLE001
            log.warning("index amount failed %s: %s", idx, exc)
    if not frames:
        raise RuntimeError("market total amount unavailable")
    total = pd.concat(frames, axis=1).fillna(0).sum(axis=1)
    return total.rename("amount").reset_index()


# ---------------- 行业板块 ----------------

@resilient("concept_board_members")
def get_concept_board_members(board_name: str) -> pd.DataFrame:
    """东财概念板块成分。列:code,name(供赛道成分同步;被墙时抛错由上层转友好提示)。"""
    import akshare as ak

    df = ak.stock_board_concept_cons_em(symbol=board_name)
    return pd.DataFrame({
        "code": df["代码"].astype(str).str.zfill(6),
        "name": df["名称"].astype(str),
    })


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
            _source_cooldown.pop("industry_board_list", None)
            return pd.DataFrame({
                "board_code": df["板块代码"].astype(str),
                "board_name": df["板块名称"].astype(str),
            })
        except Exception as exc:  # noqa: BLE001
            _cooldown_set("industry_board_list", 600.0)  # 东财失败冷却10分钟,直接走新浪
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


@resilient("industry_fund_flow")
def get_industry_fund_flow() -> pd.DataFrame:
    """东财行业主力资金流(今日)。列:board_name,fund_flow(亿)。"""
    import akshare as ak

    df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    col_name = "名称" if "名称" in df.columns else "行业名称"
    col_flow = "今日主力净流入-净额" if "今日主力净流入-净额" in df.columns else None
    if col_flow is None:
        raise RuntimeError("fund flow columns missing")
    out = pd.DataFrame({
        "board_name": df[col_name].astype(str),
        "fund_flow": pd.to_numeric(df[col_flow], errors="coerce") / 1e8,
    })
    return out


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

@resilient("ths_industry_index")
def get_ths_industry_daily(ths_code: str, start: str, end: str | None = None) -> pd.DataFrame:
    """同花顺行业指数日线(如 881121 半导体)。列:trade_date,close,amount。"""
    import akshare as ak

    end = end or today().strftime("%Y%m%d")
    df = ak.stock_board_industry_index_ths(symbol=ths_code, start_date=start, end_date=end)
    df = df.rename(columns={"日期": "trade_date", "收盘价": "close", "成交额": "amount"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["trade_date", "close", "amount"]]
