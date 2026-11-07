"""全局配置:数据库、JWT、加密、LLM 兜底、采集参数。

约定:所有阈值类参数只提供默认值,运行期以 screener 策略参数(JSON)为准,
本文件只管基础设施,不承载策略逻辑。

采集超时/退避约定(与 shared/data_gateway.resilient 对应):
- fetch_timeout_seconds:单次源调用的等待上限(秒),默认 60s;
  超时不能终止底层线程或撤销副作用,计为一次失败并进入退避。设为 0 可关闭(仅测试)。
  每进程最多 8 个 daemon 工作线程,超时调用结束才释放容量,满时立即失败;
  嵌套调用在当前工作线程执行,仅受外层等待期限约束,不单独计时。
- fetch_retry_times × fetch_retry_wait_seconds×attempt:指数退避,
  如 3 次 × 5s×attempt(5s/10s/15s)。
- fetch_rate_limit_seconds:相邻外部请求最小间隔,默认 0.6s,防限流。
- 源级冷却见 data_gateway._SOURCE_COOLDOWN_SECONDS(东财日线 10 分钟,其余 5 分钟)。

SQLite 多 worker 约束(必读):
- 默认 SQLite(WAL + busy_timeout=5s,见 app/db.py)只允许**单写进程**。
  docker-compose 已 pin deploy.replicas=1 且单 scheduler;不要用
  `uvicorn --workers N` / gunicorn 多 worker 直连同一 .db 文件,
  否则 WAL 双写冲突 → database is locked / 数据损坏。
- 横向扩展前必须:① 切 MySQL/Postgres(设 DATABASE_URL);② 把
  task_runner._file_lock 换成 DB advisory 锁;③ 再放开 replicas。

备份指引(生产):
- 数据文件:data/selectmoney.db(+ -wal/-shm)。每日停机或 sqlite3 .backup
  热备:`sqlite3 data/selectmoney.db ".backup 'backup-YYYYMMDD.db'"`,
  保留 7 天;恢复前先停容器再整体拷回。create_all 只建缺失表,
  不做迁移——缺表告警见 app/db.py:check_missing_tables。
"""
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
ROOT_DIR = BASE_DIR.parent                          # 项目根

