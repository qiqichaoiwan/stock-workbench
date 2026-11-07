# 智能选股工作台 · A股行业景气驱动选股系统

多租户选股与持仓管理系统。自上而下四层漏斗:**市场状态 → 行业景气 → 优质资产 → 事件催化**,输出总选股池 + 赛道池,并为每个用户提供持仓管理(三阶段止损)、AI 分析(用户自配大模型)、日报周报。

> ⚠️ 仅供个人量化研究参考,不构成投资建议。

## 功能总览

**智能选股工作台**(系统级)
- 候选域收窄:沪深300/中证500/创业板50/科创50·100 成分 + 热门赛道(固态电池/半导体/PCB/光模块/存储芯片/电机/AI算力,动态管理),200–800 亿市值优先
- **大盘指数**:上证/深成/创业板/科创50/沪深300/中证500 六大综指点位+涨跌+成交额(仪表盘指数条 + 大盘指数页),数据带来源标注
- **板块资金流**:行业/概念主力净流入盘中快照 + 单板块近 30 日趋势(大盘指数页)
- **大盘统计**:涨跌家数/涨停跌停/两市成交额盘中快照(市场总览卡)
- **龙虎榜 + 游资席位**:每日龙虎榜(净额/买卖席位),内置 62 席知名游资映射,上榜股自动标注命中游资
- 市场状态:均线多空(相对口径)+ 市场宽度 + 成交额偏离 + 全A估值分位 + 融资比 → 四档趋势 + PM02 周期分档仓位(草案,滞回规则)
- 行业景气:新兴赛道雷达(启动期优先)+ 景气热力图 + 涨价周期跟踪 + **SC-U08 拥挤度偏离回避**
- 技术信号:底分型 + 连红 + 量能偏离 = 入场组合;相对强弱(个股vs行业vs全A)
- 新闻甄别:低频采集 + 关键词规则过滤日内噪声
- 策略生命周期卡:unverified→testing→verified/deprecated,证据链必填

**赛道成分四源降级**(断网不断供):东财概念成分 → 同花顺名单核验 → 新浪匹配 → 关键词 → 本地种子/行业/关键词展开;来源在赛道管理页可见,东财恢复后自动切回权威源并差集清理降级残留。

**持仓工作台**(每用户独立)
- 录入持仓自动计算:真实保本价(含佣金/过户费/印花税)、初始止损、三阶段动态止损(G03:A初始→B保本→C跟踪,禁止浮亏放宽)
- **自选三价预警**:成本/止盈/止损/涨跌幅阈值,盘中每 30 分钟扫描,24h 去重(只提醒不碰止损)
- 卖出登记入审计库,胜率/盈亏比/持有天数复盘
- AI 分析(用户自配 LLM,OpenAI 兼容,密钥加密;输出标注「参考建议」,硬规则不可被覆盖)
- 每日持仓日报 + 每周报告

**系统**
- **拼音搜股**:顶栏搜索框支持代码/名称/全拼/首字母(如 `gzmt`→贵州茅台),选中跳选股池定位
- **使用帮助**:站内 `/help` 页,16 个功能分组说明(看什么/怎么用/何时更新/空数据怎么办/权限)
- 三角色 + 邀请码注册;用户与权限管理页

## 技术栈

- 后端:Python 3.12+ / FastAPI / SQLAlchemy / APScheduler / SQLite(WAL,预留 MySQL)
- 数据:AKShare(主)+ Baostock(备)+ EFinance(快照),统一网关重试/限速/冷却/强制超时/多源自动降级
- 前端:Vue3 + Element Plus + Pinia + vue-router + pypinyin(后端搜索索引)
- 部署:Docker Compose 单容器(前端构建产物由 FastAPI 托管)

实时行情接入边界、免费方案对比、质量门禁和授权源升级路径见
[docs/REALTIME_SLA.md](docs/REALTIME_SLA.md)。免费公开源只作为 best-effort/降级源，不能宣称交易所级 SLA。

### 实时行情试运行

项目内置免费 GQuant Tick 桥接器、Futu OpenD 适配器和可选 Redis 共享快照。默认仍是单进程内存模式，
适合本地试跑；生产最低成本方案是用 Futu OpenD 单租户 worker 写入 Redis，API 只读共享快照。免费
GQuant 推送会显示为 `best_effort`，只有完成授权核验并加入 `MARKETDATA_AUTHORIZED_PROVIDERS` 的源才会显示为
`realtime`。完整配置与启动命令见 [docs/REALTIME_SLA.md](docs/REALTIME_SLA.md)。

