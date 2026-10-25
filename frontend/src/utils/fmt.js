// 全局格式化工具:数字千分位/固定位数、百分比、日期时间、A股红涨绿跌、评级配色

export function fmtNum(v, digits = 2, dash = '-') {
  const n = Number(v)
  if (v === null || v === undefined || v === '' || Number.isNaN(n)) return dash
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtInt(v, dash = '-') {
  return fmtNum(v, 0, dash)
}

export function fmtPct(v, digits = 2, dash = '-') {
  const n = Number(v)
  if (v === null || v === undefined || v === '' || Number.isNaN(n)) return dash
  return `${n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`
}

// A股口径:红涨绿跌(0 视为平,不着色)
export function upDownClass(v) {
  const n = Number(v)
  if (Number.isNaN(n) || n > 0) return n > 0 ? 'up' : ''
  return 'down'
}

// ISO 时间 → 'YYYY-MM-DD HH:mm'(已是该格式则原样;秒为 00 时省略)
export function fmtDT(iso) {
  if (!iso) return '-'
  const s = String(iso).replace('T', ' ')
  const m = s.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(:\d{2})?/)
  if (!m) return s
  return `${m[1]} ${m[2]}`
}

// 同日新闻列表:只显示 'HH:mm'
export function fmtTimeShort(iso) {
  const s = String(iso || '').replace('T', ' ')
  const m = s.match(/^\d{4}-\d{2}-\d{2} (\d{2}:\d{2})/)
  return m ? m[1] : (iso || '-')
}

// 股票名清理:去掉 XD/XR/DR 除权除息前缀(显示用)
export function cleanName(name) {
  return String(name || '').replace(/^(XD|XR|DR)/, '')
}

// 池评级 → 中文与标签配色(买入=红 / 持有=蓝 / 卖出=绿 / 回避=灰)
const RATING = {
  buy: { label: '买入', type: 'danger' },
  hold: { label: '持有', type: 'primary' },
  sell: { label: '卖出', type: 'success' },
  avoid: { label: '回避', type: 'info' },
}

export function ratingLabel(r) {
  return RATING[r]?.label || r || '-'
}

export function ratingType(r) {
  return RATING[r]?.type ?? 'info'
}

// 秒/毫秒耗时 → 可读
export function fmtDuration(ms) {
  const n = Number(ms)
  if (Number.isNaN(n) || n < 0) return '-'
  if (n < 1000) return `${n}ms`
  if (n < 60000) return `${(n / 1000).toFixed(1)}s`
  return `${Math.floor(n / 60000)}m${Math.round((n % 60000) / 1000)}s`
}
