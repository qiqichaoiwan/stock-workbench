// 不把采集时间当成交易时间；缺失/停牌/延迟逐股标注。
import { isTradingSession } from './poolQuotes.js'

export function portfolioQuoteLabel(q) {
  if (!q || q.quote_kind === 'missing') return '暂无行情'
  if (q.refresh_failed) {
    if (q.quote_kind === 'stored') return `刷新失败 · 上次收盘 ${q.quote_at || '日期未知'}`
    return `刷新失败 · 上次行情 ${formatTime(q.quote_at)}`
  }
  if (q.quote_kind === 'stored') return `收盘 ${q.quote_at || '日期未知'}`
  const source = { sina: '新浪', akshare: '东财', efinance: '东财' }[q.quote_source] || q.quote_source || '未知来源'
  if (!q.quote_at) return `行情时点未核验 · ${source}`
  if (q.quote_quality === 'unverified') return `时效未核验 · ${source} · ${formatTime(q.quote_at)}`
  if (q.quote_quality === 'stale') return `陈旧 · ${source} · ${formatTime(q.quote_at)}`
  if (q.quote_quality === 'best_effort') return `实时研究源(未授权SLA) · ${source} · ${formatTime(q.quote_at)}`
  if (q.quote_quality === 'delayed') return `延迟 · ${source} · ${formatTime(q.quote_at)}`
  return `${q.realtime ? '实时' : '非实时'} · ${source} · ${formatTime(q.quote_at)}`
}
function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '时间未知'
}

// Date inputs represent a trading date in Shanghai, not the browser's UTC
// date. Using toISOString() makes users in Asia/Shanghai see yesterday before
// 08:00, which silently records an incorrect buy/sell date.
export function todayInShanghai(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now).map((part) => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day}`
}

// 标签页休眠恢复后，服务端响应时的质量状态会过期。推送源必须使用
// 后端提供的质量窗口；缺少窗口时不猜测 SLA，公共快照兼容旧版 90 秒。
export function effectiveQuote(quote, currentTime = Date.now()) {
  if (!quote || quote.quote_kind === 'stored' || quote.quote_kind === 'missing') return quote
  if (quote.refresh_failed) return { ...quote, realtime: false }
  const rawStamp = quote.quote_at ? String(quote.quote_at) : ''
  const stamp = rawStamp
    ? Date.parse(/Z$|[+-]\d{2}:?\d{2}$/.test(rawStamp) ? rawStamp : `${rawStamp.replace(' ', 'T')}+08:00`)
    : NaN
  const age = currentTime - stamp
  const validAge = Number.isFinite(age) && age >= 0
  if (quote.quote_kind === 'stream') {
    const realtimeWindow = Number(quote.realtime_max_age_seconds)
    const delayedWindow = Number(quote.delayed_max_age_seconds)
    if (!validAge || !Number.isFinite(realtimeWindow) || realtimeWindow <= 0
        || !Number.isFinite(delayedWindow) || delayedWindow < realtimeWindow) {
      return { ...quote, realtime: false, quote_quality: 'unverified' }
    }
    let quality = quote.quote_quality
    if (age > delayedWindow * 1000) quality = 'stale'
    else if (age > realtimeWindow * 1000 && ['realtime', 'best_effort'].includes(quality)) quality = 'delayed'
    return { ...quote, quote_quality: quality,
      realtime: !!quote.realtime && quality === 'realtime' && isTradingSession(new Date(currentTime)) }
  }
  const configuredWindow = Number(quote.realtime_max_age_seconds)
  const realtimeWindow = Number.isFinite(configuredWindow) && configuredWindow > 0 ? configuredWindow : 90
  return { ...quote, realtime: !!quote.realtime && validAge && age <= realtimeWindow * 1000
    && isTradingSession(new Date(currentTime)) }
}
