import test from 'node:test'
import assert from 'node:assert/strict'
import { isTradingSession, quoteSourceLabel, quoteStamp, quoteStatusText, quoteItemLabel, markQuoteRefreshFailed } from './poolQuotes.js'

test('individual quote status and refresh failure never retain stale realtime claims', () => {
  assert.match(quoteItemLabel({ quote_kind: 'spot', realtime: false }), /时间未核验/)
  assert.match(quoteItemLabel({ quote_kind: 'spot', realtime: true, quote_at: '2026-10-29T10:21:30+08:00' }), /实时.*10-29 10:21:30/)
  const original = { '600000': { quote_kind: 'spot', realtime: true, price: 10 } }
  const failed = markQuoteRefreshFailed(original)
  assert.equal(failed['600000'].realtime, false)
  assert.equal(original['600000'].realtime, true)
  assert.match(quoteStatusText({ as_of: '2026-10-29T10:21:30+08:00', realtime: true, errors: ['refresh_failed'] }), /刷新失败/)
})

test('quote status keeps realtime, closed snapshot and degraded source distinct', () => {
  const base = { as_of: '2026-10-29T10:21:30+08:00', source: 'akshare', counts: {} }

  assert.match(quoteStatusText({ ...base, realtime: true }), /行情均为实时 · 查询 10-29 10:21:30 · 来源东财/)
  assert.doesNotMatch(quoteStatusText({ ...base, realtime: true }), /非交易时段/)

  const closed = quoteStatusText({ ...base, realtime: false, as_of: '2026-10-29T15:00:00+08:00' })
  assert.match(closed, /行情状态见各股 · 查询 10-29 15:00:00/)
  assert.doesNotMatch(closed, /最近收盘快照/)

  // 三源全挂:只能说已落库,不能把降级数据说成实时/收盘快照
  const degraded = quoteStatusText({ ...base, realtime: false, source: 'unavailable' })
  assert.match(degraded, /行情源暂不可用/)
  assert.doesNotMatch(degraded, /实时快照|最近收盘快照/)
})

test('delayed snapshot during trading is labelled with delay, not as realtime', () => {
  const text = quoteStatusText({
    as_of: '2026-10-29T10:30:00+08:00',
    snapshot_at: '2026-10-29T10:00:00+08:00',
    snapshot_age_seconds: 420,
    source: 'sina',
    session: 'trading',
    realtime: false,
    counts: { total: 23, spot: 23, stored: 0, missing: 0 },
  })
  assert.match(text, /行情状态见各股 · 查询 10-29 10:30:00/)
  assert.doesNotMatch(text, /实时快照|非交易时段|暂不可用/)
})

test('quote status reports stored fallback and missing counts', () => {
  const text = quoteStatusText({
    as_of: '2026-10-29 10:21:30',
    source: 'akshare',
    realtime: true,
    counts: { total: 3, spot: 1, stored: 1, missing: 1 },
  })
  assert.match(text, /1 只显示已落库收盘价/)
  assert.match(text, /1 只暂无行情/)
})

test('quote status fails closed without usable timestamps', () => {
  assert.match(quoteStatusText(null), /行情暂不可用/)
  assert.match(quoteStatusText({ source: 'akshare' }), /行情暂不可用/)
  assert.match(quoteStatusText({ as_of: 'not-a-time', source: 'akshare' }), /行情暂不可用/)
  assert.equal(quoteStatusText(null, true), '行情加载中…')
  // 已有数据时的后台刷新不应把文案退回加载中
  const refreshing = quoteStatusText({ as_of: '2026-10-29T10:00:00+08:00' }, true)
  assert.doesNotMatch(refreshing, /加载中/)
  assert.match(refreshing, /10-29 10:00:00/)
})

test('source labels cover the three spot sources only', () => {
  assert.equal(quoteSourceLabel('akshare'), '东财')
  assert.equal(quoteSourceLabel('efinance'), '东财')
  assert.equal(quoteSourceLabel('sina'), '新浪')
  assert.equal(quoteSourceLabel('stored_daily'), '')
  assert.equal(quoteSourceLabel(undefined), '')
})

test('quote stamp keeps month-day and time but rejects unusable values', () => {
  assert.equal(quoteStamp('2026-10-29T10:21:30+08:00'), '10-29 10:21:30')
  assert.equal(quoteStamp('2026-10-29 15:00:00'), '10-29 15:00:00')
  assert.equal(quoteStamp(''), '')
  assert.equal(quoteStamp(null), '')
  assert.equal(quoteStamp('2026-10-29'), '')
})

test('trading session uses Beijing time and A-share auction windows', () => {
  const at = (iso) => new Date(iso)

  assert.equal(isTradingSession(at('2026-10-29T01:29:00Z')), false) // 09:29
  assert.equal(isTradingSession(at('2026-10-29T01:31:00Z')), true)  // 09:31
  assert.equal(isTradingSession(at('2026-10-29T03:30:00Z')), false) // 11:30 收盘边界
  assert.equal(isTradingSession(at('2026-10-29T04:00:00Z')), false) // 12:00 午休
  assert.equal(isTradingSession(at('2026-10-29T05:30:00Z')), true)  // 13:30
  assert.equal(isTradingSession(at('2026-10-29T07:00:00Z')), false) // 15:00 收盘边界
  assert.equal(isTradingSession(at('2026-10-29T07:05:00Z')), false) // 15:05 收盘后
  assert.equal(isTradingSession(at('2026-10-31T02:00:00Z')), false) // 周六 10:00
  assert.equal(isTradingSession(at('2026-11-01T05:30:00Z')), false) // 周日 13:30
})
