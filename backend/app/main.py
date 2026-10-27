"""FastAPI 应用入口。"""
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import ROOT_DIR, settings
from .db import create_all, missing_tables
from .portfolio.api import router as portfolio_router
from .portfolio.dual_core_checklist_api import router as dual_core_cards_router
from .screener.api import router as screener_router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _is_production() -> bool:
    """显式生产标记才判生产:ENV/APP_ENV=production 或 docker-compose 的 APP_ENV。"""
    return os.environ.get("APP_ENV", os.environ.get("ENV", "")).lower() == "production"


def _low_entropy(secret: str) -> bool:
    """朴素熵检查:字符种类过少,或整串只是某个短片段循环重复(如 aaaa…/abcabc…)。"""
    if len(set(secret)) < 8:
        return True
    return re.fullmatch(r"(.{1,16}?)\1+", secret) is not None


def _require_strong_jwt() -> None:
    """JWT 无强密钥直接拒绝启动(防默认密钥上线)。

    - 默认值/空:任何环境直接抛错(开发机也必须显式设置 JWT_SECRET,
      与 docker-compose 的 `${JWT_SECRET:?...}` 强制要求一致)。
    - 非默认但长度 <32:生产拒绝启动;开发/测试仅警告(兼容现有测试密钥)。
    - 生产额外拒绝低熵密钥(长度够但字符种类极少/循环重复)。
    """
    if settings.is_using_default_jwt:
        raise RuntimeError(
            "拒绝启动:必须设置强 JWT_SECRET("
            "可用 openssl rand -hex 32 生成,经 .env 或环境变量 JWT_SECRET 传入),"
            "禁止使用默认值 change-me-in-production。"
        )
    if len(settings.jwt_secret) < 32:
        if _is_production():
            raise RuntimeError("拒绝启动:生产 JWT_SECRET 长度不足 32 字符,请换强随机值。")
        logging.getLogger("startup").warning(
            "JWT_SECRET 长度不足 32 字符,仅开发/测试允许;生产部署请换强随机值。")
    elif _is_production() and _low_entropy(settings.jwt_secret):
        raise RuntimeError(
            "拒绝启动:生产 JWT_SECRET 熵过低(字符种类少或循环重复),请用 openssl rand -hex 32 重新生成。")


_require_strong_jwt()


def _startup() -> None:
    """启动自检:库可用性 → 补建缺表 → 清理僵尸任务 → 默认策略参数 → 调度器。

    create_all 只新建 metadata 中缺失的表,不会修改已存在表的列/索引,
    因此不能替代迁移;生产缺表仍会先告警(多为挂载/备份问题),由运维确认后补表。
    """
    log = logging.getLogger("startup")
    try:
        missing = missing_tables()
    except Exception as exc:  # noqa: BLE001 库文件不可读等情况同样告警
        log.error(
            "数据库自检失败(可能 data 卷未挂载):%s。请先检查备份再启动,不要空库覆盖生产数据。", exc)
        missing = []
    if missing:
        log.warning(
            "检测到缺失表 %s:create_all 将补建空表,已存在表的结构不会改动;"
            "请先备份 data/selectmoney.db(sqlite3 .backup),生产缺表建议人工核对后再启动。", missing)
    create_all()
    from .task_runner import cleanup_zombie_tasks

    cleanup_zombie_tasks()
    from .screener.strategy_engine import ensure_defaults

    db = __import__("app.db", fromlist=["SessionLocal"]).SessionLocal()
    try:
        ensure_defaults(db)
    finally:
        db.close()
    from .scheduler import start_scheduler

    start_scheduler()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _startup()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

# CORS(默认同源,显式配置才放行):
# - 前后端同源部署(后端托管 dist)时无需跨域,此处仅放行 vite dev(5173)与本机调试。
# - 生产前后端分离部署必须显式配置 CORS_ORIGINS=https://app.example.com(逗号分隔),
#   为空则默认同源(不放行任意外域);allow_credentials=True 下不使用通配符。
# - 5173 仅开发期需要(非生产才放行),生产不放行任何本机来源。
_extra_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
_dev_origins = [] if _is_production() else ["http://localhost:5173", "http://127.0.0.1:5173"]
if _is_production() and not _extra_origins:
    logging.getLogger("startup").info(
        "CORS:生产同源部署,不额外放行外域(如需分离部署请配置 CORS_ORIGINS)。")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_dev_origins + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}


app.include_router(portfolio_router)
app.include_router(dual_core_cards_router)
app.include_router(screener_router)

# 前端构建产物存在时由后端统一托管(单服务部署)
_frontend_dist = ROOT_DIR / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
