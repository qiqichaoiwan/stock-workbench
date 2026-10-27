export function safeSummary(result) {
  const missing = result?.source_missing || ['未收到可验证的自动快照']
  const complete = result?.data_quality?.complete === true && missing.length === 0
  const final = result?.phase === 'final'
  const cap = complete ? Math.min(50, Number(result?.verdict?.cap) || 0) : 0
  return { cap, canOpen: complete && final && result?.decision?.can_open === true && cap > 0,
    state: !complete ? '缺失阻断 · 空仓建议' : !final ? '预警 / 观察 · 非执行' : '14:45 终判 · 仅建议', missing }
}
export function candidateState(checks = []) {
  if (checks.some(c => c.pass === false)) return 'fail'
  return checks.length && checks.every(c => c.pass === true) ? 'pass' : 'unknown'
}
export function newEntryCap(cap, hasNewEntry) { return Math.max(0, Math.min(50, Number(cap) || 0) - (hasNewEntry ? 20 : 0)) }
// Field names are the v2 engine contract; null always means unknown, never false.
export const numericFields = {
  core: 'price prev_high prev_low ma5 ma10 ma20 ma5_prev wma5 wma10 wma20 w_pct below_ma5_days high60 high pct_chg pct_chg_high',
  industry: 'price wma5 wma10 wma20 high20w previous_high20w rank amount amount_ma20 gain5 above_ma5_days',
  stock: 'price open high low prev_close ma5 ma10 ma20 prev_price prev_ma5 volume_ratio w_close wma30 macd_cross_weeks dif float_cap amount_avg limit_down5 return20 boards box_range20 prior_high20 n_bear_age n_bear_high',
  holding: 'weight pnl price ma5 boards',
  account: 'daily_pnl weekly_pnl consecutive_losses total_weight frozen_sessions',
}
export const booleanFields = {
  core: 'wma20_up4 macd_dead_cross', industry: 'wma20_up4',
  stock: 'black_swan is_core leader1 full_resonance high_upper_shadow high_volume_bear stagnation one_price_limit_up ma5_bad',
  holding: 'black_swan high_volume_bear high_upper_shadow bought_today limit_down_locked limit_up_open',
  account: 'stopped_today data_failure_today review_complete two_month_low_winrate',
}
export const fields = (kind, type='number') => (type === 'boolean' ? booleanFields : numericFields)[kind].split(' ')
export function emptyEvidence(kind) { return Object.fromEntries([...fields(kind), ...fields(kind,'boolean')].map(k => [k,null])) }
export function createCard() {
  return { revision: 0, evidence: { source: 'manual', indices: {'883957':emptyEvidence('core'),'883994':{...emptyEvidence('core'),macd_hist:[null,null,null]}}, emotion: {}, industry: emptyEvidence('industry'), stocks: {} },
    account: emptyEvidence('account'),
    candidates: [], holdings: [], checklist: {}, trades: [], next_day_plan: {}, review: {} }
}
function clean(value) {
  if (value === '') return null
  if (Array.isArray(value)) return value.map(clean)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,clean(v)]))
  return value
}
export function buildManualPayload(card, tradeDate, phase) {
  const stamp = card.evidence.captured_at
  const manual = group => ({...clean(group || {}), source:'manual', ...(stamp ? {timestamp:stamp} : {})})
  return clean({ trade_date:tradeDate, phase:phase === 'preview' ? 'warning' : phase === 'final' ? 'final' : 'observation',
    evidence:{cores:Object.fromEntries(['883957','883994'].map(code=>[code,manual(card.evidence.indices?.[code])])),
      emotion:manual(card.evidence.emotion), industry:manual(card.evidence.industry), stock:manual(card.candidates[0] || {})},
    account:card.account, holdings:card.holdings.map(manual), candidates:card.candidates.map(manual) })
}
export function toCardBody(card, tradeDate, phase) {
  const body = JSON.parse(JSON.stringify(Object.fromEntries(['revision', 'evidence', 'account', 'candidates', 'holdings', 'checklist', 'trades', 'next_day_plan', 'review'].map(k => [k, card[k]]))))
  body.evidence.calculation_input = buildManualPayload(card,tradeDate,phase)
  body.candidates = body.evidence.calculation_input.candidates
  body.holdings = body.evidence.calculation_input.holdings
  return body
}
