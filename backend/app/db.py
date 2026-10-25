"""数据库会话与建表。

两个限界上下文(Portfolio / Screener)各自持有独立 Base,
表共用同一数据库文件,但模型互不引用——上下文隔离落在 ORM 层。
"""
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class PortfolioBase(DeclarativeBase):
    """持仓/用户上下文"""


class ScreenerBase(DeclarativeBase):
    """选股侦测上下文"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    pool_pre_ping=True,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Session:
    """FastAPI 依赖:请求级会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    from .portfolio import models as _pf_models  # noqa: F401
    from .screener import models as _scr_models  # noqa: F401

    PortfolioBase.metadata.create_all(engine)
    ScreenerBase.metadata.create_all(engine)
