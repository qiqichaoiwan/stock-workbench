"""FastAPI 应用入口。"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import ROOT_DIR, settings
from .db import create_all
from .portfolio.api import router as portfolio_router
from .screener.api import router as screener_router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

if settings.jwt_secret == "change-me-in-production":
    logging.getLogger("startup").warning(
        "JWT_SECRET 使用默认值!生产部署必须在 .env 或环境变量中设置强随机值。")

app = FastAPI(title=settings.app_name, version="0.1.0")

# 前后端同源部署(后端托管 dist)时无需跨域;此处仅为 vite dev(5173)与本机调试放行。
# 生产自定义域名通过环境变量 CORS_ORIGINS 追加(逗号分隔)。
_extra_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"] + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
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
