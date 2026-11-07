import test from 'node:test'
import assert from 'node:assert/strict'
import { effectiveQuote, portfolioQuoteLabel, todayInShanghai } from './portfolioQuotes.js'
import { quoteItemLabel } from './poolQuotes.js'

test('shows each quote time and distinguishes missing, stored and unverified data', () => {
  assert.equal(portfolioQuoteLabel({quote_kind:'missing'}), '暂无行情')
  assert.equal(portfolioQuoteLabel({quote_kind:'stored', quote_at:'2026-11-05'}), '收盘 2026-11-05')
  assert.match(portfolioQuoteLabel({quote_kind:'spot', quote_source:'efinance'}), /时点未核验/)
  assert.match(portfolioQuoteLabel({quote_kind:'spot', quote_source:'sina', realtime:true, quote_at:'2026-11-06T10:00:00+08:00'}), /实时 · 新浪 · 2026-11-06 10:00:00/)
  assert.match(portfolioQuoteLabel({quote_kind:'spot', quote_source:'sina', realtime:false, quote_at:'2026-11-05T15:00:00+08:00'}), /^非实时/)
  assert.match(portfolioQuoteLabel({quote_kind:'spot', quote_source:'sina', realtime:false, refresh_failed:true, quote_at:'2026-11-06T10:00:00+08:00'}), /^刷新失败 · 上次行情/)
  assert.match(portfolioQuoteLabel({quote_kind:'stored', refresh_failed:true, quote_at:'2026-11-05'}), /^刷新失败 · 上次收盘/)
})

test('real time label expires based on each stock timestamp even after a suspended tab resumes', async () => {
  const quote = { quote_kind: 'spot', quote_source: 'sina', realtime: true, quote_at: '2026-11-06T10:00:00+08:00' }
  assert.equal(effectiveQuote(quote, Date.parse('2026-11-06T10:01:00+08:00')).realtime, true)
  assert.equal(effectiveQuote(quote, Date.parse('2026-11-06T10:01:31+08:00')).realtime, false)
  assert.equal(effectiveQuote({...quote, quote_at: null}, Date.parse('2026-11-06T10:00:10+08:00')).realtime, false)
  assert.equal(effectiveQuote(quote, Date.parse('2026-11-06T09:59:59+08:00')).realtime, false)
})

test('stream quality expires according to server windows instead of the public snapshot window', () => {
  const quote = { quote_kind: 'stream', quote_source: 'licensed', realtime: true,
    quote_quality: 'realtime', quote_at: '2026-11-06T10:00:00+08:00',
    realtime_max_age_seconds: 3, delayed_max_age_seconds: 15 }
  const at = (second) => Date.parse(`2026-11-06T10:00:${String(second).padStart(2, '0')}+08:00`)
  assert.equal(effectiveQuote(quote, at(3)).realtime, true)
  const delayed = effectiveQuote(quote, at(4))
  assert.equal(delayed.realtime, false)
  assert.equal(delayed.quote_quality, 'delayed')
  assert.match(portfolioQuoteLabel(delayed), /^延迟/)
  assert.match(quoteItemLabel(effectiveQuote(quote, at(16))), /^陈旧/)
  assert.equal(effectiveQuote({ ...quote, realtime_max_age_seconds: undefined }, at(1)).quote_quality, 'unverified')
  assert.equal(effectiveQuote({ ...quote, realtime: false, quote_quality: 'best_effort' }, at(1)).realtime, false)
  assert.equal(effectiveQuote({ ...quote, realtime: false, quote_quality: 'delayed' }, at(1)).quote_quality, 'delayed')
})

test('todayInShanghai uses the trading timezone before Beijing midnight rollover', () => {
  const beforeBeijingEight = new Date('2026-11-06T23:30:00.000Z')
  assert.equal(todayInShanghai(beforeBeijingEight), '2026-11-07')
})
