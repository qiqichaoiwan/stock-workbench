"""New additive tables only; deliberately independent of live holdings/trades."""
from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ..db import PortfolioBase


class ExecutionCard(PortfolioBase):
    __tablename__ = 'dual_core_execution_cards'
    __table_args__ = (UniqueConstraint('user_id', 'trade_date', 'phase'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    trade_date: Mapped[str] = mapped_column(String(10))
    phase: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class CardAudit(PortfolioBase):
    __tablename__ = 'dual_core_card_audit'
    __table_args__ = (UniqueConstraint('user_id', 'trade_date', 'phase', 'revision'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    trade_date: Mapped[str] = mapped_column(String(10))
    phase: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))
    recorded_at: Mapped[str] = mapped_column(String(40))
    snapshot: Mapped[str] = mapped_column(Text)


class VerifiedDayEvent(PortfolioBase):
    __tablename__ = 'dual_core_verified_day_events'
    __table_args__ = (UniqueConstraint('user_id', 'trade_date', 'reason', 'source', 'reference'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    trade_date: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(40))
    reference: Mapped[str] = mapped_column(String(128))
    observed_at: Mapped[str] = mapped_column(String(40))
    recorded_at: Mapped[str] = mapped_column(String(40))
