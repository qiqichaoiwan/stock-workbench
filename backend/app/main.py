"""FastAPI 应用入口。"""
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import ROOT_DIR, settings
from .db import create_all, missing_tables
from .portfolio.api import router as portfolio_router
from .screener.api import router as screener_router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _is_production() -> bool:
    """显式生产标记才判生产:ENV/APP_ENV=production 或 docker-compose 的 APP_ENV。"""
    return os.environ.get("APP_ENV", os.environ.get("ENV", "")).lower() == "production"


def _require_strong_jwt() -> None:
    """JWT 无强密钥直接拒绝启动(防默认密钥上线)。

    - 默认值/空:任何环境直接抛错(开发机也必须显式设置 JWT_SECRET,
      与 docker-compose 的 `${JWT_SECRET:?...}` 强制要求一致)。
    - 非默认但长度 <32:生产拒绝启动;开发/测试仅警告(兼容现有测试密钥)。
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


_require_strong_jwt()

app = FastAPI(title=settings.app_name, version="0.1.0")

# CORS(默认同源,显式配置才放行):
# - 前后端同源部署(后端托管 dist)时无需跨域,此处仅放行 vite dev(5173)与本机调试。
# - 生产前后端分离部署必须显式配置 CORS_ORIGINS=https://app.example.com(逗号分隔),
#   为空则默认同源(不放行任意外域);allow_credentials=True 下不使用通配符。
_extra_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if _is_production() and not _extra_origins:
    logging.getLogger("startup").info(
        "CORS:生产同源部署,不额外放行外域(如需分离部署请配置 CORS_ORIGINS)。")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"] + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    # 缺表检查先行:只告警不静默重建——生产缺表多为备份/挂载问题,先告警再人工处理。
    # 自检→ 备份(data/selectmoney.db 每日 sqlite3 .backup,保留 7 天,恢复前停容器)
    #   → 确认无误后由运维手动补表/跑迁移脚本,不要自动 create_all 覆盖。
    try:
        missing = missing_tables()
    except Exception as exc:  # noqa: BLE001 库文件不可读等情况同样告警
        logging.getLogger("startup").error(
            "数据库自检失败(可能 data 卷未挂载):%s。请先检查备份再启动,不要空库覆盖生产数据。", exc)
        missing = []
    if missing:
        logging.getLogger("startup").warning(
            "检测到缺失表 %s:已存在的表不会被改动;请先备份 data/selectmoney.db "
            "(sqlite3 .backup),再补表/跑迁移,详见 config.py 备份指引。", missing)
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


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}


app.include_router(portfolio_router)
app.include_router(screener_router)

# 前端构建产物存在时由后端统一托管(单服务部署)
_frontend_dist = ROOT_DIR / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