```bash
# .env 中配置强 JWT、Redis 模式、标的和 Futu OpenD 地址后启动
MARKETDATA_STORAGE_MODE=redis
MARKETDATA_REDIS_URL=redis://marketdata-redis:6379/2
MARKETDATA_STREAM_ENABLED=true
MARKETDATA_PROCESS_ROLE=api       # API 只读；独立 worker 使用 worker
MARKETDATA_SINGLE_TENANT=true
MARKETDATA_SYMBOLS=600000,000001,600519
FUTU_OPEND_HOST=host.docker.internal
FUTU_OPEND_PORT=11111
FUTU_ENABLED=false                 # API 不连接 OpenD，避免重复订阅
MARKETDATA_AUTHORIZED_PROVIDERS=futu # 确认账户权限后再填写

docker compose --profile marketdata up -d --build
docker compose logs -f marketdata-worker
```

## 快速开始

### 本机开发

```bash
# 后端(必须先设强 JWT_SECRET,默认值拒绝启动)
cd backend
pip install -r requirements.txt          # 含 pypinyin(拼音搜索)
export JWT_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
python scripts/init_data.py --admin-user admin --admin-password 你的密码   # 建表+种子+管理员
python scripts/init_data.py --admin-password 你的密码 --backfill --years 5  # 可选:成分同步+历史回填
python -m uvicorn app.main:app --port 8899

# 前端(另开终端)
cd frontend
npm install
npm run dev        # 开发模式(5173,已代理 /api)
npm run build      # 构建产物由 FastAPI 托管(8899 直接可用)
```

首次登录后，先在任务中心运行「同步股票基础信息」，再在持仓或自选页输入股票代码。页面每 30 秒刷新行情，并显示每只股票的来源和行情时间；缺少可核验时间时显示为参考快照，源不可用时退回有日期的已落库收盘价或显示暂无行情。盘中自选提醒只使用时间可核验且新鲜的报价；数据源不可用时任务会报告降级，不保证触发提醒。免费公开数据源可能限流、延迟或不可用，行情并非交易所逐笔数据。

如已取得授权推送源，可显式开启实时接入层：设置 `MARKETDATA_STREAM_ENABLED=true`、`MARKETDATA_INGEST_TOKEN`、`MARKETDATA_SYMBOLS`，并把完成授权核验的供应商加入 `MARKETDATA_AUTHORIZED_PROVIDERS`。系统会记录 provider_time、received_at、sequence、延迟和质量状态；没有授权、处于非交易时段或存在连续序列缺口时不会触发实时预警。序列缺口会持续标记为 delayed，直到供应商显式发送 `sequence_contiguous=true`/`sequence_resync=true` 证明已重同步，或运维重置 Hub；供应商不保证连续的 opaque 事件编号保留为 provider_sequence，不参与 gap 判定，但同一时间戳会按其单调顺序去重和排序。

富途个人行情还必须设置 `MARKETDATA_SINGLE_TENANT=true`；多用户部署默认拒绝富途事件，避免把个人账户行情共享给其他租户。

已有部署升级前请备份数据库和原密钥。旧版本删除用户可能留下无外键数据；升级仅修复此后的删除行为，历史数据需要先审计，不能仅凭用户 ID 安全判定其归属。

### 云服务器部署(Docker)

```bash
git clone <本仓库> && cd 项目目录
# 首次部署生成并安全保存 .env;后续部署复用,并将其纳入安全备份。
umask 077
if [ ! -e .env ]; then
  printf 'JWT_SECRET=%s\nFERNET_KEY=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -base64 32 | tr '+/' '-_')" > .env
fi
docker compose up -d --build
# 进入容器完成初始化
docker exec -it stock-workbench python scripts/init_data.py --admin-user admin --admin-password 你的密码 --backfill
```

访问 `http://服务器IP:8000`。龙虎榜游资映射随镜像内置(`backend/data/hot_money_seats.json`)。
镜像会自动构建前端。`.env` 包含敏感密钥,不要提交或公开;恢复数据库时同时恢复原密钥,更换 Fernet 密钥会使已保存的 LLM 密钥无法解密。

## 首次初始化顺序(任务中心或 CLI)

1. `sync_members` 同步指数成分(候选域)
2. `sync_basic` 股票基础信息
3. `sync_tracks` / `tracks_backfill` 赛道成分(东财被墙时自动走降级链,见赛道管理"来源"列)
4. `backfill_history` 历史日线回填(逐股联网，耗时取决于数量和数据源；增量追加前核验前复权基准)
5. `sync_financials` 财务(近8报告期,point-in-time 公告日)
6. `sync_indexes` / `lhb_sync` / `fund_flow` / `market_stat` 新模块数据
7. 其余任务(估值/行业/商品/市场状态/信号/策略)可随时手动触发,此后由调度器自动运行

## 定时任务(交易日,Asia/Shanghai)

