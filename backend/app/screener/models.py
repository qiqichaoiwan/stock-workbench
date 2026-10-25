"""Screener 上下文数据模型(系统级,无用户概念)。

设计红线(来自 Invest 知识库回测教训):
- 指标优先「相对偏离度」口径,禁止绝对数值阈值作主信号
- 每个策略/信号在 strategy_card 挂证据链,状态机 unverified→testing→verified/deprecated
"""
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import ScreenerBase


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IndexMembership(ScreenerBase):
    """候选域:核心指数成分按日快照,支持退市/调仓留痕。"""

    __tablename__ = "scr_index_membership"
    __table_args__ = (UniqueConstraint("index_code", "stock_code", "in_date", name="uq_idx_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    index_code: Mapped[str] = mapped_column(String(12), index=True)   # 000300/000905/399673/000688/000698
    index_name: Mapped[str] = mapped_column(String(32), default="")
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    stock_name: Mapped[str] = mapped_column(String(32), default="")
    in_date: Mapped[date] = mapped_column(Date, index=True)
    out_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Track(ScreenerBase):
    """赛道标签(固态电池/半导体/PCB/光模块/电机…),映射概念板块与关键词,可动态新增。"""

    __tablename__ = "scr_track"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    concept_board: Mapped[str] = mapped_column(String(64), default="")   # 东财概念板块名
    industry_index: Mapped[str] = mapped_column(String(32), default="")  # 参照行业指数代码
    keywords: Mapped[str] = mapped_column(Text, default="")              # 新闻关键词,逗号分隔
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TrackMembership(ScreenerBase):
    """赛道成分映射:一只股票可属多个赛道。"""

    __tablename__ = "scr_track_membership"
    __table_args__ = (UniqueConstraint("track_id", "stock_code", name="uq_track_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(Integer, index=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")    # manual/concept_board
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StockBasic(ScreenerBase):
    __tablename__ = "scr_stock_basic"

    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), default="")
    industry: Mapped[str] = mapped_column(String(32), default="")       # 东财行业
    board: Mapped[str] = mapped_column(String(16), default="")          # 主板/创业板/科创板/北交所
    list_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")   # active/delisted
    total_mv: Mapped[float] = mapped_column(Float, default=0.0)         # 总市值(亿)
    circ_mv: Mapped[float] = mapped_column(Float, default=0.0)          # 流通市值(亿)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class DailyQuote(ScreenerBase):
    __tablename__ = "scr_daily_quote"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_quote"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float, default=0.0)
    high: Mapped[float] = mapped_column(Float, default=0.0)
    low: Mapped[float] = mapped_column(Float, default=0.0)
    close: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)           # 元
    turnover: Mapped[float] = mapped_column(Float, default=0.0)         # 换手率%
    pct_chg: Mapped[float] = mapped_column(Float, default=0.0)
    total_mv: Mapped[float] = mapped_column(Float, default=0.0)         # 亿
    adj_factor: Mapped[float] = mapped_column(Float, default=1.0)


class IntradaySnapshot(ScreenerBase):
    """盘中半小时快照(仅候选域);收盘汇总入日线后可定期清理。"""

    __tablename__ = "scr_intraday_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    snap_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float, default=0.0)
    high: Mapped[float] = mapped_column(Float, default=0.0)
    low: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)


