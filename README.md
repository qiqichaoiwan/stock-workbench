# 智能选股工作台 · A股行业景气驱动选股系统

多租户选股与持仓管理系统。自上而下四层漏斗:**市场状态 → 行业景气 → 优质资产 → 事件催化**,输出总选股池 + 赛道池,并为每个用户提供持仓管理(三阶段止损)、AI 分析(用户自配大模型)、日报周报。

> ⚠️ 仅供个人量化研究参考,不构成投资建议。

## 功能总览

**智能选股工作台**(系统级)
- 候选域收窄:沪深300/中证500/创业板50/科创50·100 成分 + 热门赛道(固态电池/半导体/PCB/光模块/电机…动态管理),200–800 亿市值优先
- 市场状态:均线多空(相对口径)+ 市场宽度 + 成交额偏离 + 全A估值分位 + 融资比 → 四档趋势 + PM02 周期分档仓位(草案,滞回规则)
- 行业景气:涨价周期跟踪(商品价格动量+持续时长区分周期性/短期波动)+ 板块资金流 + **SC-U08 拥挤度偏离回避**(唯一带回测证据的策略,IS/OOS 一致)
- 技术信号:底分型 + 连红3-4天 + 量能偏离放大 = 入场组合;相对强弱(个股vs行业vs全A)
- 新闻甄别:低频采集 + 关键词规则过滤日内噪声(会议共识:高频文本采集不做)
- 策略生命周期卡:unverified→testing→verified/deprecated,证据链必填

**持仓工作台**(每用户独立,与选股侦测模块物理隔离)
- 录入持仓自动计算:真实保本价(含佣金/过户费/印花税)、初始止损(会议口径:最高点回撤10-15%必须卖出)、三阶段动态止损(G03:保本→移动,禁止浮亏放宽)
- 卖出登记入审计库,胜率/盈亏比/持有天数复盘
- AI 分析与卖出点位预测(用户自配 LLM,OpenAI 兼容协议,密钥加密;输出一律标注「参考建议,非决策」,硬规则不受影响)
- 每日持仓日报 + 每周报告

## 技术栈

- 后端:Python 3.12+ / FastAPI / SQLAlchemy / APScheduler / SQLite(WAL,预留 MySQL)
- 数据:AKShare(主)+ Baostock(备)+ EFinance(快照),统一网关重试/限速/冷却/自动降级
- 前端:Vue3 + Element Plus + Pinia + vue-router
- 部署:Docker Compose 单容器(前端构建产物由 FastAPI 托管)

## 快速开始

### 本机开发

```bash
# 后端
cd backend
pip install -r requirements.txt
python scripts/init_data.py --admin-user admin --admin-password 你的密码   # 建表+种子+管理员
python scripts/init_data.py --admin-password 你的密码 --backfill --years 5  # 可选:同步成分+回填历史
python -m uvicorn app.main:app --port 8000

# 前端(另开终端)
cd frontend
npm install
npm run dev        # 开发模式,代理未配置时直接访问 8000 端口
npm run build      # 构建产物由 FastAPI 托管
```

### 云服务器部署(Docker)

```bash
git clone <本仓库> && cd 智能选股工作台项目目录
JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_hex(32))") \
FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())") \
docker compose up -d --build
# 进入容器完成初始化
docker exec -it selectmoney python scripts/init_data.py --admin-user admin --admin-password 你的密码 --backfill
```

访问 `http://服务器IP:8000`。

## 首次初始化顺序(任务中心或 CLI)

1. `sync_members` 同步指数成分(候选域)
2. `sync_basic` 股票基础信息
3. `sync_tracks` 赛道成分
4. `backfill_history` 历史日线回填(约30-60分钟)
5. `sync_financials` 财务(近8报告期业绩报表)
6. 其余任务(估值/行业/商品/市场状态/信号/策略)可随时手动触发,此后由调度器自动运行

## 定时任务(交易日,Asia/Shanghai)

