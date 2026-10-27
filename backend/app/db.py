"""数据库会话与建表。

两个限界上下文(Portfolio / Screener)各自持有独立 Base,
表共用同一数据库文件,但模型互不引用——上下文隔离落在 ORM 层。

并发写入说明(SQLite):
- WAL + busy_timeout=5000:读不阻塞写,短事务并发可重试 5s,而非立即
  "database is locked"。这是**单写进程内的并发缓冲**,不是多写者方案。
- 多进程/多 worker 写同一 .db 文件仍会冲突损坏(见 config.py 约束),
  生产保持单容器单进程;后台任务另有 _file_lock 跨进程守卫。
- 切 MySQL/Postgres 后 busy_timeout PRAGMA 自动跳过(仅 sqlite),
  连接池默认 QueuePool,按需调 pool_size/max_overflow。

建表与迁移检查:
- create_all() 只建缺失表(Portfolio/Screener 两套 metadata),**不做迁移**。
  已存在的表结构变更不会自动生效——改模型后必须手写迁移脚本升级。
- missing_tables():启动自检用,列出 metadata 有但库里缺的表;缺表时
  main.py 在日志告警并指引备份恢复(不静默重建覆盖生产数据)。
- 备份指引见 config.py 模块 docstring(每日 sqlite3 .backup,保留 7 天)。
"""
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, inspect
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


def missing_tables() -> list[str]:
    """列出 metadata 中定义但数据库缺失的表(迁移检查用,不建表)。"""
    from .portfolio import models as _pf_models  # noqa: F401
    from .portfolio import dual_core_checklist_models as _card_models  # noqa: F401
    from .screener import models as _scr_models  # noqa: F401

    existing = set(inspect(engine).get_table_names())
    wanted: set[str] = set()
    for base in (PortfolioBase, ScreenerBase):
        wanted.update(base.metadata.tables.keys())
    return sorted(wanted - existing)


def create_all() -> None:
    from .portfolio import models as _pf_models  # noqa: F401
    from .portfolio import dual_core_checklist_models as _card_models  # noqa: F401
    from .screener import models as _scr_models  # noqa: F401

    PortfolioBase.metadata.create_all(engine)
    ScreenerBase.metadata.create_all(engine)