class FinancialIndicator(ScreenerBase):
    """按报告期财务(point-in-time: ann_date 首次披露日)。行业内分位在计算层做。"""

    __tablename__ = "scr_financial_indicator"
    __table_args__ = (UniqueConstraint("code", "period", name="uq_fin"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    period: Mapped[str] = mapped_column(String(8), index=True)          # 20241231
    ann_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    roe: Mapped[float] = mapped_column(Float, default=0.0)              # 加权ROE%
    gross_margin: Mapped[float] = mapped_column(Float, default=0.0)     # 毛利率%
    net_margin: Mapped[float] = mapped_column(Float, default=0.0)       # 净利率%
    revenue_yoy: Mapped[float] = mapped_column(Float, default=0.0)
    profit_yoy: Mapped[float] = mapped_column(Float, default=0.0)
    ocf_to_profit: Mapped[float] = mapped_column(Float, default=0.0)    # 经营现金流/净利润
    debt_ratio: Mapped[float] = mapped_column(Float, default=0.0)       # 资产负债率%
    inventory_days: Mapped[float] = mapped_column(Float, default=0.0)
    receivable_days: Mapped[float] = mapped_column(Float, default=0.0)
    deducted_profit: Mapped[float] = mapped_column(Float, default=0.0)  # 扣非净利润(亿)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Valuation(ScreenerBase):
    __tablename__ = "scr_valuation"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_val"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    pe_ttm: Mapped[float] = mapped_column(Float, default=0.0)
    pb: Mapped[float] = mapped_column(Float, default=0.0)
    dv_ratio: Mapped[float] = mapped_column(Float, default=0.0)         # 股息率%
    pe_pct_5y: Mapped[float] = mapped_column(Float, default=0.0)        # 5年分位%
    pb_pct_5y: Mapped[float] = mapped_column(Float, default=0.0)


class MarketState(ScreenerBase):
    """市场状态:趋势标签 + PM02 周期档位(草案,unverified)+ 仓位建议(滞回)。"""

    __tablename__ = "scr_market_state"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    trend_label: Mapped[str] = mapped_column(String(16), default="")    # 强势/震荡/弱势/极端底部
    cycle_stage: Mapped[str] = mapped_column(String(16), default="")    # 衰退/复苏/下行/过热
    stage_pending: Mapped[str] = mapped_column(String(16), default="")  # 滞回:待确认的新档位
    pending_count: Mapped[int] = mapped_column(Integer, default=0)      # 滞回:连续确认次数
    position_range: Mapped[str] = mapped_column(String(16), default="") # 如 50%-70%
    breadth: Mapped[float] = mapped_column(Float, default=0.0)          # 站上MA200个股占比%
    amount_pct_60d: Mapped[float] = mapped_column(Float, default=0.0)   # 两市成交额60日分位%
    valuation_pct: Mapped[float] = mapped_column(Float, default=0.0)    # 全A PE 5年分位%
    margin_ratio: Mapped[float] = mapped_column(Float, default=0.0)     # 融资余额/成交额
    score_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class IndustryDaily(ScreenerBase):
    """行业/赛道日度监测:含 SC-U08 拥挤度偏离(RATIO_SCORE,相对口径)。"""

    __tablename__ = "scr_industry_daily"
    __table_args__ = (UniqueConstraint("industry_code", "trade_date", name="uq_ind_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry_code: Mapped[str] = mapped_column(String(32), index=True)
    industry_name: Mapped[str] = mapped_column(String(64), default="")
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[float] = mapped_column(Float, default=0.0)
    pct_chg: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)           # 元
    market_amount: Mapped[float] = mapped_column(Float, default=0.0)    # 全市场成交额(元)
    ratio: Mapped[float] = mapped_column(Float, default=0.0)            # 成交额占比%
    ratio_ma120: Mapped[float] = mapped_column(Float, default=0.0)
    ratio_score: Mapped[float] = mapped_column(Float, default=0.0)      # SC-U08 偏离度
    avoid_until: Mapped[date | None] = mapped_column(Date, nullable=True)  # 回避名单到期日
    fund_flow: Mapped[float] = mapped_column(Float, default=0.0)        # 主力净流入(亿)
    flow_streak: Mapped[int] = mapped_column(Integer, default=0)        # 连续净流入天数
    mom_20: Mapped[float] = mapped_column(Float, default=0.0)           # 20日动量%
    mom_60: Mapped[float] = mapped_column(Float, default=0.0)
    pe_pct: Mapped[float] = mapped_column(Float, default=0.0)           # 行业PE 5年分位%
    heat_score: Mapped[float] = mapped_column(Float, default=0.0)       # 景气综合评分
    news_heat: Mapped[float] = mapped_column(Float, default=0.0)


class CommodityPrice(ScreenerBase):
    """重点商品价格序列与涨价周期信号(锂/铜/化工/猪/内存颗粒…)。"""

    __tablename__ = "scr_commodity_price"
    __table_args__ = (UniqueConstraint("series_name", "trade_date", name="uq_cmdty"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_name: Mapped[str] = mapped_column(String(64), index=True)
    related_track: Mapped[str] = mapped_column(String(64), default="")
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    mom_20: Mapped[float] = mapped_column(Float, default=0.0)           # 20日动量%
    up_days: Mapped[int] = mapped_column(Integer, default=0)            # 连续上涨天数
    signal: Mapped[str] = mapped_column(String(16), default="")         # 启动/持续/回落/无
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class TechnicalSignal(ScreenerBase):
    """个股技术信号日志(相对口径:量能偏离、相对强弱)。"""

    __tablename__ = "scr_technical_signal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    signal_type: Mapped[str] = mapped_column(String(32), index=True)
    # bottom_fractal(底分型) / red_streak(连红) / volume_surge(量能偏离) / rs_weak(弱于行业) / rs_strong(强于行业)
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NewsItem(ScreenerBase):
    """甄别后的新闻(低频,过滤日内噪声)。"""

    __tablename__ = "scr_news_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), default="cls")      # cls/eastmoney
    publish_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="")       # 主题归类
    sentiment: Mapped[str] = mapped_column(String(16), default="中性")   # 利好/利空/中性
    related_industry: Mapped[str] = mapped_column(String(64), default="")
    related_codes: Mapped[str] = mapped_column(String(512), default="")
    heat: Mapped[float] = mapped_column(Float, default=0.0)
    rule_hits: Mapped[str] = mapped_column(Text, default="{}")
    screened: Mapped[bool] = mapped_column(Boolean, default=False)      # 是否通过噪声甄别
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StockPool(ScreenerBase):
    """选股池:main=总池,track=赛道池。出池留档不物理删。"""

    __tablename__ = "scr_stock_pool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    pool_type: Mapped[str] = mapped_column(String(16), default="main")  # main/track
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(32), default="")
    in_price: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reasons: Mapped[str] = mapped_column(Text, default="[]")            # 命中理由 JSON
    rating: Mapped[str] = mapped_column(String(16), default="hold")     # buy/hold/sell/avoid
    status: Mapped[str] = mapped_column(String(16), default="in", index=True)  # in/out
    out_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    out_reason: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PoolPerformance(ScreenerBase):
    __tablename__ = "scr_pool_performance"
    __table_args__ = (UniqueConstraint("code", "pool_entry_date", "trade_date", name="uq_pool_perf"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    pool_entry_date: Mapped[date] = mapped_column(Date)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[float] = mapped_column(Float, default=0.0)
    cum_pct: Mapped[float] = mapped_column(Float, default=0.0)          # 自入选累计涨跌%
    benchmark_pct: Mapped[float] = mapped_column(Float, default=0.0)    # 同期全A累计


class StrategyCard(ScreenerBase):
    """策略生命周期卡:unverified→testing→verified/deprecated,证据链必填。"""

    __tablename__ = "scr_strategy_card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_code: Mapped[str] = mapped_column(String(32), unique=True)     # 如 SC-U08
    title: Mapped[str] = mapped_column(String(128), default="")
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="unverified", index=True)
    evidence: Mapped[str] = mapped_column(Text, default="{}")           # 回测数字/来源链接
    params: Mapped[str] = mapped_column(Text, default="{}")             # 可执行参数
    source_note: Mapped[str] = mapped_column(String(256), default="")
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class StrategyParams(ScreenerBase):
    """全局策略参数(KV,后台表单可调)。key 见 strategy_engine.PARAM_DEFAULTS。"""

    __tablename__ = "scr_strategy_params"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(String(256), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class TaskLog(ScreenerBase):
    __tablename__ = "scr_task_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_name: Mapped[str] = mapped_column(String(64), index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/success/failed
    detail: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
