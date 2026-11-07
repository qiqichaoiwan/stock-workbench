// 选股池行情展示:实时快照 / 收盘快照 / 已落库降级的文案与 A 股交易时段判断。

const SPOT_SOURCES = new Set(['akshare', 'efinance', 'sina'])
const SOURCE_LABELS = { akshare: '东财', efinance: '东财', sina: '新浪' }
const tradingClockFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', hourCycle: 'h23',
  weekday: 'short', hour: '2-digit', minute: '2-digit',
})

export function quoteSourceLabel(source) {
  return SOURCE_LABELS[source] || ''
}

// 行情时间 → 'MM-DD HH:mm:ss'(去年份,保留日期以免跨日快照看起来像当天)
export function quoteStamp(iso) {
  const text = String(iso || '').replace('T', ' ')
  const match = text.match(/^\d{4}-(\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})/)
  return match ? `${match[1]} ${match[2]}` : ''
}

export function quoteItemLabel(quote) {
  if (!quote) return '暂无行情'
  if (quote.quote_kind === 'stored') return `已落库日线 ${quote.trade_date || ''}`.trim()
  if (quote.refresh_failed) return '刷新失败 · 上次行情'
  const stamp = quoteStamp(quote.quote_at)
  if (!stamp) return '快照 · 时间未核验'
  if (quote.quote_quality === 'unverified') return `时效未核验 · ${stamp}`
  if (quote.quote_quality === 'stale') return `陈旧 · ${stamp}`
  if (quote.quote_quality === 'best_effort') return `实时研究源(未授权SLA) · ${stamp}`
  if (quote.quote_quality === 'delayed') return `延迟 · ${stamp}`
  return `${quote.realtime ? '实时' : '非实时'} · ${stamp}`
}

export function markQuoteRefreshFailed(quotes) {
  return Object.fromEntries(Object.entries(quotes || {}).map(([code, quote]) =>
    [code, { ...quote, realtime: false, refresh_failed: true }]))
}

// 行情状态文案:明确区分「实时」「延迟快照」「非交易时段收盘快照」「源异常降级」
// 四种口径,避免把已落库数据或过期快照说成实时。
export function quoteStatusText(meta, loading = false) {
  if (loading && !meta?.as_of) return '行情加载中…'
  if (!meta || !meta.as_of) return '行情暂不可用(数据源异常),现价/涨跌幅见已落库数据'
  const time = quoteStamp(meta.as_of)
  if (!time) return '行情暂不可用(数据源异常),现价/涨跌幅见已落库数据'
  if (meta.errors?.includes('refresh_failed')) return `行情刷新失败，显示上次数据 · ${time}`
  const source = quoteSourceLabel(meta.source)
  const counts = meta.counts || {}
  const hasSpot = SPOT_SOURCES.has(meta.source)
    || Number(counts.spot) > 0 || Number(meta.realtime_count) > 0
  let text
  if (meta.realtime) {
    text = `行情均为实时 · 查询 ${time}${source ? ` · 来源${source}` : ''}`
  } else if (hasSpot) {
    text = `行情状态见各股 · 查询 ${time}${source ? ` · 来源${source}` : ''}`
    if (meta.realtime_count) text += ` · ${meta.realtime_count} 只实时`
  } else {
    text = `行情源暂不可用,现价/涨跌幅显示已落库收盘数据 · ${time}`
  }
  if (counts.stored) text += `;${counts.stored} 只显示已落库收盘价`
  if (counts.missing) text += `;${counts.missing} 只暂无行情`
  return text
}

// A 股连续竞价时段(北京时间):[09:30,11:30)、[13:00,15:00);午休/周末/夜间为 false。
export function isTradingSession(now = new Date()) {
  const parts = Object.fromEntries(tradingClockFormatter.formatToParts(now).map((part) => [part.type, part.value]))
  if (parts.weekday === 'Sat' || parts.weekday === 'Sun') return false
  const minutes = Number(parts.hour) * 60 + Number(parts.minute)
  return (minutes >= 570 && minutes < 690) || (minutes >= 780 && minutes < 900)
}
