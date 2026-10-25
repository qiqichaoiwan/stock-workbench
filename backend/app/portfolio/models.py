"""Portfolio 上下文数据模型(多租户):用户、LLM配置、持仓、交易记录、AI分析、周报。

持仓表内嵌 G03 三阶段止损引擎字段:
- breakeven_price: 真实保本价(含买入佣金/过户费/卖出佣金/印花税)
- stop_stage: A=初始止损(禁上移) B=已移至保本价 C=创新高后动态抬升
- stop_price: 当前生效止损价,只许上移(禁止浮亏放宽)
"""
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import PortfolioBase


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(PortfolioBase):
    __tablename__ = "pf_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    nickname: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin / analyst / user
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class InviteCode(PortfolioBase):
    """一次性邀请码:管理员生成,凭码注册;防开放注册滥用。"""

    __tablename__ = "pf_invite_code"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    note: Mapped[str] = mapped_column(String(128), default="")          # 用途备注
    created_by: Mapped[int] = mapped_column(Integer, default=0)         # 生成者 user_id
    used_by: Mapped[int] = mapped_column(Integer, nullable=True)        # 使用者 user_id
    used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UserLlmConfig(PortfolioBase):
    """用户自配大模型(OpenAI 兼容协议);api_key 加密存储,接口只回传掩码。"""

    __tablename__ = "pf_user_llm_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("pf_user.id"), unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(256))
    api_key_cipher: Mapped[str] = mapped_column(String(512), default="")
    api_key_masked: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.3)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Holding(PortfolioBase):
    __tablename__ = "pf_holding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("pf_user.id"), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    buy_price: Mapped[float] = mapped_column(Float)
    buy_date: Mapped[date] = mapped_column(Date)

    # G03 止损引擎
    breakeven_price: Mapped[float] = mapped_column(Float, default=0.0)   # 真实保本价
    initial_stop_price: Mapped[float] = mapped_column(Float, default=0.0)  # 初始止损(禁上移)
    stop_price: Mapped[float] = mapped_column(Float, default=0.0)          # 当前生效止损
    stop_stage: Mapped[str] = mapped_column(String(2), default="A")        # A/B/C
    stop_rule: Mapped[str] = mapped_column(String(32), default="percent")  # percent/atr/swing/ema
    trail_param: Mapped[float] = mapped_column(Float, default=8.0)         # 规则参数(N% 或 ATR倍数)
    highest_price: Mapped[float] = mapped_column(Float, default=0.0)       # 持仓期间最高价
    risk_r: Mapped[float] = mapped_column(Float, default=0.0)              # 初始风险 R = 开仓价-初始止损

    take_profit_price: Mapped[float] = mapped_column(Float, default=0.0)   # 参考止盈位
    status: Mapped[str] = mapped_column(String(16), default="holding")     # holding/sold
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class TradeRecord(PortfolioBase):
    """卖出审计:用户点击「已卖出」后写入,支持复盘与胜率统计。"""

    __tablename__ = "pf_trade_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("pf_user.id"), index=True)
    holding_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    code: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    buy_price: Mapped[float] = mapped_column(Float)
    buy_date: Mapped[date] = mapped_column(Date)
    sell_price: Mapped[float] = mapped_column(Float)
    sell_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)            # 含费后盈亏额
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)        # 含费后收益率
    holding_days: Mapped[int] = mapped_column(Integer, default=0)
    exit_trigger: Mapped[str] = mapped_column(String(32), default="manual")  # stop_loss/stop_breakeven/trailing/manual/ai_ref
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AiAnalysis(PortfolioBase):
    """AI 分析快照(参考建议,非决策):个股分析、卖点预测。"""

    __tablename__ = "pf_ai_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("pf_user.id"), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="holding_analysis")  # holding_analysis/sell_point/daily_report/weekly_report
    content: Mapped[str] = mapped_column(Text, default="")       # markdown
    sell_points: Mapped[str] = mapped_column(Text, default="")   # 卖出点位预测(JSON)
    model_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WeeklyReport(PortfolioBase):
    __tablename__ = "pf_weekly_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("pf_user.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    week_end: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(128), default="")
    content: Mapped[str] = mapped_column(Text, default="")          # markdown
    stats_json: Mapped[str] = mapped_column(Text, default="{}")     # 本周持仓/交易统计
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
