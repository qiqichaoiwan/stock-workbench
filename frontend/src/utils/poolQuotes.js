// 选股池行情展示:实时快照 / 收盘快照 / 已落库降级的文案与 A 股交易时段判断。

const SPOT_SOURCES = new Set(['akshare', 'efinance', 'sina'])
const SOURCE_LABELS = { akshare: '东财', efinance: '东财', sina: '新浪' }

export function quoteSourceLabel(source) {
  return SOURCE_LABELS[source] || ''
}

// 行情时间 → 'MM-DD HH:mm:ss'(去年份,保留日期以免跨日快照看起来像当天)
export function quoteStamp(iso) {
  const text = String(iso || '').replace('T', ' ')
  const match = text.match(/^\d{4}-(\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})/)
  return match ? `${match[1]} ${match[2]}` : ''
}

// 行情状态文案:明确区分「实时」「延迟快照」「非交易时段收盘快照」「源异常降级」
// 四种口径,避免把已落库数据或过期快照说成实时。
export function quoteStatusText(meta, loading = false) {
  if (loading && !meta?.as_of) return '行情加载中…'
  if (!meta || !meta.as_of) return '行情暂不可用(数据源异常),现价/涨跌幅见已落库数据'
  const time = quoteStamp(meta.snapshot_at || meta.as_of)
  if (!time) return '行情暂不可用(数据源异常),现价/涨跌幅见已落库数据'
  const source = quoteSourceLabel(meta.source)
  const counts = meta.counts || {}
  let text
  if (meta.realtime) {
    text = `行情 实时快照 · ${time}${source ? ` · 来源${source}` : ''}`
  } else if (SPOT_SOURCES.has(meta.source) && meta.session === 'trading') {
    text = `行情快照延迟 · ${time}${source ? ` · 来源${source}` : ''}${delayText(meta.snapshot_age_seconds)}`
  } else if (SPOT_SOURCES.has(meta.source)) {
    text = `行情 最近收盘快照 · ${time}${source ? ` · 来源${source}` : ''}(非交易时段)`
  } else {
    text = `行情源暂不可用,现价/涨跌幅显示已落库收盘数据 · ${time}`
  }
  if (counts.stored) text += `;${counts.stored} 只显示已落库收盘价`
  if (counts.missing) text += `;${counts.missing} 只暂无行情`
  return text
}

function delayText(age) {
  const seconds = Number(age)
  if (!Number.isFinite(seconds) || seconds < 0) return ''
  if (seconds < 60) return `,数据延迟约 ${Math.round(seconds)} 秒`
  return `,数据延迟约 ${Math.round(seconds / 60)} 分钟`
}

// A 股连续竞价时段(北京时间):09:30–11:30、13:00–15:00;午休/周末/夜间为 false。
export function isTradingSession(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', hourCycle: 'h23',
    weekday: 'short', hour: '2-digit', minute: '2-digit',
  }).formatToParts(now).map((part) => [part.type, part.value]))
  if (parts.weekday === 'Sat' || parts.weekday === 'Sun') return false
  const minutes = Number(parts.hour) * 60 + Number(parts.minute)
  return (minutes >= 570 && minutes <= 690) || (minutes >= 780 && minutes <= 900)
}
