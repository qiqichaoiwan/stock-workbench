import test from 'node:test'
import assert from 'node:assert/strict'
import { safeSummary, candidateState, newEntryCap, createCard, toCardBody } from './dualCoreCard.mjs'

test('missing automatic evidence cannot authorize a final decision', () => {
  const summary = safeSummary(null)
  assert.equal(summary.canOpen, false)
  assert.equal(summary.cap, 0)
  assert.match(summary.state, /阻断/)
})

test('unverified strategy caps at 50 and preview cannot open', () => {
  const result = { phase: 'warning', source_missing: [], data_quality: { complete: true }, verdict: { cap: 100 }, decision: { can_open: true } }
  assert.equal(safeSummary(result).cap, 50)
  assert.equal(safeSummary(result).canOpen, false)
})
test('new positions subtract 20 percentage points, trial becomes zero', () => {
  assert.equal(newEntryCap(50, true), 30)
  assert.equal(newEntryCap(10, true), 0)
  assert.equal(newEntryCap(10, false), 10)
})
test('candidate unknown or failed automatic filter never passes', () => {
  assert.equal(candidateState([{ pass: true }, { pass: null }]), 'unknown')
  assert.equal(candidateState([{ pass: true }, { pass: false }]), 'fail')
  assert.equal(candidateState([{ pass: true }]), 'pass')
  assert.equal(candidateState([]), 'unknown')
})
test('manual payload maps exact engine fields, preserves unknown and saves the same calculation input', async () => {
  const { buildManualPayload } = await import('./dualCoreCard.mjs')
  const card = createCard()
  card.evidence.captured_at = '2026-10-28T14:45:00+08:00'
  card.evidence.indices['883957'] = {price:110,wma20_up4:null}
  card.evidence.emotion = {limit_up:60,broken:10,amount:130,amount_ma5:100}
  card.account.total_weight = 20
  card.candidates.push({code:'000001',black_swan:null,w_close:10})
  card.holdings.push({code:'000002',weight:20,limit_down_locked:null,bought_today:null})
  const payload = buildManualPayload(card, '2026-10-28', 'final')
  assert.equal(payload.evidence.cores['883957'].price,110)
  assert.equal(payload.evidence.cores['883957'].wma20_up4,null)
  assert.equal(payload.evidence.cores['883957'].source,'manual')
  assert.equal(payload.evidence.cores['883957'].timestamp,card.evidence.captured_at)
  assert.equal(payload.evidence.emotion.broken,10)
  assert.equal(payload.evidence.emotion.amount_ma5,100)
  assert.equal(payload.account.total_weight,20)
  assert.equal(payload.holdings[0].limit_down_locked,null)
  assert.equal(payload.candidates[0].black_swan,null)
  assert.deepEqual(toCardBody(card,'2026-10-28','final').evidence.calculation_input,payload)
  assert.equal(createCard().account.review_complete,null)
  assert.equal(buildManualPayload(createCard()).evidence.emotion.timestamp,undefined)
  card.evidence.emotion.limit_up = ''
  assert.equal(buildManualPayload(card).evidence.emotion.limit_up,null)
})

test('structured inputs retain explicit unknown without inventing safe risks', async () => {
  const {emptyEvidence} = await import('./dualCoreCard.mjs')
  assert.equal(emptyEvidence('holding').limit_down_locked,null)
  assert.equal(emptyEvidence('stock').black_swan,null)
})

test('card save strips server fields and preserves manual inputs and records', () => {
  const card = createCard()
  card.trades.push({ code: '600000', status: '未执行', source: 'manual' })
  card.updated_at = 'server'
  assert.equal(toCardBody(card).revision, 0)
  assert.equal(toCardBody(card).evidence.source, 'manual')
  assert.equal(toCardBody(card).trades[0].status, '未执行')
  assert.equal('updated_at' in toCardBody(card), false)
})
