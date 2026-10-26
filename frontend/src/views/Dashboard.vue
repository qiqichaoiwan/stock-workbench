<template>
  <div ref="root" class="cockpit">
    <header class="terminal-head">
      <div class="identity"><span class="terminal-mark"><el-icon><DataAnalysis /></el-icon></span><div><h1>市场驾驶舱</h1><p>行情 / 资金 / 事件 / 策略</p></div></div>
      <div class="session-block"><span class="session" :class="{ trading: session.state === 'trading' && !marketError }">{{ marketError ? '行情连接异常' : session.label || '交易状态待确认' }}</span><small>{{ session.calendar_verified ? '交易日历已核验' : '交易日历未核验' }}</small></div>
      <div class="clock"><strong>{{ clockText }}</strong><small>北京时间 UTC+8</small></div>
      <div class="toolbar">
        <span class="countdown">{{ pauseLabel || `行情 ${countdown('market')}s / 新闻 ${countdown('news')}s` }}</span>
        <button :aria-pressed="paused" @click="togglePause"><el-icon><component :is="paused ? VideoPlay : VideoPause" /></el-icon>{{ paused ? '恢复刷新' : '暂停刷新' }}</button>
        <button class="primary" :disabled="busy" @click="refreshAll"><el-icon><Refresh /></el-icon>{{ busy ? '读取中' : '手动刷新' }}</button>
        <button :aria-label="fullscreen ? '退出全屏' : '进入全屏'" @click="toggleFullscreen"><el-icon><FullScreen /></el-icon>{{ fullscreen ? '退出全屏' : '全屏' }}</button>
      </div>
    </header>
    <p v-if="fullscreenError" class="notice" role="alert">{{ fullscreenError }}</p>
    <div class="feed-strip"><span>指数为 <b>1分钟分时</b>，不代表逐笔行情</span><span>{{ session.state === 'trading' ? '交易时行情每30秒 / 新闻每60秒读取' : '非交易时每60秒读取缓存，不代表实时更新' }}</span><span v-if="marketError" class="warning">获取失败，保留上次数据</span></div>

    <section class="index-grid" aria-label="四大指数分时行情">
      <article v-for="ix in charts" :key="ix.code" class="index-panel">
        <div class="index-heading"><h2>{{ ix.name }} <small>{{ ix.code.toUpperCase() }}</small></h2><span class="data-state" :class="{ warning: ix.status !== 'ok' || marketError }">{{ marketError ? '陈旧 / 获取失败' : statusLabel(ix.status) }}</span></div>
        <div class="quote"><strong :class="tone(ix.pct_chg)">{{ num(ix.last) }}</strong><span :class="tone(ix.pct_chg)">{{ signed(ix.pct_chg, '%') }}</span><small>昨收 {{ num(ix.prev_close) }}</small></div>
        <div class="chart-wrap">
          <svg v-if="ix.plot.length" class="chart" viewBox="0 0 400 164" preserveAspectRatio="none" role="img" :aria-label="`${ix.name}一分钟分时，${ix.plot.length}个真实采样点`" @pointermove="hoverChart($event, ix)" @pointerleave="hover = null">
            <line v-for="y in [16, 70, 124]" :key="y" x1="8" x2="392" :y1="y" :y2="y" class="grid-line" />
            <text x="10" y="12" class="axis-text">{{ num(ix.high) }}</text><text x="10" y="137" class="axis-text">{{ num(ix.low) }}</text>
            <template v-if="finite(ix.prev_close)"><line x1="8" x2="392" :y1="ix.baseline" :y2="ix.baseline" class="baseline" /><text x="390" :y="Math.max(12, ix.baseline - 4)" text-anchor="end" class="axis-text">昨收</text></template>
            <path :d="ix.path" fill="none" :class="['price-line', tone(ix.pct_chg)]" />
            <circle v-if="ix.plot.length === 1" :cx="ix.plot[0].x" :cy="ix.plot[0].y" r="3" class="single-point" />
            <template v-if="hover?.code === ix.code"><line :x1="hover.point.x" :x2="hover.point.x" y1="16" y2="124" class="crosshair" /><circle :cx="hover.point.x" :cy="hover.point.y" r="3" class="hover-dot" /></template>
            <text x="8" y="158" class="axis-text">09:30</text><text x="200" y="158" text-anchor="middle" class="axis-text">11:30 / 13:00</text><text x="392" y="158" text-anchor="end" class="axis-text">15:00</text>
          </svg>
          <div v-else class="chart-empty" :class="{ skeleton: ix.status === 'loading' && !marketError }"><el-icon><TrendCharts /></el-icon><span>{{ ix.status === 'loading' && !marketError ? '等待后端采集分时数据' : '暂无可用分时数据' }}</span><small>不使用日线或模拟走势代替</small></div>
          <div v-if="hover?.code === ix.code" class="chart-tip" role="status">{{ dateText(hover.point.time) }} <b>{{ num(hover.point.value) }}</b></div>
        </div>
        <footer class="source-line"><span>{{ ix.source || '来源待确认' }}</span><time>{{ dateText(ix.data_time) }}</time></footer>
        <p v-if="ix.error" class="inline-error">{{ ix.error }}</p>
      </article>
    </section>

    <section class="main-grid">
      <article class="panel flow-panel">
        <header class="panel-heading"><div><h2>板块资金方向</h2><p>今日累计主力净流入 <span class="unit">亿元</span></p></div><div class="segmented" aria-label="板块类型"><button v-for="tab in flowTabs" :key="tab.key" :aria-pressed="flowTab === tab.key" @click="flowTab = tab.key">{{ tab.label }}</button></div></header>
        <div class="flow-summary"><div><small>净流入板块合计</small><strong class="up">{{ flowRows.length ? signed(flowStats.inflow) : '-' }}</strong></div><div><small>净流出板块合计</small><strong class="down">{{ flowRows.length ? signed(flowStats.outflow) : '-' }}</strong></div><div><small>截面板块 / 净流入</small><strong>{{ flowRows.length || '-' }} <em>/ {{ flowRows.length ? flowStats.positive : '-' }}</em></strong></div></div>
        <div class="flow-legend"><span class="down">← 净流出</span><span>按净流入排序，展示两端最多各6个</span><span class="up">净流入 →</span></div>
        <div v-if="rankedBoards.length" class="flow-chart" role="img" aria-label="板块主力资金正负分叉排行，单位亿元">
          <div v-for="(board, i) in rankedBoards" :key="`${board.name}-${i}`" class="flow-row"><span class="board-name" :title="board.name">{{ board.name }}</span><div class="bar-space"><span class="zero-line" /><span :class="['flow-bar', tone(board.net_inflow)]" :style="barStyle(board.net_inflow)" /></div><strong :class="tone(board.net_inflow)">{{ signed(board.net_inflow) }}</strong></div>
        </div>
        <div v-else class="empty-state flow-empty" :class="{ skeleton: flow.status === 'loading' && !marketError }"><el-icon><DataLine /></el-icon><h3>{{ flow.status === 'loading' && !marketError ? '正在等待资金截面' : '暂无可用资金数据' }}</h3><p>采集完成后显示真实板块排行，不推断板块之间的资金转移。</p></div>
        <footer class="panel-footer"><span :class="{ warning: marketError || flow.status !== 'ok' }">{{ marketError ? '获取失败，已有截面已标陈旧' : statusLabel(flow.status) }}{{ flow.error ? `：${flow.error}` : '' }}</span><span>{{ flow.source || '来源待确认' }} / {{ dateText(flow.data_time) }}</span></footer>
        <p class="method-note">合计仅覆盖接口返回板块，行业与概念可能重叠，不等同全市场资金总额。</p>
      </article>

      <article class="panel news-panel">
        <header class="panel-heading"><div><h2>实时新闻</h2><p>每60秒自动读取 · 事件时间轴 <span class="unit">{{ filteredNews.length }} 条</span></p></div><div class="segmented" aria-label="新闻筛选"><button :aria-pressed="importantOnly" @click="importantOnly = true">重点</button><button :aria-pressed="!importantOnly" @click="importantOnly = false">全部</button></div></header>
        <div class="news-list">
          <article v-for="item in filteredNews" :key="item.id" class="news-item" :class="{ important: item.important }">
            <div class="news-meta"><time>{{ dateText(item.published_at) }}</time><span v-if="item.important" class="important-flag">重要</span><span>{{ item.source || '来源未注明' }}</span></div>
            <h3>{{ item.title }}</h3><p class="news-content" :class="{ expanded: expanded.has(item.id) }">{{ item.content || '暂无正文，请查看原文。' }}</p>
            <div class="news-actions"><button :aria-expanded="expanded.has(item.id)" @click="toggleNews(item.id)">{{ expanded.has(item.id) ? '收起全文' : '展开全文' }}<el-icon><ArrowDown /></el-icon></button><a v-if="safeUrl(item.url)" :href="safeUrl(item.url)" target="_blank" rel="noopener noreferrer">查看原文 <el-icon><TopRight /></el-icon></a></div>
          </article>
          <div v-if="!filteredNews.length" class="empty-state"><el-icon><Document /></el-icon><h3>{{ news.status === 'loading' && !newsError ? '等待新闻采集' : importantOnly && news.items.length ? '暂无重点标记新闻' : '暂无可用新闻' }}</h3><p>{{ importantOnly && news.items.length ? '切换“全部”查看其他新闻。' : '保留事件时间与来源，采集后自动呈现。' }}</p></div>
        </div>
        <footer class="panel-footer"><span :class="{ warning: newsError || news.status !== 'ok' }">{{ newsError ? '获取失败，保留上次新闻 / 陈旧' : statusLabel(news.status) }}{{ news.error ? `：${news.error}` : '' }}</span><span>{{ news.source || '来源待确认' }} / {{ dateText(news.data_time) }}</span></footer>
      </article>
    </section>

    <section class="strategy-section" aria-label="策略观察">
      <header class="strategy-heading"><h2>策略观察</h2><p>日级策略数据，非实时交易指令 <span v-if="strategyError" class="warning">获取失败{{ strategyLoaded ? '，保留上次数据 / 陈旧' : '' }}</span><span v-else-if="!strategyLoaded">读取中</span><span v-else>最近读取 {{ dateText(strategyReadAt) }}</span></p></header>
      <div class="strategy-grid">
        <article class="strategy-cell market-cell"><h3>市场状态 <router-link to="/market">大盘详情 <el-icon><TopRight /></el-icon></router-link></h3><template v-if="d.market_state"><div class="strategy-value">{{ d.market_state.trend_label || '-' }} <small>{{ d.market_state.cycle_stage || '-' }}</small></div><div class="position">建议仓位 <b>{{ d.market_state.position_range || '-' }}</b><span class="warning">PM02 草案，待验证</span></div><dl class="market-stats"><div><dt>市场宽度</dt><dd>{{ num(d.market_state.breadth, 1) }}%</dd></div><div><dt>成交额60日分位</dt><dd>{{ num(d.market_state.amount_pct_60d, 1) }}%</dd></div><div><dt>全A估值分位</dt><dd>{{ num(d.market_state.valuation_pct, 1) }}%</dd></div><div><dt>融资/成交额</dt><dd>{{ num(d.market_state.margin_ratio, 4) }}</dd></div></dl><p class="muted">{{ d.market_state.trade_date || '日期待确认' }} / 风险收紧：{{ d.risk_guard === true ? '生效' : d.risk_guard === false ? '未触发' : '待确认' }}</p></template><p v-else class="compact-empty">暂无市场状态，请在任务中心运行「{{ taskName('market_state') }}」任务。</p></article>
        <article class="strategy-cell"><h3>拥挤度回避 <router-link to="/industries">行业 <el-icon><TopRight /></el-icon></router-link></h3><p class="warning">SC-U08 规则，待验证</p><p v-if="d.market_wide_crowded" class="up">全市场过度拥挤，建议整体降仓</p><div v-if="d.crowd_avoid?.length" class="tags"><span v-for="name in d.crowd_avoid" :key="name">{{ name }}</span></div><p v-else class="compact-empty">{{ strategyLoaded ? '当前无拥挤行业' : '等待策略数据' }}</p><p class="method-note">行业成交占比偏离自身 MA120 超300%时列入回避。</p></article>
        <article class="strategy-cell"><h3>选股池表现 <router-link to="/pool">选股池 <el-icon><TopRight /></el-icon></router-link></h3><template v-if="d.pool_perf?.trade_date"><div class="strategy-value mono" :class="tone(d.pool_perf.avg_cum_pct)">{{ signed(d.pool_perf.avg_cum_pct, '%') }}</div><p>自入选累计绝对收益</p><div class="pool-excess">相对全A超额 <b :class="tone(d.pool_perf.avg_excess_pct)">{{ signed(d.pool_perf.avg_excess_pct, '%') }}</b></div><p class="muted">{{ num(d.pool_perf.count, 0) }} 只 / {{ d.pool_perf.trade_date }}</p></template><p v-else class="compact-empty">暂无池表现数据</p></article>
        <article class="strategy-cell signals-cell"><h3>技术信号 <router-link to="/signals">全部 <el-icon><TopRight /></el-icon></router-link></h3><p class="muted">{{ signalDate || '日期待确认' }}</p><div class="mini-table"><div v-for="(s, i) in (d.signals || []).slice(0, 5)" :key="`${s.code}-${s.type}-${i}`" class="signal-row"><span class="mono muted">{{ s.code }}</span><span>{{ cleanName(s.name) || s.code || '-' }}</span><span class="signal-tag">{{ typeLabel(s.type) }}</span><b class="mono">{{ num(s.strength, 1) }}</b></div></div><p v-if="!d.signals?.length" class="compact-empty">暂无技术信号</p></article>
        <article class="strategy-cell commodities-cell"><h3>商品涨价信号 <span class="muted">供需跟踪</span></h3><div class="mini-table"><div v-for="(c, i) in commodities" :key="`${c.series_name}-${i}`" class="commodity-row"><span>{{ c.series_name }}</span><span class="signal-tag">{{ c.signal || '-' }}</span><b class="mono" :class="tone(c.mom_20)">{{ signed(c.mom_20, '%') }}</b><small>{{ c.related_track || '关联赛道待确认' }}</small></div></div><p v-if="!commodities.length" class="compact-empty">暂无商品信号</p><p class="method-note">20日动量 / 仅展示有信号或动量显著品种</p></article>
      </div>
    </section>
    <footer class="terminal-footer"><span>数据缺失以“-”表示；陈旧数据不作为即时行情。</span><router-link to="/tasks">前往任务中心 <el-icon><TopRight /></el-icon></router-link></footer>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { DataAnalysis, DataLine, TrendCharts, Document, Refresh, FullScreen, VideoPause, VideoPlay, ArrowDown, TopRight } from '@element-plus/icons-vue'