DEFAULT_INSECURE_JWT = "change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "智能选股工作台"
    # SQLite:两个上下文共用一个库文件,模型表用前缀区分;预留 DATABASE_URL 切 MySQL
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'selectmoney.db'}"

    jwt_secret: str = DEFAULT_INSECURE_JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    # Fernet 主密钥(生产必须由 .env 提供;为空则首次启动自动生成写入 data/.fernet.key)
    fernet_key: str = ""

    # 系统级兜底 LLM(OpenAI 兼容协议);用户自配优先。
    # 默认空 = 无兜底:llm_gateway.resolve_config 返回 source="none"/enabled=False,
    # 上层降级为纯规则输出,绝不静默伪造 AI 内容。生产如需启用,三项必须全配。
    # 仅部署管理员可配置;环境变量 LLM_LOCAL_BASE_URLS='["http://127.0.0.1:11434/v1"]'。
    # 精确匹配规范化后的完整端点,仅对环回 IP 放宽,不允许私网或特殊地址。
    llm_local_base_urls: list[str] = []
    default_llm_base_url: str = ""
    default_llm_api_key: str = ""
    default_llm_model: str = "deepseek-chat"

    # 数据采集(超时/重试/限速,见模块 docstring)
    intraday_interval_minutes: int = 30   # 盘中半小时快照
    # 双核 v2 服务端实时适配器开关:关闭后 GET /dual-core 只读已落库日线
    # (unverified),不发外部请求;生产故障排查/断网时可作为 kill switch。
    dual_core_feed_enabled: bool = True
    fetch_timeout_seconds: float = 60.0   # 单次等待上限,不终止底层调用;0=关闭(仅测试)
    fetch_retry_times: int = 3
    fetch_retry_wait_seconds: int = 5
    fetch_rate_limit_seconds: float = 0.6  # 相邻外部请求最小间隔

    # 可选的推送行情接入层。默认关闭；关闭时现有 AKShare/新浪/腾讯链路继续
    # 作为研究/复盘降级源。开启后仍需把数据源、授权范围和共享存储单独验收，
    # 不能因为收到一条推送就把它标成交易所级 SLA。
    marketdata_stream_enabled: bool = False
    # Process ownership is explicit so an API replica never opens a second
    # broker/OpenD connection.  The standalone marketdata worker must run with
    # ``MARKETDATA_PROCESS_ROLE=worker``; the FastAPI process must stay ``api``.
    marketdata_process_role: str = "api"  # api | worker
    # 富途等个人账户行情不可默认广播给多租户；必须由部署者明确声明单租户。
    marketdata_single_tenant: bool = False
    marketdata_authorized_providers: str = ""
    marketdata_ingest_token: str = ""
    marketdata_max_age_seconds: float = 3.0
    marketdata_delayed_max_age_seconds: float = 15.0
    marketdata_future_tolerance_seconds: float = 2.0
    marketdata_symbols: str = ""
    # latest/sequence 默认进程内；生产多 worker/多副本必须切到 Redis。
    # Redis 客户端是可选依赖，未安装或服务不可达时健康接口会明确报错，
    # 不会把本地内存快照伪装成共享 SLA 存储。
    marketdata_storage_mode: str = "memory"  # memory | redis
    marketdata_redis_url: str = ""
    marketdata_redis_prefix: str = "stock-workbench:marketdata"
    marketdata_redis_stream_maxlen: int = 100_000

    # 可选富途 OpenD 适配器。futu-api 不放进基础镜像，未安装/未配置时不影响
    # 现有系统；启用前必须确认账号授权和单租户数据使用范围。
    futu_enabled: bool = False
    futu_opend_host: str = "127.0.0.1"
    futu_opend_port: int = 11111
    futu_max_symbols: int = 100
    futu_reconnect_max_backoff: float = 30.0

    # 允许的跨域来源(逗号分隔,追加到内置 dev 白名单);同源部署无需配置。
    # 生产前后端分离部署时必须显式配置 CORS_ORIGINS=https://app.example.com,
    # 为空则默认同源(仅放行本地 vite dev 端口),见 main.py。
    cors_origins: str = ""

    # 后台任务全局并发上限(超限 API 转 409 拒绝,防回填拖垮采集)
    max_concurrent_tasks: int = 3

    tz: str = "Asia/Shanghai"

    @field_validator("marketdata_storage_mode", mode="before")
    @classmethod
    def _validate_marketdata_storage_mode(cls, value: object) -> str:
        """Reject an unknown storage mode instead of silently using memory.

        A typo such as ``MARKETDATA_STORAGE_MODE=redsi`` would otherwise make
        a multi-worker deployment fall back to process-local state while its
        operators believe Redis is active.  Failing during configuration load
        keeps that unsafe state from reaching startup.
        """
        mode = str(value).strip().lower()
        if mode not in {"memory", "redis"}:
            raise ValueError("MARKETDATA_STORAGE_MODE 必须为 memory 或 redis")
        return mode

    @field_validator("marketdata_process_role", mode="before")
    @classmethod
    def _validate_marketdata_process_role(cls, value: object) -> str:
        """Keep broker connection ownership unambiguous across containers."""
        role = str(value).strip().lower()
        if role not in {"api", "worker"}:
            raise ValueError("MARKETDATA_PROCESS_ROLE 必须为 api 或 worker")
        return role

    @property
    def is_using_default_jwt(self) -> bool:
        return self.jwt_secret == DEFAULT_INSECURE_JWT or not self.jwt_secret.strip()

    @property
    def llm_fallback_enabled(self) -> bool:
        """系统兜底 LLM 是否可用;默认 False(空 key),guard 住误启用。"""
        return bool(self.default_llm_api_key.strip())


settings = Settings()
