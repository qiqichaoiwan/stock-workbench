# agent.md · AI 编码助手工作约定

> 本文件面向在本仓库工作的 AI 编码助手(Claude/Cursor/ZCode 等)与新人工程师。
> 目标:让任何改动都遵守本项目已用真金白银换来的设计红线,不破坏测试与数据。

## 0. 一句话定位

FastAPI + Vue3 + SQLite 的多租户 A 股选股系统:四层漏斗(市场→行业→个股→事件)产出选股池,配三阶段止损持仓工作台。**规则引擎为准,AI 只作参考**。

## 1. 常用命令(Windows Git Bash / Linux 通用)

```bash
# 后端测试(33 项;任何改动后必须全绿)
cd backend && python -m pytest tests -q

# 编译检查(快速语法门)
cd backend && python -m compileall -q app

# 前端构建(改动 .vue/.js 后必须通过)
cd frontend && npm run build

# 本地起服务(必须先 export 强 JWT_SECRET,默认值拒绝启动)
export JWT_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
cd backend && python -m uvicorn app.main:app --port 8899
```

## 2. 绝对红线(违反即返工)

1. **相对口径**:策略/信号指标一律用相对偏离度(行业内分位、相对自身 MA 偏离),绝对阈值不作主信号。
2. **Point-in-time**:财务指标查询必须带 `ann_date <= 截面日` 条件;行情查询带 `trade_date <= completed_trade_day()`(见 `shared/data_gateway.completed_trade_day`,15:00 收盘分界)。禁止偷看未来。
3. **AI 不可覆盖硬规则**:G03 止损、SC-U08 回避、出池规则是代码层硬约束;LLM 输出一律标注参考。
4. **SQLite 单写**:禁止多 worker/多进程写同一 .db;任务并发受 `MAX_CONCURRENT_TASKS` 约束;不要绕过 `task_runner.run_task/run_task_async` 直接在请求线程跑长任务。
5. **测试不碰生产库**:`backend/tests/conftest.py` 已把 DATABASE_URL 钉到临时库——**新测试文件严禁在 import app 之前不设环境变量**;新增测试一律内存库/临时库 + monkeypatch mock 外网,禁止联网测试。
6. **生产数据只读**:默认不修改 `data/selectmoney.db`;需要迁移时先 `sqlite3 .backup` 再手工 ALTER,并在 db.py/main.py 留告警逻辑。
7. **降级链不可拆**:数据源降级(spot 三源/concept 四源/index 三源)与源级冷却是断网不断供的关键;改任何一源必须保持 `(df, source)` 返回契约与 `TRACK_SOURCE_MAP` 落库映射一致。
8. **prune 只信权威源**:`universe_manager.prune_track_members` 仅接受 source=="eastmoney" 且全 6 位数字码;降级源一律 `DegradedSourceError`(API 转 409)。这是防"清空整赛道成分"的 P0 守卫。

## 3. 架构速览(改哪里先看这)

```
backend/app/
├── shared/data_gateway.py   # 全部外部数据入口:重试/限速/冷却/超时/降级链。改数据源只动这里
├── screener/                 # 系统级选股上下文(无用户概念)
│   ├── strategy_engine.py    # 四层漏斗+总池/赛道池。PARAM_DEFAULTS 是全部策略参数
│   ├── industry_monitor.py   # 行业日度+SC-U08 拥挤度+新兴赛道雷达
│   ├── market_regime.py      # 市场状态+PM02(滞回)
│   ├── technical_signal.py   # 底分型/连红/量能/相对强弱
│   ├── universe_manager.py   # 候选域+赛道成分(四源降级+种子展开+TRACK_SEED_MAP)
│   └── news_monitor.py       # 新闻采集甄别
├── portfolio/                # 用户级持仓上下文
│   ├── holding_service.py    # G03 三阶段止损(唯一写止损入口:update_daily)
│   ├── account.py            # 注册(邀请码原子占用)/登录(限流/凭证指纹)
│   └── api.py                # holdings/watchlist/LLM/报告 REST
├── task_runner.py            # TASKS 注册表+_log_wrap(degraded 判定)+savepoint
├── scheduler.py              # 交易日下午收盘流水线(注释含完整时刻表)
└── main.py                   # 启动守卫(JWT/CORS/缺表告警)
frontend/src/views/           # 页面与后端模块一一对应;utils/fmt.js 是格式化唯一入口
```

## 4. 数据流与降级链(核心机制)

- **快照** `get_spot_snapshot_with_source`:东财akshare → efinance → 新浪;金额归一为元、市值归一为亿(启发式校验,异常抛错不静默)。
- **概念成分** `get_concept_board_members_with_source`:东财(600s 源冷却)→ 同花顺名单核验(1h 缓存,返回 `THS:` 占位行)→ 新浪匹配 → 关键词(快照 120s 缓存 + 库内行业 join)。ths 占位由 `universe_manager` 按 `TRACK_SEED_MAP` 种子展开;全部失败走 `_local_expand_track`(种子+行业+关键词)。
- **指数日线** `get_index_daily`:东财 → 腾讯 → 新浪,写 `IndexDaily.source`。
- **任务执行**:`run_task`(同步,带同名互斥+全局并发上限+跨进程文件锁+TaskLog)与 `run_task_async`(后台线程,API 手动触发走这个)。degraded(error/errors/codes_failed 非空)不冒充 success。

## 5. 写代码风格约定

- 注释密度与现有一致:解释"为什么/口径/红线",不复述代码。中文注释。
- pandas 过滤链注意运算符优先级(`|` 两侧必须括号);`str.contains` 一律 `regex=False` 除非明确要正则。
- 时间:库内一律 naive UTC(`_now()`);调度/展示用 Asia/Shanghai(`dg.now_tz()`);对比 created_at 时必须同口径(踩过 8h 错位坑)。
- 前端:请求统一走 `src/api.js`(silent:true 时调用方必须自己 catch 并提示);竞态用 seq-token 模式;表格 stripe border + `.table-scroll` 移动端横滚;涨跌色用 `upDownClass`(0/NaN 不着色)。
- 空状态必须带"下一步操作"指引(去任务中心跑哪个任务);新页面同步更新 `Help.vue`。

## 6. 测试约定

- 文件名 `test_<模块>.py`;纯函数直测,服务层用内存 sqlite + monkeypatch mock `data_gateway`;API 级用 TestClient。
- 断言错误码语义:参数错 422/400、降级不可用 409、权限 403、未认证 401、无数据 200 空态(**不许 500**)。
- 涉及策略口径的改动必须补参数化测试(总池口径 vs 赛道口径)。

## 7. 已知环境坑(本机)

- 东财 push2 域名本地 443 被墙:concept/快照会走降级链,属预期;测试严禁真实请求东财。
- Windows:Git Bash 路径 `/e/selectMoney`;Read 工具对 cwd 敏感;行尾 LF(git 会警告 CRLF,无碍)。
- pytest 收集按字母序:conftest.py 必须最先设 DATABASE_URL(历史事故:测试用户写进生产库)。
- uvicorn 后台跑时日志在 `data/uvicorn.log`;重启后 APScheduler 任务表见 scheduler.py 头注释。

## 8. 提交规范

- 一次提交一个主题;中文 commit message,首行 ≤50 字概述,正文列要点。
- 永不提交:`.env`、`data/`(生产库/密钥/日志)、`tmp/`(参考项目)、`frontend/dist`、`__pycache__`。
- 提交前门禁:`pytest -q` 全绿 + `npm run build` 通过 + `compileall` 通过。