import api from '../api'
import { cleanName } from '../utils/fmt'
import { taskName } from '../utils/tasks'

const root = ref(null)
const d = ref({})
const market = ref({ session: {}, indexes: [], fund_flow: {} })
const news = ref({ items: [], status: 'loading' })
const marketError = ref(false)
const newsError = ref(false)
const strategyError = ref(false)
const strategyLoaded = ref(false)
const strategyReadAt = ref(null)
const paused = ref(false)
const hidden = ref(document.hidden)
const now = ref(Date.now())
const fullscreen = ref(false)
const fullscreenError = ref('')
const flowTab = ref('industry')
const flowTabs = [{ key: 'industry', label: '行业' }, { key: 'concept', label: '概念' }]
const importantOnly = ref(false)
const expanded = ref(new Set())
const hover = ref(null)
const inFlight = ref({ market: false, news: false, strategy: false })
const deadlines = ref({ market: 0, news: 0, strategy: 0 })
const retries = { market: 0, news: 0 }
const controllers = {}
const sequences = { market: 0, news: 0, strategy: 0 }
let disposed = false
let ticker
const indexDefinitions = [{ code: 'sh000001', name: '上证指数' }, { code: 'sz399001', name: '深证成指' }, { code: 'sz399006', name: '创业板指' }, { code: 'sh000688', name: '科创50' }]
const session = computed(() => market.value.session || {})
const busy = computed(() => Object.values(inFlight.value).some(Boolean))
const pauseLabel = computed(() => hidden.value ? '页面隐藏，自动刷新暂停' : paused.value ? '自动刷新已暂停' : '')
const clockText = computed(() => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(now.value))
const finite = (v) => typeof v === 'number' && Number.isFinite(v)
function num(v, digits = 2) { return finite(v) ? v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '-' }
function signed(v, suffix = '') { return finite(v) ? `${v > 0 ? '+' : ''}${num(v)}${suffix}` : '-' }
function tone(v) { return finite(v) && v !== 0 ? v > 0 ? 'up' : 'down' : '' }
function statusLabel(status) { return ({ ok: '已更新', stale: '陈旧数据', loading: '采集中', unavailable: '数据不可用' })[status] || '等待数据' }
function dateText(value) {
  if (!value) return '-'
  const str = String(value)
  const date = new Date(/Z$|[+-]\d{2}:?\d{2}$/.test(str) ? str : `${str.replace(' ', 'T')}+08:00`)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}
