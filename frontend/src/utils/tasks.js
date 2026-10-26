// 任务中文名统一映射:任务中心日志/手动触发下拉与各页面空态提示共用,
// 避免同一任务在不同页面出现英文 key 或不同叫法。
export const TASK_NAMES = {
  sync_members: '同步指数成分(候选域)',
  sync_basic: '同步股票基础信息',
  sync_tracks: '同步赛道成分',
  tracks_backfill: '一键回填赛道成分',
  backfill_history: '历史日线回填(慢,数十年)',
  sync_financials: '财务增量(业绩报表)',
  sync_quotes: '收盘快照落库(日线)',
  intraday_snapshot: '盘中快照',
  sync_indexes: '指数收盘同步',
  sync_valuation: '估值快照',
  fund_flow: '板块资金快照',
  market_stat: '市场总览快照',
  watch_alert: '自选预警扫描',
  industry_daily: '行业日度监测+拥挤度',
  commodities: '商品涨价跟踪',
  market_state: '市场状态计算',
  signals: '技术信号扫描',
  strategy: '策略筛选+入池出池',
  lhb_sync: '龙虎榜同步',
  news: '新闻采集甄别',
  holding_patrol: '持仓巡检',
  weekly_reports: '生成周报(全体用户)',
}

// 页面提示统一走这里,保证与任务中心显示的名称一致。
export function taskName(key) {
  return TASK_NAMES[key] || '未登记任务'
}

// 首次初始化建议顺序(任务中心与帮助页共用)。
export const INIT_ORDER_TEXT = ['sync_members', 'sync_basic', 'sync_tracks', 'backfill_history', 'sync_financials']
  .map(taskName).join(' → ') + ' → 其余任务'
