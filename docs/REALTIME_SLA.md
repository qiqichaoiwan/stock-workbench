# A 股实时行情 SLA 落地说明

更新：2026-11-07

## 结论

不存在同时满足“免费、A 股全市场、交易所级 SLA、可再分发”的公开方案。
交易所级行情需要上交所/深交所或其授权服务商的 Level-1/Level-2 授权、线路和运维合同。
免费方案可以提供券商/平台级实时研究能力，但必须标成 `best_effort`，不能用于自动下单、对外转售或宣称交易所级 SLA。

## 可选数据源

| 方案 | 成本与权限 | 实时能力 | 能否作为交易所级 SLA |
| --- | --- | --- | --- |
| 东方财富掘金量化免费版 | 需要本地终端/token，免费版约 50 只订阅 | 沪深 Tick 约 3 秒推送，支持断线恢复 | 否，适合自选/持仓研究 |
| 富途 OpenAPI + OpenD | 境内认证账户可获得部分 A 股 LV1 权限，需 OpenD | 报价、逐笔、摆盘订阅，权限和额度随账户 | 否；个人研究/单租户，不能向其他用户再分发 |
| Longbridge OpenAPI | 基础行情是否包含持续 A 股推送需按账户实测 | 有长连接推送和 sequence/timestamp | 否，需核验账户权限和服务条款 |
| AKShare / 新浪 / 腾讯 / efinance | 免费公开源 | 轮询快照或分钟数据 | 否，无 SLA，作为降级源 |
| Tushare Pro 免费层 | 历史/日线为主，实时接口通常单独授权 | 不适合作为免费实时主源 | 否 |
| 上交所/深交所授权 Level-1/2 | 申请、签约、购买 | 官方实时；Level-2 含逐笔/深度和重传能力 | 这是达到目标的正确路径 |

官方参考：