function safeUrl(value) { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : '' } catch { return '' } }
function countdown(key) { return Math.max(0, Math.ceil((deadlines.value[key] - now.value) / 1000)) }
function interval(key) { return key === 'market' && session.value.state === 'trading' ? 30000 : 60000 }

function tradingMinute(time) {
  const match = String(time || '').match(/\s(\d{2}):(\d{2}):\d{2}$/)
  if (!match) return null
  const minute = Number(match[1]) * 60 + Number(match[2])
  if (minute >= 570 && minute <= 690) return minute - 570
  if (minute >= 780 && minute <= 900) return minute - 660
  return null
}
const charts = computed(() => indexDefinitions.map((definition) => {
  const ix = market.value.indexes?.find((item) => item.code === definition.code) || { ...definition, status: 'loading' }
  const points = (ix.points || []).filter((p) => finite(p.value) && tradingMinute(p.time) !== null).slice().sort((a, b) => a.time.localeCompare(b.time))
  const values = points.map((p) => p.value)
  if (finite(ix.prev_close)) values.push(ix.prev_close)
  const min = values.length ? Math.min(...values) : 0
  const max = values.length ? Math.max(...values) : 0
  const padding = Math.max((max - min) * 0.12, Math.abs(max) * 0.0003, 0.01)
  const low = min - padding
  const high = max + padding
  const y = (value) => 124 - (value - low) / (high - low) * 108
  const plot = points.map((p) => ({ ...p, x: 8 + tradingMinute(p.time) / 240 * 384, y: y(p.value) }))
  const path = plot.map((p, i) => `${!i || tradingMinute(p.time) - tradingMinute(plot[i - 1].time) > 2 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ')
  return { ...definition, ...ix, plot, path, low, high, baseline: finite(ix.prev_close) ? y(ix.prev_close) : null }
}))
function hoverChart(event, ix) {
  const box = event.currentTarget.getBoundingClientRect()
  const x = (event.clientX - box.left) / box.width * 400
  const point = ix.plot.reduce((best, p) => Math.abs(p.x - x) < Math.abs(best.x - x) ? p : best, ix.plot[0])
  if (point) hover.value = { code: ix.code, point }
}
const flow = computed(() => market.value.fund_flow?.[flowTab.value] || { boards: [], status: 'loading' })
const flowRows = computed(() => (flow.value.boards || []).filter((b) => b.name && finite(b.net_inflow)))
const rankedBoards = computed(() => {
  const sorted = flowRows.value.slice().sort((a, b) => b.net_inflow - a.net_inflow)
  if (sorted.length <= 12) return sorted
  return [...sorted.slice(0, 6), ...sorted.slice(-6)]
})
const flowStats = computed(() => flowRows.value.reduce((sum, b) => { if (b.net_inflow > 0) { sum.inflow += b.net_inflow; sum.positive++ } else sum.outflow += b.net_inflow; return sum }, { inflow: 0, outflow: 0, positive: 0 }))
const flowMax = computed(() => Math.max(...rankedBoards.value.map((b) => Math.abs(b.net_inflow)), 0.01))
function barStyle(value) { const width = Math.abs(value) / flowMax.value * 50; return { left: `${value < 0 ? 50 - width : 50}%`, width: `${width}%` } }
const filteredNews = computed(() => (news.value.items || []).filter((item) => !importantOnly.value || item.important).slice().sort((a, b) => String(b.published_at).localeCompare(String(a.published_at))))
function toggleNews(id) { const next = new Set(expanded.value); next.has(id) ? next.delete(id) : next.add(id); expanded.value = next }
const commodities = computed(() => (d.value.rising_commodities || []).filter((c) => c.signal || finite(c.mom_20) && Math.abs(c.mom_20) > 0.5).slice(0, 5))
const signalDate = computed(() => (d.value.signals || []).map((s) => s.trade_date).filter(Boolean).sort().at(-1))
function typeLabel(type) { return ({ buy_setup: '入场组合', bottom_fractal: '底分型', red_streak: '连红', volume_surge: '量能放大', rs_strong: '强于行业', rs_weak: '弱于行业' })[type] || type || '-' }

function preserveFeed(previous, incoming, field) {
  if (!incoming) return previous ? { ...previous, status: 'stale', error: '本次响应缺少该数据' } : { [field]: [], status: 'unavailable' }
  const failed = incoming.status !== 'ok'
  const keep = failed && !incoming[field]?.length && previous?.[field]?.length
  return keep ? { ...previous, ...incoming, [field]: previous[field], data_time: previous.data_time, source: previous.source, status: incoming.status === 'loading' ? 'loading' : 'stale' } : incoming
}
async function fetchResource(key) {
  if (disposed || hidden.value || inFlight.value[key]) return
  const seq = ++sequences[key]
  const controller = new AbortController()
  controllers[key] = controller
  inFlight.value[key] = true
  let waiting = false
  try {
    const url = key === 'strategy' ? '/api/screener/dashboard' : `/api/screener/cockpit/${key}`
    const data = await api.get(url, { silent: true, signal: controller.signal })
    if (disposed || seq !== sequences[key] || controller.signal.aborted) return
    if (!data || typeof data !== 'object') throw new Error('响应格式异常')
    if (key === 'market') {
      if (!Array.isArray(data.indexes) || !data.fund_flow || !data.session) throw new Error('行情响应格式异常')
      const old = market.value
      const indexes = indexDefinitions.map((ix) => {
        const previous = old.indexes?.find((item) => item.code === ix.code)
        const incoming = data.indexes.find((item) => item.code === ix.code)
        const merged = preserveFeed(previous, incoming, 'points')
        if (incoming?.status !== 'ok' && previous) return { ...merged, ...Object.fromEntries(['last', 'pct_chg', 'prev_close'].map((field) => [field, finite(incoming?.[field]) ? incoming[field] : previous[field]])), ...ix }
        return { ...merged, ...ix }
      })
      market.value = { ...data, indexes, fund_flow: Object.fromEntries(flowTabs.map(({ key: tab }) => [tab, preserveFeed(old.fund_flow?.[tab], data.fund_flow?.[tab], 'boards')])) }
      marketError.value = false
      waiting = indexes.some((ix) => ix.status === 'loading') || flowTabs.some(({ key: tab }) => data.fund_flow[tab]?.status === 'loading')
    } else if (key === 'news') {
      if (!Array.isArray(data.items)) throw new Error('新闻响应格式异常')
      news.value = preserveFeed(news.value, data, 'items')
      newsError.value = false
      waiting = data.status === 'loading'
      const ids = new Set(news.value.items.map((item) => item.id))
      expanded.value = new Set([...expanded.value].filter((id) => ids.has(id)))
    } else {
      d.value = data
      strategyLoaded.value = true
      strategyError.value = false
      strategyReadAt.value = new Date().toISOString()
    }
  } catch {
    if (disposed || seq !== sequences[key] || controller.signal.aborted) return
    if (key === 'market') marketError.value = true
    else if (key === 'news') newsError.value = true
    else strategyError.value = true
  } finally {
    if (!disposed && seq === sequences[key]) {
      inFlight.value[key] = false
      // 首次异步采集最多快速读取缓存6次，之后恢复正常频率。
      const quick = key !== 'strategy' && waiting && retries[key] < 6
      if (quick) retries[key]++
      else if (key !== 'strategy') retries[key] = 6
      deadlines.value[key] = Date.now() + (quick ? 3000 : interval(key))
    }
  }
}
function refreshAll() {
  if (busy.value || hidden.value || disposed) return
  hover.value = null
  return Promise.all(['market', 'news', 'strategy'].map(fetchResource))
}
function togglePause() { paused.value = !paused.value; if (!paused.value) tick() }
function tick() {
  now.value = Date.now()
  if (paused.value || hidden.value || disposed) return
  for (const key of ['market', 'news', 'strategy']) if (now.value >= deadlines.value[key]) fetchResource(key)
}
function onVisibility() {
  hidden.value = document.hidden
  if (hidden.value) {
    for (const key of Object.keys(controllers)) { sequences[key]++; controllers[key].abort(); inFlight.value[key] = false }
  } else tick()
}
function onFullscreen() { fullscreen.value = document.fullscreenElement === root.value }
async function toggleFullscreen() {
  fullscreenError.value = ''
  try {
    if (document.fullscreenElement === root.value) await document.exitFullscreen()
    else if (root.value?.requestFullscreen) await root.value.requestFullscreen()
    else fullscreenError.value = '当前浏览器不支持全屏，请使用浏览器全屏功能。'
  } catch { if (!disposed) fullscreenError.value = '无法切换全屏，浏览器权限或运行环境限制了此操作。' }
}
onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
  document.addEventListener('fullscreenchange', onFullscreen)
  tick()
  ticker = window.setInterval(tick, 1000)
})
onBeforeUnmount(() => {
  disposed = true
  window.clearInterval(ticker)
  document.removeEventListener('visibilitychange', onVisibility)
  document.removeEventListener('fullscreenchange', onFullscreen)
  for (const controller of Object.values(controllers)) controller.abort()
})
</script>

<style scoped>
.cockpit { --bg: #09121f; --surface: #0e1c2d; --line: #25384c; --text: #e2edf7; --muted: #9baec2; --accent: #65d2ec; --red: #ff818a; --green: #59d5a4; background: var(--bg); color: var(--text); padding: 18px; min-width: 0; font-size: 12px; line-height: 1.5; color-scheme: dark; }
.cockpit, .cockpit * { box-sizing: border-box; }
.cockpit:fullscreen { width: 100%; height: 100%; overflow: auto; padding: 22px; }
.cockpit h1, .cockpit h2, .cockpit h3, .cockpit p { margin: 0; }
.cockpit h2 { font-size: 15px; font-weight: 650; }
.cockpit h3 { font-size: 13px; font-weight: 600; }
.cockpit small { font-size: 11px; }
.cockpit a { color: var(--accent); text-decoration: none; }
.cockpit a:hover { text-decoration: underline; }
.cockpit button { appearance: none; display: inline-flex; align-items: center; justify-content: center; gap: 5px; padding: 7px 10px; border: 1px solid #354d64; border-radius: 3px; background: #13273b; color: var(--text); cursor: pointer; font: inherit; white-space: nowrap; }
.cockpit button:hover { background: #20394f; border-color: var(--accent); }
.cockpit button:active { transform: translateY(1px); }
.cockpit button:disabled { opacity: .6; cursor: wait; }
.cockpit button:focus-visible, .cockpit a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.cockpit button.primary { color: #081b29; background: var(--accent); border-color: var(--accent); font-weight: 650; }
.cockpit .up { color: var(--red); }
.cockpit .down { color: var(--green); }
.cockpit .warning { color: #f1c77c; }
.cockpit .muted { color: var(--muted); }
.mono, .quote, .clock strong, .countdown, .flow-summary strong, .flow-row strong, dd, time, .chart-tip { font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace; font-variant-numeric: tabular-nums; }
.terminal-head { display: flex; align-items: center; flex-wrap: wrap; gap: 16px 24px; padding-bottom: 15px; border-bottom: 1px solid var(--line); }
.identity { display: flex; align-items: center; gap: 10px; }
.identity h1 { font-size: 22px; letter-spacing: 1px; line-height: 1.3; }
.identity p { color: var(--muted); font-size: 10px; margin-top: 4px; letter-spacing: 1px; }
.terminal-mark { display: grid; place-items: center; border: 1px solid #33516b; color: var(--accent); width: 36px; height: 40px; font-size: 24px; }
.session-block, .clock { display: flex; flex-direction: column; gap: 3px; }
.session { color: #f1c77c; font-weight: 600; }
.session.trading { color: var(--accent); }
.session-block small, .clock small { color: var(--muted); }
.clock strong { font-size: 23px; line-height: 1.2; font-weight: 500; }
.toolbar { margin-left: auto; display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }
.countdown { color: var(--muted); margin-right: 5px; font-size: 11px; }
.feed-strip { display: flex; flex-wrap: wrap; gap: 6px 24px; color: var(--muted); padding: 10px 0; font-size: 11px; }
.feed-strip b { color: #c8dbe9; font-weight: 500; }
.index-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid var(--line); background: var(--surface); }
.index-panel { padding: 12px 14px 9px; min-width: 0; border-bottom: 1px solid var(--line); }
.index-panel:nth-child(odd) { border-right: 1px solid var(--line); }
.index-panel:nth-last-child(-n+2) { border-bottom: 0; }
.index-heading { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.index-heading h2 { font-size: 13px; }
.index-heading h2 small { display: inline-block; color: var(--muted); font-weight: 400; font-size: 10px; margin-left: 4px; }
.data-state { color: var(--accent); font-size: 10px; white-space: nowrap; }
.quote { display: flex; align-items: baseline; flex-wrap: wrap; gap: 12px; margin: 5px 0; }
.quote strong { font-size: 26px; font-weight: 600; letter-spacing: -.5px; }
.quote > span { font-size: 14px; }
.quote small { color: var(--muted); margin-left: auto; }
.chart-wrap { position: relative; height: 155px; }
.chart { width: 100%; height: 100%; overflow: visible; touch-action: pan-y; }
.grid-line { stroke: #20334a; stroke-width: .7; }
.axis-text { fill: #9baec2; font-size: 9px; font-family: Consolas, monospace; }
.baseline { stroke: #8194a7; stroke-dasharray: 4 4; stroke-width: .8; }
.price-line { stroke: var(--accent); stroke-width: 1.7; vector-effect: non-scaling-stroke; }
.price-line.up { stroke: var(--red); }
.price-line.down { stroke: var(--green); }
.crosshair { stroke: var(--accent); stroke-dasharray: 3 3; stroke-width: .8; }
.hover-dot, .single-point { fill: var(--accent); stroke: var(--surface); stroke-width: 1; }
.chart-tip { position: absolute; top: 2px; right: 3px; padding: 4px 7px; background: #162d43; border: 1px solid #42637d; font-size: 10px; pointer-events: none; }
.chart-tip b { margin-left: 7px; }
.chart-empty { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; background: #0b1726; color: #b5c6d6; }
.chart-empty .el-icon { font-size: 25px; color: #5e829e; }
.chart-empty small { color: var(--muted); }
.source-line { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px; color: var(--muted); font-size: 10px; margin-top: 6px; }
.inline-error { color: #f1c77c; font-size: 10px; overflow-wrap: anywhere; margin-top: 4px !important; }
.main-grid { display: grid; grid-template-columns: minmax(0, 1.85fr) minmax(0, 1fr); gap: 12px; margin-top: 12px; }
.panel { min-width: 0; border: 1px solid var(--line); background: var(--surface); }
.panel-heading { display: flex; justify-content: space-between; flex-wrap: wrap; align-items: center; gap: 10px; padding: 13px 15px 12px; border-bottom: 1px solid var(--line); }
.panel-heading p { color: var(--muted); font-size: 11px; margin-top: 3px; }
.unit { margin-left: 5px; color: var(--accent); }
.segmented { display: flex; }
.segmented button { padding: 4px 12px; border-radius: 0; background: transparent; color: #b8cbdc; }
.segmented button + button { border-left: 0; }
.segmented button[aria-pressed='true'] { background: #1b4259; color: #8ce5fa; border-color: #4d90ab; }
.flow-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); padding: 12px 15px; gap: 12px; }
.flow-summary > div { display: flex; flex-direction: column; gap: 3px; }
.flow-summary small { color: var(--muted); }
.flow-summary strong { font-size: 23px; font-weight: 500; }
.flow-summary em { font-size: 14px; color: var(--muted); font-style: normal; }
.flow-legend { display: flex; justify-content: space-between; gap: 8px; font-size: 10px; color: var(--muted); padding: 5px 15px; background: #0a1726; }
.flow-chart { padding: 8px 15px 10px; min-height: 284px; }
.flow-row { display: grid; grid-template-columns: 105px minmax(0, 1fr) 78px; gap: 10px; align-items: center; min-height: 23px; }
.board-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #cad9e7; font-size: 11px; }
.bar-space { position: relative; height: 23px; }
.zero-line { position: absolute; top: 0; bottom: 0; left: 50%; border-left: 1px solid #486179; }
.flow-bar { position: absolute; top: 6px; height: 11px; background: var(--accent); }
.flow-bar.up { background: #ce5969; }
.flow-bar.down { background: #3aa981; }
.flow-row strong { text-align: right; font-size: 11px; font-weight: 500; }
.panel-footer { display: flex; flex-wrap: wrap; gap: 3px 12px; justify-content: space-between; color: var(--muted); border-top: 1px solid var(--line); padding: 8px 15px; font-size: 10px; overflow-wrap: anywhere; }
.method-note { color: var(--muted); font-size: 10px; line-height: 1.7; }
.flow-panel > .method-note { padding: 0 15px 9px; }
.news-panel { display: flex; flex-direction: column; }
.news-list { padding: 0 15px; height: 402px; overflow-y: auto; scrollbar-width: thin; scrollbar-color: #3a536c #0e1c2d; }
.news-item { padding: 12px 0 10px 13px; position: relative; border-left: 1px solid #324a61; margin-left: 3px; }
.news-item::before { content: ''; width: 5px; height: 5px; position: absolute; top: 18px; left: -3px; background: #7e9ab2; border-radius: 50%; }
.news-item.important::before { background: #f1c77c; }
.news-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 5px 9px; font-size: 10px; color: var(--muted); margin-bottom: 5px; }
.important-flag { color: #f1c77c; background: #3a3020; padding: 0 4px; font-size: 10px; }
.news-item h3 { font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; }
.news-content { color: #b0c1d2; font-size: 11px; line-height: 1.75; margin-top: 4px !important; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; white-space: pre-wrap; overflow-wrap: anywhere; }
.news-content.expanded { display: block; }
.news-actions { display: flex; gap: 15px; align-items: center; margin-top: 6px; font-size: 10px; }
.news-actions button { color: var(--accent); border: 0; background: transparent; padding: 2px 0; font-size: 10px; }
.news-panel .panel-footer { margin-top: auto; }
.empty-state { min-height: 280px; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 8px; padding: 25px; color: var(--muted); }
.empty-state .el-icon { font-size: 30px; color: #6388a7; }
.empty-state h3 { color: #bfd0df; font-weight: 500; }
.empty-state p { max-width: 320px; font-size: 11px; line-height: 1.8; }
.flow-empty { min-height: 284px; }
.strategy-section { margin-top: 17px; }
.strategy-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 7px 16px; padding: 0 0 8px; }
.strategy-heading h2 { font-size: 14px; }
.strategy-heading p { color: var(--muted); font-size: 10px; }
.strategy-heading p span { margin-left: 12px; }
.strategy-grid { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1fr); border: 1px solid var(--line); background: #0c1929; }
.strategy-cell { padding: 12px 14px; border-right: 1px solid var(--line); min-width: 0; }
.strategy-cell:nth-child(3) { border-right: 0; }
.strategy-cell h3 { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 10px; }
.strategy-cell h3 a, .strategy-cell h3 > span { font-size: 10px; font-weight: 400; }
.strategy-cell p { font-size: 11px; margin-top: 7px; }
.strategy-value { font-size: 22px; font-weight: 600; line-height: 1.4; }
.strategy-value small { font-weight: 400; color: var(--muted); margin-left: 8px; }
.position { display: flex; align-items: baseline; flex-wrap: wrap; gap: 5px 10px; margin-top: 5px; color: var(--muted); font-size: 11px; }
.position b { color: var(--accent); font-family: Consolas, monospace; font-size: 16px; }
.market-stats { margin: 9px 0 0; display: grid; grid-template-columns: 1fr 1fr; gap: 4px 15px; }
.market-stats > div { display: flex; justify-content: space-between; gap: 8px; font-size: 10px; }
.market-stats dt { color: var(--muted); }
.market-stats dd { margin: 0; }
.tags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 12px; }
.tags span { color: var(--red); border: 1px solid #673746; background: #2d202e; padding: 2px 7px; font-size: 11px; }
.compact-empty { color: var(--muted); padding: 12px 0; line-height: 1.9; }
.pool-excess { padding-top: 9px; display: flex; flex-wrap: wrap; gap: 8px; color: var(--muted); font-size: 11px; }
.signals-cell { grid-column: span 2; border-top: 1px solid var(--line); }
.commodities-cell { border-top: 1px solid var(--line); border-right: 0; }
.signal-row { display: grid; grid-template-columns: 68px minmax(0, 1fr) 85px 40px; gap: 10px; align-items: center; min-height: 24px; font-size: 11px; }
.signal-row > span:nth-child(2) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.signal-row b { text-align: right; font-weight: 400; }
.signal-tag { color: #a1d7eb; font-size: 10px; }
.commodity-row { display: grid; grid-template-columns: 1fr 45px 65px; gap: 4px 7px; align-items: baseline; padding: 3px 0; font-size: 11px; }
.commodity-row b { text-align: right; font-weight: 400; }
.commodity-row small { grid-column: 1 / -1; color: var(--muted); font-size: 10px; }
.terminal-footer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 5px 15px; color: var(--muted); font-size: 10px; padding-top: 10px; }
.notice { color: #f1c77c; padding: 8px 10px; border: 1px solid #705b36; margin-top: 8px !important; background: #30291d; }
@media (min-width: 1500px) {
  .index-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .index-panel, .index-panel:nth-child(odd) { border-bottom: 0; border-right: 1px solid var(--line); }
  .index-panel:last-child { border-right: 0; }
  .strategy-grid { grid-template-columns: minmax(0, 1.4fr) minmax(0, .9fr) minmax(0, .9fr) minmax(0, 1.2fr) minmax(0, 1.1fr); }
  .strategy-cell:nth-child(3) { border-right: 1px solid var(--line); }
  .signals-cell { grid-column: auto; border-top: 0; }
  .commodities-cell { border-top: 0; }
  .signal-row { grid-template-columns: 55px minmax(0, 1fr) 60px 25px; gap: 5px; font-size: 10px; }
}
@media (max-width: 1100px) {
  .main-grid { grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); }
  .toolbar { margin-left: 0; width: 100%; }
  .flow-summary strong { font-size: 19px; }
  .flow-row { grid-template-columns: 78px minmax(0, 1fr) 66px; gap: 6px; }
  .flow-legend > span:nth-child(2) { max-width: 140px; text-align: center; }
}
@media (max-width: 768px) {
  .cockpit { padding: 12px; width: 100%; }
  .terminal-head { gap: 12px 18px; }
  .identity { width: 100%; }
  .identity h1 { font-size: 20px; }
  .clock { margin-left: auto; }
  .clock strong { font-size: 21px; }
  .toolbar { gap: 7px; }
  .countdown { width: 100%; }
  .cockpit button { padding: 7px 9px; font-size: 11px; }
  .index-grid, .main-grid, .strategy-grid { grid-template-columns: minmax(0, 1fr); }
  .index-panel, .index-panel:nth-child(odd), .index-panel:nth-last-child(-n+2) { border-right: 0; border-bottom: 1px solid var(--line); }
  .index-panel:last-child { border-bottom: 0; }
  .index-panel { padding: 12px; }
  .main-grid { gap: 12px; }
  .panel-heading { padding: 12px; }
  .flow-summary { padding: 12px; gap: 7px; }
  .flow-summary strong { font-size: 18px; }
  .flow-summary small { font-size: 10px; }
  .flow-chart { padding: 8px 12px; }
  .flow-row { grid-template-columns: 76px minmax(0, 1fr) 62px; gap: 6px; }
  .flow-legend { padding: 5px 12px; font-size: 9px; }
  .flow-legend > span:nth-child(2) { max-width: 150px; }
  .news-list { height: 420px; padding: 0 12px; }
  .strategy-cell, .strategy-cell:nth-child(3) { border-right: 0; border-top: 1px solid var(--line); }
  .strategy-cell:first-child { border-top: 0; }
  .signals-cell { grid-column: auto; }
  .signal-row { grid-template-columns: 57px minmax(0, 1fr) 65px 32px; gap: 6px; }
  .panel-footer { padding: 8px 12px; }
}
@media (prefers-reduced-motion: no-preference) { .skeleton { animation: waiting 2s ease-in-out infinite; } @keyframes waiting { 50% { opacity: .65; } } }
@media (prefers-reduced-motion: reduce) { .cockpit *, .cockpit *::before { animation: none !important; transition: none !important; scroll-behavior: auto !important; } }
</style>