| 时间 | 任务 |
|---|---|
| 09:35-15:05 每30分钟 | 盘中快照(候选域) |
| 11:40 / 14:40 | 新闻采集甄别 |
| 15:35 / 15:45 | 收盘日线落库 / 股票基础信息 |
| 16:05-16:40 | 估值、行业拥挤度(SC-U08)、商品、市场状态、技术信号、财务 |
| 17:05 | 四层漏斗筛选 → 总池/赛道池 → 池表现 |
| 周五 17:45 | 逐用户周报 |
| 周一 08:30 | 指数成分与赛道维护 |

## 设计红线(来自回测教训,开发必读)

1. 指标一律用**相对偏离度**口径;绝对数值阈值不作主信号(绝对均线/绝对家数/长期分位均已证伪漂移)
2. 风控与 alpha 分离:止损框架只负责「不死」;策略验证必须带随机入场对照组
3. 回测同时报绝对收益与相对中位股超额(指数假涨个股失血行情)
4. AI 输出仅作参考建议;硬性止损由代码强制,不可被覆盖
5. PM02 仓位分档、G03 止损参数、H01 五维均为 unverified 草案,界面已标注,启用前需完成回测验证

## 数据与安全

- 用户密码 bcrypt;LLM API Key Fernet 加密落库(主密钥 `data/.fernet.key`,务必随数据备份)
- 多租户隔离:用户数据强制 user_id 过滤;Screener 无用户概念;单侧故障互不拖累
- SQLite 单文件在 `data/selectmoney.db`(WAL 模式),备份即拷贝该目录

## 项目结构

```
backend/app/
├── portfolio/          # 限界上下文一:账户/持仓(G03止损)/AI/报告
├── screener/           # 限界上下文二:候选域/市场状态/行业/信号/新闻/策略
├── shared/             # 数据网关/LLM网关/安全/只读引用
├── scheduler.py        # 定时任务
├── task_runner.py      # 任务运行器(定时与手动共用)
└── main.py
frontend/src/views/     # 登录/布局/仪表盘/选股池/行业/信号/新闻/策略/任务/持仓/交易/报告/设置
Dockerfile / docker-compose.yml
backend/tests/          # 核心逻辑单元测试(G03/SC-U08/底分型/PM02/新闻规则)
```

## 二期路线

完整回测模块(首批:PM02 U12-U14、G03 U26、H01 U15、SC-U08 小仓跟踪;协议含随机对照+IS/OOS 60/40+bootstrap)→ czsc 信号增强 → LLM 新闻语义与供需解读 → 通知推送 → 多模型对比。

## 用户与权限(三角色 + 邀请码注册)

| 角色 | 权限 |
|---|---|
| **admin 管理员** | 用户与邀请码管理 + 全部功能 |
| **analyst 策略员** | 选股工作台操作:触发任务 / 改策略参数 / 管赛道与策略卡 / 手动出池 |
| **user 普通用户** | 只读看板 + 自己的持仓工作台(录入/卖出/AI/报告/LLM 设置) |

- **注册需邀请码**:管理员在「系统管理 → 用户与权限」生成一次性邀请码(默认 7 天有效),凭码注册的账号为 user 角色;管理员也可直接创建任意角色账号。
- 角色与启用状态实时生效:停用账号后其 token 立即失效(401)。
- 保护规则:不能停用/删除自己;系统至少保留一个启用的管理员;删除用户会级联清理其持仓/交易/AI/报告/LLM 配置。
- 管理员初始化:`python backend/scripts/create_admin.py --admin-user admin --admin-password <密码≥8位>`(注册接口不产生管理员)。
- 修改自己的密码:右上角用户菜单 → 修改密码(需验旧密码)。

## 安全基线

- 密码 bcrypt;JWT 有效期 7 天;**生产必须设置 `JWT_SECRET`**(docker-compose 已改为必填注入,缺失拒绝启动)。
- 登录限流:同 IP 每分钟最多 8 次,超限 429。
- CORS 默认仅放行 vite dev(5173),生产同源部署无需跨域;自定义域名用 `CORS_ORIGINS` 环境变量追加。
- 用户 LLM api_key Fernet 加密存储,接口只回传掩码;周报 Markdown 渲染前经 DOMPurify 消毒。