- [上交所行情服务与授权](https://www.sse.com.cn/transparency/services/index.shtml)
- [上证所 Level-1 行情说明](https://www.sseinfo.com/services/assortment/level1/)
- [深交所实时/延迟/EOD/Level-2 数据服务](https://www.szse.cn/English/services/dataServices/)
- [东方财富掘金量化实时行情](https://emt.18.cn/api/quant-help/data/stock.html)
- [东方财富掘金量化订阅接口](https://emt.18.cn/api/quant-help/python/python_subscribe.html)
- [富途权限说明](https://openapi.futunn.com/futu-api-doc/intro/authority.html)
- [富途实时报价推送](https://openapi.futunn.com/futu-api-doc/en/quote/update-stock-quote.html)
- [Longbridge 行情推送](https://open.longbridge.cn/zh-CN/docs/quote/push/quote)
- [AKShare 使用与商业风险说明](https://akshare.akfamily.xyz/introduction.html)

## 本项目已经实现的第一阶段

新增 `backend/app/marketdata/`：

1. 接收标准化行情事件：代码、价格、provider_time、received_at、sequence、session、provider；供应商提供但不保证连续的事件编号保留在 `provider_sequence` 审计字段。
2. 质量门禁：拒绝无时区时间、未来时间、非正价格、重复/乱序连续 `sequence` 和倒序 provider_time；opaque provider sequence 不参与缺口判断，但会在相同 provider_time 下做独立单调 tie-break，避免富途同毫秒逐笔被误丢。连续序列一旦出现缺口会持续标记 `delayed`，直到适配器显式 resync/连续性证明或执行 reset。
3. 输出 `realtime / best_effort / delayed / stale / unavailable` 五种状态。
4. 记录接受数、拒绝数、拒绝原因、最后事件时间、供应商统计。
5. `/api/marketdata/ingest` 使用 `X-Marketdata-Token` 写入，授权供应商由服务端环境变量决定。
6. `/api/marketdata/latest` 和 `/api/marketdata/health` 提供最新值及健康指标。
7. 可选富途 OpenD 适配器：基础镜像不安装 `futu-api`，安装 `backend/requirements-futu.txt` 并显式开启后才连接。
8. 选股池和持仓行情优先读取已通过门禁的推送值；推送不可用时继续使用现有公共源和已落库收盘值。

默认 Hub 使用进程内内存，只适合单进程开发/试运行，健康接口会明确返回
`single_process=true`、`sla_ready=false`。启用下方 Redis 共享存储后，latest、序列状态和
Stream 会跨进程共享，但它仍不提供交易所数据授权或交易所级 SLA。

### 可选 Redis 共享存储

项目现在提供 `backend/app/marketdata/redis_store.py`。把
`MARKETDATA_STORAGE_MODE=redis` 并配置 `MARKETDATA_REDIS_URL` 后，行情事件会在
Redis 中同时写入：

- `latest` Hash：每个 `provider|code` 的最新规范化事件，供多进程读取；
- `stream` Stream：原始规范化事件追加记录，默认近似保留 100,000 条；
- `sequence/provider_time/provider_sequence/gap_pending` Hash：由 Lua 脚本原子检查重复、乱序、opaque 同时刻顺序和序列缺口，并持久化缺口降级状态；
- `metrics` Hash：跨进程接受/拒绝数、拒绝原因、序列缺口数、provider_time 与最后接收时间。

Redis 客户端是可选依赖，安装：

```bash
pip install -r backend/requirements-marketdata-redis.txt
export MARKETDATA_STORAGE_MODE=redis
export MARKETDATA_REDIS_URL='redis://127.0.0.1:6379/2'
export MARKETDATA_REDIS_PREFIX='stock-workbench:marketdata'
```

仓库的 Compose 文件还提供了一个可选 Redis 7 服务和独立 `marketdata-worker`。在 `.env` 写入
`MARKETDATA_STORAGE_MODE=redis`、`MARKETDATA_REDIS_URL=redis://marketdata-redis:6379/2` 后，
执行 `docker compose --profile marketdata up -d --build` 即可启动 Redis；设置 Futu 参数后，
同一 profile 会启动单租户 Futu worker。API 容器默认 `FUTU_ENABLED=false`，避免和 worker
重复连接 OpenD。Redis 只解决跨进程状态共享，不会替代行情授权线路。

最低成本生产试运行配置：

```bash
MARKETDATA_STREAM_ENABLED=true
MARKETDATA_PROCESS_ROLE=api       # API 只读；worker 容器必须设为 worker
MARKETDATA_SINGLE_TENANT=true
MARKETDATA_STORAGE_MODE=redis
MARKETDATA_REDIS_URL=redis://marketdata-redis:6379/2
MARKETDATA_SYMBOLS=600000,000001,600519
FUTU_ENABLED=false                 # API 容器保持只读
FUTU_OPEND_HOST=host.docker.internal # OpenD 在宿主机时
FUTU_OPEND_PORT=11111
MARKETDATA_AUTHORIZED_PROVIDERS=futu # 确认账户权限后再填
docker compose --profile marketdata up -d --build
docker compose logs -f marketdata-worker
```

worker 启动前会检查 Redis、单租户、Futu 开关和标的列表；缺少 `futu-api` 或 Redis
时会明确失败并由 Compose 重启。OpenD 不可达时 worker 会保持进程存活并按指数退避重连，
同时把 provider 状态写成 `error`/`stale`；API 的 SLO 门禁会保持未就绪，不会把本地内存
快照伪装成共享实时数据。

worker 容器的 healthcheck 检查 Redis 和 worker heartbeat；它只负责暴露 liveness，Docker
Compose 不会因为 `unhealthy` 自动重启容器。OpenD 的连接自愈由 worker 内部退避重连完成；
若需要按 healthcheck 重启，请交给 Kubernetes、Swarm 或外部监控编排。

OpenD 心跳超时只在工作日 09:15–11:35、12:55–15:05（北京时间）执行；闭市、午间和周末
不会因为没有 tick 就反复重连，但最新行情仍会按交易时段和新鲜度门禁降级。

`/api/marketdata/health` 会返回 `shared_storage.connected`、`stream_length` 和
`single_process=false`。Redis 不可用时写入会返回 `storage_unavailable`，读取只显示
`unavailable`，不会悄悄退回 worker 本地快照。Redis 只是共享状态组件，不能替代交易所
授权线路，也不会把免费源升级为交易所级 SLA。项目其他业务仍使用 SQLite 单写约束；若要
扩展整个 API 或运行多个 scheduler，还必须先切换 `DATABASE_URL` 到 PostgreSQL/MySQL，
并把任务互斥锁迁移到数据库锁。

`MARKETDATA_STORAGE_MODE` 仅接受 `memory` 或 `redis`。拼写错误或空值会阻止启动，
避免部署者误以为状态已共享；Redis 客户端缺失、地址未配置或服务不可达时，健康接口
会显示 Redis 不可用，应用不会改用进程内缓存。

## 试运行配置

```bash
# 授权行情桥接进程向本服务发送事件
export MARKETDATA_STREAM_ENABLED=true
export MARKETDATA_INGEST_TOKEN='生成一个随机长 token'
export MARKETDATA_AUTHORIZED_PROVIDERS='gquant'  # 只有完成授权核验后才填写
export MARKETDATA_SYMBOLS='600000,000001,600519'
export MARKETDATA_STORAGE_MODE='redis'  # 拆分行情 worker/API 时使用；单进程试跑可保持 memory
export MARKETDATA_REDIS_URL='redis://127.0.0.1:6379/2'
```

免费研究模式不要把 `gquant` 或 `futu` 写进 `MARKETDATA_AUTHORIZED_PROVIDERS`，这样即使事件新鲜，系统也只显示 `best_effort`，不会被预警逻辑当作授权实时行情。

### 免费 GQuant Tick 桥接

项目提供了不依赖 `gm` SDK 才能导入的桥接模块：`backend/app/marketdata/gquant.py`。
在掘金终端/策略环境中安装并运行 SDK 后，桥接器会订阅 `tick`，将
`SHSE.600000`/`SZSE.000001` 等事件标准化，通过受保护的 `/api/marketdata/ingest`
批量发送。HTTP 408/429/5xx、连接超时等传输故障会按有限预算退避重试；单条
`storage_unavailable` 也只按该预算重试，永久拒绝不会重复发送。进程内队列没有磁盘
持久化，进程重启或预算耗尽可能造成数据缺口，不能把这条免费链路当成可靠交付。

```bash
# 后端服务侧
export MARKETDATA_STREAM_ENABLED=true
export MARKETDATA_INGEST_TOKEN='生成一个随机长 token'
# 免费 GQuant 不属于授权 SLA 源，保持为空
export MARKETDATA_AUTHORIZED_PROVIDERS=''

# 掘金策略侧；GQUANT_TOKEN 是掘金 SDK 凭证，与上面的接入 token 不同
export GQUANT_INGEST_URL='http://127.0.0.1:8000/api/marketdata/ingest'
export GQUANT_INGEST_TOKEN="$MARKETDATA_INGEST_TOKEN"
export GQUANT_STRATEGY_ID='掘金策略 ID'
export GQUANT_TOKEN='掘金终端 token'
export GQUANT_SYMBOLS='SHSE.600000,SZSE.000001,SHSE.600519'
export GQUANT_BATCH_SIZE=50
export GQUANT_RETRY_TIMES=3
export GQUANT_FLUSH_INTERVAL_SECONDS=0.5
export GQUANT_MAX_PENDING=1000

# 在仓库根目录执行；也可以复制该模块到掘金策略目录
PYTHONPATH=backend python -m app.marketdata.gquant
# 或
python backend/scripts/gquant_ingest_bridge.py
```

桥接器会生成 `provider=gquant`、UTC `provider_time/received_at`。如果原始 tick 没有
供应商序列，则发送 `sequence=null`，由 Hub/Redis 按供应商时间排序，并在同一时间戳按
`provider_sequence` 单调校验；服务重启后
仍能继续接收更晚的事件，但相同时间戳的无序更新会得到
`provider_time_out_of_order`。只有响应中逐条明确 `accepted=true` 的事件才计入
`sent`；逐条拒绝会记录 `reject_reasons`，网络或响应格式无法确认的事件计入
`unconfirmed`。这些计数可从桥接器 `health()` 和日志观察。无法解析的 tick 仍记为转换错误，
桥接器不会把 token 或传输异常写入日志。运行后可在 `GET /api/marketdata/latest` 查看
`quality=best_effort` 和 `age_seconds`。免费版的
推送间隔、订阅数量及账户权限以掘金官方账户为准；该链路只适合研究/人工盯盘，不能
用于自动下单、对外再分发或宣称交易所级 SLA。

默认未满一批的 tick 最迟约 `0.5s` 自动发送，`GQUANT_FLUSH_INTERVAL_SECONDS` 可调但
建议不超过 `1s`；`GQUANT_MAX_PENDING` 用于限制断线时的内存队列，队列满会明确报错而不
静默丢弃。远程接入地址必须使用 HTTPS；只有 `localhost`、`127.0.0.1`、`::1` 的本机
HTTP 被默认允许，确需内网明文时才显式设置 `GQUANT_ALLOW_INSECURE_HTTP=true`。

富途 OpenD（仅单租户研究）可选配置：

```bash
pip install -r backend/requirements-futu.txt
export MARKETDATA_STREAM_ENABLED=true
export MARKETDATA_PROCESS_ROLE=worker
export MARKETDATA_SINGLE_TENANT=true  # 富途个人行情必须显式单租户
export FUTU_ENABLED=true
export FUTU_OPEND_HOST=127.0.0.1
export FUTU_OPEND_PORT=11111
export MARKETDATA_SYMBOLS='600000,000001,600519'
# 只有确认账户权限、服务条款和部署范围后才填写
export MARKETDATA_AUTHORIZED_PROVIDERS='futu'
```

API 进程必须保持 `MARKETDATA_PROCESS_ROLE=api`，只读取 Redis；生产建议使用上面的
Compose profile，让 `marketdata-worker` 独立运行并设置 `MARKETDATA_PROCESS_ROLE=worker`。
FastAPI 若误设为 worker 会在 lifespan 启动时拒绝运行，避免重复打开 OpenD。worker 的命令是：

```bash
python scripts/marketdata_worker.py --dry-run  # 只校验配置，不连接 OpenD/Redis
docker compose --profile marketdata up -d --build
```

适配器会在无回调超过心跳窗口时把状态标为 `stale` 并触发重连；`/health` 会返回
`message_age_seconds` 和 `reconnects`。富途 TICKER 文档中的 `sequence` 是大整数事件标识，
不承诺从上一条加一；适配器因此将它原样写入 `provider_sequence`，把 Hub 的连续
`sequence` 置空，避免把正常逐笔误判成序列缺口。缺少可验证的连续序列时，系统仍不能
证明无丢包，也不会把该连接升级为交易所级 SLA。这只是连接自愈，不代表账户权限或交易所 SLA 已认证。

## 达到交易所级 SLA 还必须完成

1. 使用交易所/授权供应商的 Level-1/Level-2 线路，确认允许的展示、内部使用和再分发范围。
2. 已提供 Redis Streams + latest 快照实现；生产仍应把 Redis 部署为高可用实例，并把原始事件异步写入 PostgreSQL/Timescale 或 ClickHouse。
3. 用独立行情 worker 维护长连接、心跳、断线指数退避、重订阅和 sequence gap 回补；API 只读最新快照。
4. 记录 provider_time、received_at、延迟 P50/P95/P99、心跳间隔、乱序率、重复率、缺失率和重连次数。
5. 预警只接受授权、交易时段、`age_seconds <= 5` 且没有 sequence gap 的事件；其他值只能展示或复盘。
6. 在至少一个完整交易日完成 09:25、09:30、11:30、13:00、14:30、15:00、15:30 的连续认证，并保留原始事件和报告。
7. 交易服务与行情服务分离，订单执行单独鉴权、幂等、审计、熔断；行情质量下降时禁止自动下单。

当前前端持仓/自选/选股页仍按 20–30 秒轮询用户行情接口；后端 Tick Hub 已按事件质量保存
最新值，但若目标是交易席位级的亚秒展示，还需要在授权源验收后增加鉴权 WebSocket/SSE，
并让前端订阅最新快照，不能把轮询间隔误认为行情源 SLA。

这套实现把免费源的边界和授权源的接入位置固定下来，下一步只需替换 provider adapter 和共享存储，不需要再改持仓、选股池和预警的数据口径。