| 时间 | 任务 |
|---|---|
| 09:00-14:30 每30分钟 | 盘中快照(候选域;9:00 在集合竞价前仅留痕) |
| 09:35/10:05-13:35/14:05/14:35(各错峰1分钟) | fund_flow 板块资金流 + market_stat 大盘统计 + watch_alert 自选预警 |
| 11:40 / 14:40 | 新闻采集甄别 |
| 15:35 / 15:45 | 收盘日线落库(带来源)/ 股票基础信息 |
| 15:50 | 持仓巡检(G03 止损推进,唯一写入口) |
| 16:05-16:40 | 估值、行业拥挤度(SC-U08)、商品、市场状态、技术信号、财务 |
| 16:45 | 大盘指数落库(sync_indexes) |
| 17:05 / 17:10 | 策略漏斗+池表现 / 龙虎榜(lhb_sync) |
| 周五 17:45 | 逐用户周报 |
| 周一 08:30/08:40 | 指数成分 / 赛道维护 |

非交易日自动跳过;同名任务互斥,全局并发上限 `MAX_CONCURRENT_TASKS`(默认 3)。

收盘日线任务有 120 秒等待预算，未处理或无法核验的股票会使任务显示降级，可重跑补齐；进程内轮转进度在重启后重置。前复权基准变化或旧行情不完整时，系统拒绝拼接新旧日线，持仓巡检也不会用过期收盘价推进止损。此时请先备份，在隔离库从可信来源重建并核验该股票完整历史及相关衍生指标，确认无误后停机切换；系统不会自动覆盖旧数据。

## 设计红线(来自回测教训,开发必读)

1. 指标一律用**相对偏离度**口径;绝对数值阈值不作主信号
2. 风控与 alpha 分离:止损框架只负责「不死」;策略验证必须带随机入场对照组
3. 回测同时报绝对收益与相对中位股超额
4. AI 输出仅作参考建议;硬性止损由代码强制,不可被覆盖
5. PM02 仓位分档、G03 止损参数、H01 五维、SC-U08 均为 unverified 草案,界面已标注
6. 财务数据 point-in-time:公告日(ann_date)晚于截面日的一律不可见

## 数据与安全

- 用户密码 bcrypt;JWT 携带凭证指纹(改密后旧 token 立即失效);LLM API Key Fernet 加密落库
- LLM Base URL 服务端 SSRF 校验(字面 IP + 域名解析结果双重校验,禁内网/环回/链路本地)
- 多租户隔离:用户数据强制 user_id 过滤;Screener 无用户概念
- SQLite 单文件 `data/selectmoney.db`(WAL,单写进程),备份即拷贝该目录;每日 `sqlite3 .backup` 保留 7 天
- 生产必设 `JWT_SECRET`(≥32位,缺失/默认值拒绝启动);CORS 默认同源。需要跨域时在 `CORS_ORIGINS` 中逐项填写可信来源；生产环境禁止 `*`。

## 项目结构

```
backend/app/
├── portfolio/          # 限界上下文一:账户/持仓(G03止损)/自选预警/AI/报告
├── screener/           # 限界上下文二:候选域/市场状态/行业/信号/新闻/策略/龙虎榜
├── shared/             # 数据网关(多源降级)/LLM网关/安全/拼音搜索/只读引用
├── scheduler.py        # 定时任务
├── task_runner.py      # 任务运行器(互斥/并发上限/degraded/savepoint)
└── main.py
backend/data/hot_money_seats.json   # 游资席位映射(随镜像)
frontend/src/views/     # 登录/仪表盘/大盘指数/选股池/龙虎榜/行业/信号/新闻/策略配置/策略卡/任务/持仓/自选预警/交易/报告/设置/用户/帮助
Dockerfile / docker-compose.yml
backend/tests/          # 后端回归测试(G03/SC-U08/底分型/PM02/权限/行情质量/持仓隔离/审计回归)
```

## 用户与权限(三角色 + 邀请码注册)

| 角色 | 权限 |
|---|---|
| **admin 管理员** | 用户与邀请码管理 + 全部功能 |
| **analyst 策略员** | 选股工作台操作:触发任务 / 改策略参数 / 管赛道与策略卡 / 手动出池 |
| **user 普通用户** | 只读看板 + 自己的持仓工作台(录入/卖出/自选预警/AI/报告/LLM 设置) |

- **注册需邀请码**:管理员生成一次性邀请码(默认 7 天有效);也可直接创建任意角色账号
- 停用账号后其 token 立即失效(401);不能停用/删除自己;至少保留一个启用管理员;删用户级联清理其数据
- 管理员初始化:`python backend/scripts/create_admin.py --admin-user admin --admin-password <密码≥8位>`

## 二期路线

完整回测模块(随机对照+IS/OOS+bootstrap)→ 通知推送(飞书/钉钉)→ LLM 新闻语义 → 多模型对比 → SSE 任务进度。

详细设计见:[架构设计.md](架构设计.md) · [功能模块设计.md](功能模块设计.md) · 开发约定见 [agent.md](agent.md)。
