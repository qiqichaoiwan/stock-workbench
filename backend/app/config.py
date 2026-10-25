"""全局配置:数据库、JWT、加密、LLM 兜底、采集参数。

约定:所有阈值类参数只提供默认值,运行期以 screener 策略参数(JSON)为准,
本文件只管基础设施,不承载策略逻辑。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
ROOT_DIR = BASE_DIR.parent                          # 项目根


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "智能选股工作台"
    # SQLite:两个上下文共用一个库文件,模型表用前缀区分;预留 DATABASE_URL 切 MySQL
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'selectmoney.db'}"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    # Fernet 主密钥(生产必须由 .env 提供;为空则首次启动自动生成写入 .env.key)
    fernet_key: str = ""

    # 系统级兜底 LLM(OpenAI 兼容协议);用户自配优先
    default_llm_base_url: str = "https://api.deepseek.com/v1"
    default_llm_api_key: str = ""
    default_llm_model: str = "deepseek-chat"

    # 数据采集
    intraday_interval_minutes: int = 30   # 盘中半小时快照
    fetch_retry_times: int = 3
    fetch_retry_wait_seconds: int = 5
    fetch_rate_limit_seconds: float = 0.6  # 相邻外部请求最小间隔

    # 允许的跨域来源(逗号分隔,追加到内置 dev 白名单);同源部署无需配置
    cors_origins: str = ""

    tz: str = "Asia/Shanghai"


settings = Settings()
