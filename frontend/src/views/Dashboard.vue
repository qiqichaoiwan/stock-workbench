<template>
  <div ref="root" class="dashboard-shell">
    <header class="dashboard-topbar">
      <div class="brand-block">
        <span class="brand-mark"><el-icon><DataAnalysis /></el-icon></span>
        <div><h1>市场总览</h1><p>实时行情 · 资金方向 · 风险工作台</p></div>
      </div>
      <div class="topbar-context">
        <span class="context-date"><el-icon><Calendar /></el-icon>{{ market.server_time ? dateText(market.server_time) : '时间待确认' }}</span>
        <span class="session-pill" :class="{ trading: marketOpen && !marketError }"><i class="status-dot" />{{ marketError ? '行情连接异常' : session.state === 'trading' && !marketOpen ? '交易状态待更新' : session.label || '交易状态待确认' }}</span>
        <span class="context-time">{{ clockText }}</span>
      </div>
      <div class="topbar-actions">
        <span class="refresh-hint">{{ pauseLabel || ('行情 ' + countdown('market') + 's · 新闻 ' + countdown('news') + 's') }}</span>
        <button class="toolbar-button" :aria-pressed="paused" @click="togglePause"><el-icon><component :is="paused ? VideoPlay : VideoPause" /></el-icon>{{ paused ? '恢复刷新' : '暂停刷新' }}</button>
        <button class="toolbar-button primary" :disabled="busy" @click="refreshAll"><el-icon><Refresh /></el-icon>{{ busy ? '读取中' : '刷新' }}</button>
        <button class="toolbar-button icon-only" :aria-label="fullscreen ? '退出全屏' : '进入全屏'" @click="toggleFullscreen"><el-icon><FullScreen /></el-icon></button>
      </div>
    </header>
    <p v-if="fullscreenError" class="dashboard-notice" role="alert">{{ fullscreenError }}</p>

    <section class="market-status-banner" :class="{ degraded: marketError, closed: !marketOpen && !marketError }">
      <span class="banner-icon"><el-icon><component :is="marketError ? WarningFilled : marketOpen ? CircleCheck : DataAnalysis" /></el-icon></span>
      <div class="banner-copy"><strong>{{ bannerTitle }}</strong><span>{{ bannerCopy }}</span></div>
      <span class="quality-chip" :class="{ degraded: !session.calendar_verified || marketError }">{{ session.calendar_verified ? '交易日历已核验' : '交易日历待核验' }}</span>
    </section>

    <section class="index-grid" aria-label="指数行情">
      <article v-for="ix in charts" :key="ix.code" class="index-card">
        <div class="index-card-head">
          <div><h2>{{ ix.name }} <small>{{ ix.code.toUpperCase() }}</small></h2><span class="index-source">{{ ix.source || '来源待确认' }}</span></div>
          <span class="data-badge" :class="dataBadgeClass(indexStatus(ix))"><i />{{ statusLabel(indexStatus(ix)) }}</span>
        </div>
        <div class="index-quote"><strong :class="tone(ix.pct_chg)">{{ num(ix.last) }}</strong><span :class="tone(ix.pct_chg)">{{ signed(ix.pct_chg, '%') }}</span></div>
        <div class="index-chart-mini">
          <svg v-if="ix.plot.length" class="chart" viewBox="0 0 400 164" preserveAspectRatio="none" role="img" :aria-label="ix.name + '一分钟分时，' + ix.plot.length + '个采样点'" @pointermove="hoverChart($event, ix)" @pointerleave="hover = null">
            <line v-for="y in [16, 70, 124]" :key="y" x1="8" x2="392" :y1="y" :y2="y" class="grid-line" />
            <path :d="ix.path" fill="none" :class="['price-line', tone(ix.pct_chg)]" />
            <circle v-if="ix.plot.length === 1" :cx="ix.plot[0].x" :cy="ix.plot[0].y" r="3" class="point-marker" />
          </svg>
          <div v-else class="chart-empty" :class="{ skeleton: ix.status === 'loading' && !marketError }"><el-icon><TrendCharts /></el-icon><span>{{ ix.status === 'loading' && !marketError ? '等待行情采集' : '暂无行情数据' }}</span></div>
          <div v-if="hover?.code === ix.code" class="chart-tip" role="status">{{ dateText(hover.point.time) }} <b>{{ num(hover.point.value) }}</b></div>
        </div>
        <div class="index-card-foot"><span>昨收 {{ num(ix.prev_close) }}</span><time>{{ dateText(ix.data_time) }}</time></div>
      </article>
    </section>

    <section class="workspace-grid">
      <section class="workspace-main">
        <article class="card focus-card">
          <header class="card-header"><div><span class="eyebrow">MARKET FOCUS</span><h2>{{ primaryIndex.name || '市场焦点' }} <small>{{ primaryIndex.code?.toUpperCase() || '' }}</small></h2></div><router-link to="/market" class="text-link">查看大盘详情 <el-icon><ArrowRight /></el-icon></router-link></header>
          <div class="focus-content">
            <div class="focus-quote"><strong :class="tone(primaryIndex.pct_chg)">{{ num(primaryIndex.last) }}</strong><span :class="tone(primaryIndex.pct_chg)">{{ signed(primaryIndex.pct_chg, '%') }}</span><small>来源：{{ primaryIndex.source || '待确认' }} · {{ dateText(primaryIndex.data_time) }}</small></div>
            <div class="focus-chart"><svg v-if="primaryIndex.plot?.length" class="chart focus-svg" viewBox="0 0 640 190" preserveAspectRatio="none" role="img" :aria-label="(primaryIndex.name || '指数') + '分时走势'"><line v-for="y in [18, 72, 126, 180]" :key="y" x1="14" x2="626" :y1="y" :y2="y" class="grid-line" /><path :d="focusPath" fill="none" :class="['price-line focus-line', tone(primaryIndex.pct_chg)]" /><circle v-if="primaryIndex.plot.length === 1" :cx="14 + (primaryIndex.plot[0].x / 400) * 612" :cy="8 + (primaryIndex.plot[0].y / 164) * 172" r="3" class="point-marker" /></svg><div v-else class="chart-empty focus-empty"><el-icon><TrendCharts /></el-icon><span>暂无分时数据</span><small>不使用日线或模拟走势代替</small></div></div>
          </div>
          <div class="focus-stats"><div><span>昨收</span><b>{{ num(primaryIndex.prev_close) }}</b></div><div><span>分时最高</span><b>{{ num(primaryIndex.minuteHigh) }}</b></div><div><span>分时最低</span><b>{{ num(primaryIndex.minuteLow) }}</b></div><div><span>行情时间</span><b>{{ dateText(primaryIndex.data_time) }}</b></div></div>
        </article>

        <article class="card watchlist-card">
          <header class="card-header"><div><span class="eyebrow">WATCHLIST</span><h2>自选观察 <small>{{ watchlist.items.length }} 只</small></h2></div><router-link to="/watchlist" class="text-link">管理自选 <el-icon><ArrowRight /></el-icon></router-link></header>
          <div v-if="watchlist.items.length" class="watchlist-table"><div class="watchlist-row table-head"><span>股票</span><span>现价 / 行情状态</span><span>涨跌</span></div><div v-for="item in watchlist.items.slice(0, 6)" :key="item.id || item.code" class="watchlist-row"><span class="stock-cell"><el-icon><Star /></el-icon><b>{{ item.code }}</b><small>{{ cleanName(item.name) || '名称待确认' }}</small></span><span class="quote-cell"><b class="mono">{{ num(watchQuote(item).price) }}</b><small class="row-status" :class="{ muted: !watchQuote(item).realtime }" :title="quoteLabel(watchQuote(item), watchlistQuotes.status)"><i class="status-dot" :class="{ ok: watchQuote(item).realtime }" />{{ shortQuoteLabel(watchQuote(item), watchlistQuotes.status) }}</small></span><span :class="tone(watchQuote(item).pct_chg)">{{ signed(watchQuote(item).pct_chg, '%') }}</span></div><p v-if="watchlist.status !== 'ok' || watchlistQuotes.status === 'stale' || watchlistQuotes.errors.length" class="data-note">{{ watchlist.status !== 'ok' ? '自选列表刷新失败，显示上次结果。' : '部分行情读取失败，状态与时间以各股为准。' }}</p></div>
          <div v-else class="card-empty"><el-icon><Star /></el-icon><strong>{{ watchlist.status === 'loading' ? '正在读取自选' : watchlist.status !== 'ok' ? '自选读取失败' : '暂无自选股票' }}</strong><span>{{ watchlist.status === 'loading' ? '请稍候。' : watchlist.status !== 'ok' ? '点击刷新重试。' : '添加自选后，行情会显示在这里。' }}</span><router-link to="/watchlist">去管理自选</router-link></div>
        </article>

        <article class="card funds-card">
          <header class="card-header"><div><span class="eyebrow">CAPITAL FLOW</span><h2>板块资金方向 <small>今日累计 · 亿元</small></h2></div><div class="segmented light" aria-label="板块类型"><button v-for="tab in flowTabs" :key="tab.key" :aria-pressed="flowTab === tab.key" @click="flowTab = tab.key">{{ tab.label }}</button></div></header>
          <div class="flow-summary"><div><span>净流入板块合计</span><b class="up">{{ flowRows.length ? signed(flowStats.inflow) : '-' }}</b></div><div><span>净流出板块合计</span><b class="down">{{ flowRows.length ? signed(flowStats.outflow) : '-' }}</b></div><div><span>板块截面</span><b>{{ flowRows.length || '-' }} <em>/ {{ flowRows.length ? flowStats.positive : '-' }} 正向</em></b></div></div>
          <div v-if="rankedBoards.length" class="flow-chart light-flow" role="img" aria-label="板块主力资金排行"><div v-for="(board, i) in rankedBoards" :key="board.name + '-' + i" class="flow-row"><span class="board-name" :title="board.name">{{ board.name }}</span><div class="bar-space"><span class="zero-line" /><span :class="['flow-bar', tone(board.net_inflow)]" :style="barStyle(board.net_inflow)" /></div><strong :class="tone(board.net_inflow)">{{ signed(board.net_inflow) }}</strong></div></div>
          <div v-else class="card-empty flow-empty"><el-icon><DataLine /></el-icon><strong>{{ flow.status === 'loading' ? '等待资金截面' : '暂无资金数据' }}</strong><span>仅展示接口返回板块，不推断资金转移。</span></div>
          <p class="data-note flow-note">合计仅覆盖接口返回板块，行业与概念可能重叠，不等同全市场资金总额。</p>
          <footer class="card-footer"><span :class="{ degraded: flow.status !== 'ok' || marketError || !flow.data_time }">{{ marketError ? (flowRows.length ? '获取失败，保留上次截面' : '获取失败') : flow.status === 'ok' && !flow.data_time ? '时点未核验' : statusLabel(flow.status) }}</span><span>{{ flow.source || '来源待确认' }} · {{ dateText(flow.data_time) }}</span></footer>
        </article>
      </section>

      <aside class="workspace-aside">
        <article class="card alerts-card">
          <header class="card-header compact"><div><span class="eyebrow">ATTENTION</span><h2>最近预警 <em>{{ alerts.items.length }}</em></h2></div><router-link to="/watchlist" class="text-link">全部 <el-icon><ArrowRight /></el-icon></router-link></header>
          <ul class="alert-list">
            <li v-for="item in alerts.items.slice(0, 4)" :key="item.id || item.code + item.rule" class="alert-item warning"><span class="alert-icon"><el-icon><WarningFilled /></el-icon></span><div><b>{{ item.code || '行情' }} {{ item.rule || '阈值提醒' }}</b><small>{{ dateText(item.created_at) }} · {{ item.message || '请查看自选预警详情' }}</small></div><router-link to="/watchlist">查看</router-link></li>
            <li v-if="marketError" class="alert-item danger"><span class="alert-icon"><el-icon><WarningFilled /></el-icon></span><div><b>行情连接异常</b><small>{{ market.indexes?.some(ix => ix.points?.length) ? '保留上次行情，请检查数据源' : '暂无可用行情，请检查数据源' }}</small></div><button @click="refreshAll">重试</button></li>
            <li v-if="newsError || strategyError" class="alert-item warning"><span class="alert-icon"><el-icon><WarningFilled /></el-icon></span><div><b>部分信息读取失败</b><small>{{ newsError ? '新闻' : '' }}{{ newsError && strategyError ? '、' : '' }}{{ strategyError ? '策略' : '' }}读取异常，请查看数据时间</small></div><button @click="refreshAll">重试</button></li>
            <li v-if="d.risk_guard === true" class="alert-item warning"><span class="alert-icon"><el-icon><WarningFilled /></el-icon></span><div><b>市场风险收紧</b><small>策略风控已生效，建议查看仓位</small></div><router-link to="/market">查看</router-link></li>
            <li v-if="!alerts.items.length && alerts.status === 'ok' && !marketError && !newsError && !strategyError && d.risk_guard !== true" class="alert-item success"><span class="alert-icon"><el-icon><CircleCheck /></el-icon></span><div><b>暂无预警记录</b><small>记录按触发时间展示</small></div></li>
            <li v-if="alerts.status === 'loading'" class="alert-item"><span class="alert-icon"><el-icon><Document /></el-icon></span><div><b>正在读取预警</b><small>请稍候</small></div></li>
            <li v-if="alerts.status === 'stale'" class="alert-item warning"><span class="alert-icon"><el-icon><Document /></el-icon></span><div><b>预警读取失败</b><small>{{ alerts.items.length ? '保留上次结果，请稍后重试' : '未能读取记录，请稍后重试' }}</small></div></li>
          </ul>
        </article>

        <article class="card quality-card">
          <header class="card-header compact"><div><span class="eyebrow">DATA QUALITY</span><h2>数据新鲜度</h2></div><router-link to="/help" class="text-link">口径说明 <el-icon><ArrowRight /></el-icon></router-link></header>
          <div class="quality-list"><div v-for="item in qualityItems" :key="item.label" class="quality-row"><span class="quality-state" :class="item.state"><i /></span><span class="quality-label">{{ item.label }}</span><b>{{ item.value }}</b><small>{{ item.time }}</small></div></div>
          <div class="quality-note"><el-icon><DataAnalysis /></el-icon><span>实时、延迟、收盘快照和不可用状态分开显示。</span></div>
        </article>

        <article class="card risk-card">
          <header class="card-header compact"><div><span class="eyebrow">PORTFOLIO RISK</span><h2>持仓风险</h2></div><router-link to="/portfolio" class="text-link">持仓明细 <el-icon><ArrowRight /></el-icon></router-link></header>
          <div v-if="holdings.items.length" class="risk-main"><div class="risk-ring" :class="{ guarded: holdingRisk.triggered > 0 }"><strong>{{ holdingRisk.triggered }}</strong><small>触线参考</small></div><div class="risk-copy"><b>{{ holdings.items.length }} 只持仓</b><span>{{ holdingRisk.missing ? holdingRisk.missing + ' 只行情或止损线缺失' : holdingRisk.triggered ? '参考价格触及止损线' : '参考价格未触及止损线' }}</span><small>组合报价：{{ portfolioQuoteStatus }}</small></div></div>
          <div v-else class="card-empty compact-risk"><el-icon><DataAnalysis /></el-icon><strong>{{ holdings.status === 'loading' ? '正在读取持仓' : holdings.status !== 'ok' ? '持仓读取失败' : '暂无持仓明细' }}</strong><span>{{ holdings.status === 'loading' ? '请稍候。' : holdings.status !== 'ok' ? '数据源异常，未推断风险。' : '录入持仓后显示组合风险。' }}</span></div>
          <div class="risk-foot"><span>止损线需结合各股行情时间</span><router-link to="/portfolio">打开持仓 <el-icon><ArrowRight /></el-icon></router-link></div>
        </article>
      </aside>
    </section>

    <section class="lower-grid">
      <article class="card news-card">
        <header class="card-header"><div><span class="eyebrow">EVENTS</span><h2>市场事件 <small>{{ filteredNews.length }} 条</small></h2></div><div class="segmented light" aria-label="新闻筛选"><button :aria-pressed="importantOnly" @click="importantOnly = true">重点</button><button :aria-pressed="!importantOnly" @click="importantOnly = false">全部</button></div></header>
        <div class="news-list light-news"><article v-for="item in filteredNews" :key="item.id" class="news-item"><div class="news-meta"><time>{{ dateText(item.published_at) }}</time><span v-if="item.important" class="important-flag">重要</span><span>{{ item.source || '来源未注明' }}</span></div><h3>{{ item.title }}</h3><p class="news-content" :class="{ expanded: expanded.has(item.id) }">{{ item.content || '暂无正文，请查看原文。' }}</p><div class="news-actions"><button :aria-expanded="expanded.has(item.id)" @click="toggleNews(item.id)">{{ expanded.has(item.id) ? '收起全文' : '展开全文' }}</button><a v-if="safeUrl(item.url)" :href="safeUrl(item.url)" target="_blank" rel="noopener noreferrer">原文 <el-icon><TopRight /></el-icon></a></div></article><div v-if="!filteredNews.length" class="card-empty"><el-icon><Document /></el-icon><strong>{{ newsError ? '新闻刷新失败' : importantOnly && news.items.length ? '暂无重点新闻' : '暂无可用新闻' }}</strong><span>{{ newsError ? '点击刷新重试。' : importantOnly && news.items.length ? '切换“全部”查看其他新闻。' : '采集后显示事件时间与来源。' }}</span></div></div>
        <footer class="card-footer"><span :class="{ degraded: newsError || news.status !== 'ok' }">{{ newsError ? (news.items.length ? '刷新失败，显示上次新闻' : '新闻读取失败') : statusLabel(news.status) }}</span><span>{{ news.source || '来源待确认' }} · {{ dateText(news.data_time) }}</span></footer>
      </article>

      <article class="card strategy-card">
        <header class="card-header"><div><span class="eyebrow">STRATEGY</span><h2>策略观察 <small>日级数据</small></h2></div><router-link to="/signals" class="text-link">技术信号 <el-icon><ArrowRight /></el-icon></router-link></header>
        <div class="strategy-metrics"><div><span>市场状态</span><b>{{ d.market_state?.trend_label || '-' }}</b><small>{{ d.market_state?.cycle_stage || '待确认' }}</small></div><div><span>建议仓位</span><b class="accent">{{ d.market_state?.position_range || '-' }}</b><small>PM02 草案</small></div><div><span>选股池超额</span><b :class="tone(d.pool_perf?.avg_excess_pct)">{{ d.pool_perf?.trade_date ? signed(d.pool_perf.avg_excess_pct, '%') : '-' }}</b><small>{{ d.pool_perf?.trade_date || '待确认' }}</small></div></div>
        <div v-if="d.market_state" class="strategy-details"><span>市场宽度 <b>{{ finite(d.market_state.breadth) ? num(d.market_state.breadth, 1) + '%' : '-' }}</b></span><span>成交额分位 <b>{{ finite(d.market_state.amount_pct_60d) ? num(d.market_state.amount_pct_60d, 1) + '%' : '-' }}</b></span><span>估值分位 <b>{{ finite(d.market_state.valuation_pct) ? num(d.market_state.valuation_pct, 1) + '%' : '-' }}</b></span><span>融资/成交额 <b>{{ num(d.market_state.margin_ratio, 4) }}</b></span></div>
        <p v-if="d.market_wide_crowded" class="card-note warning-note">全市场拥挤度偏高，策略建议整体降仓（规则待验证）。</p>
        <div class="strategy-tags" v-if="d.crowd_avoid?.length"><span v-for="name in d.crowd_avoid.slice(0, 6)" :key="name">{{ name }}</span></div><p v-else class="card-note">{{ strategyError ? (strategyLoaded ? '策略数据陈旧，保留上次结果。' : '策略读取失败。') : strategyLoaded ? '当前无拥挤行业' : '等待策略快照' }}</p>
        <div class="strategy-lists"><section><h3>技术信号 <small>{{ signalDate || '日期待确认' }}</small></h3><div v-for="(signal, i) in (d.signals || []).slice(0, 5)" :key="`${signal.code}-${signal.type}-${i}`" class="strategy-list-row"><span>{{ cleanName(signal.name) || signal.code || '-' }}</span><span>{{ typeLabel(signal.type) }}</span><b>{{ num(signal.strength, 1) }}</b></div><p v-if="!d.signals?.length">暂无技术信号</p></section><section><h3>商品涨价信号 <small>20日动量</small></h3><div v-for="(item, i) in commodities" :key="`${item.series_name}-${i}`" class="strategy-list-row"><span>{{ item.series_name }}</span><span>{{ item.signal || '-' }}</span><b :class="tone(item.mom_20)">{{ signed(item.mom_20, '%') }}</b></div><p v-if="!commodities.length">暂无商品信号</p></section></div>
        <footer class="card-footer"><span :class="{ degraded: strategyError }">{{ strategyError ? (strategyLoaded ? '策略数据陈旧' : '策略读取失败') : strategyLoaded ? '最近读取 ' + dateText(strategyReadAt) : '读取中' }}</span><router-link to="/tasks">任务中心 <el-icon><ArrowRight /></el-icon></router-link></footer>
      </article>
    </section>

    <footer class="dashboard-footer"><span>数据缺失以“—”表示；陈旧数据不作为即时行情。</span><span>行情刷新：{{ marketOpen ? '交易时每30秒' : '非交易时每60秒更新状态' }}</span><router-link to="/help">查看数据口径 <el-icon><ArrowRight /></el-icon></router-link></footer>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { DataAnalysis, DataLine, TrendCharts, Document, Refresh, FullScreen, VideoPause, VideoPlay, TopRight, Calendar, CircleCheck, WarningFilled, ArrowRight, Star } from '@element-plus/icons-vue'
import api from '../api'
import { cleanName } from '../utils/fmt'
import { effectiveQuote, portfolioQuoteLabel } from '../utils/portfolioQuotes'
import { isTradingSession } from '../utils/poolQuotes'

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
const deadlines = ref({ market: 0, news: 0, strategy: 0, extras: 0 })
const retries = { market: 0, news: 0 }
const controllers = {}
const sequences = { market: 0, news: 0, strategy: 0 }
// Secondary feeds are independent of the cockpit API. Keep the last successful
// payload when one of them is unavailable so the dashboard never fabricates data.
const watchlist = ref({ items: [], status: 'loading' })
const alerts = ref({ items: [], status: 'loading' })
const holdings = ref({ items: [], status: 'loading' })
const portfolioQuotes = ref({ as_of: null, items: {}, errors: [], status: 'loading' })
const watchlistQuotes = ref({ as_of: null, items: {}, errors: [], status: 'loading' })
const extrasBusy = ref(false)
let extrasSeq = 0
let extrasController
let disposed = false
let ticker
const indexDefinitions = [{ code: 'sh000001', name: '上证指数' }, { code: 'sz399001', name: '深证成指' }, { code: 'sz399006', name: '创业板指' }, { code: 'sh000688', name: '科创50' }]
const session = computed(() => market.value.session || {})
const marketOpen = computed(() => {
  const current = new Date(now.value)
  const clock = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hourCycle: 'h23', hour: '2-digit', minute: '2-digit' }).format(current)
  // The shared helper includes the ending minute; the session API closes at the boundary.
  return session.value.state === 'trading' && isTradingSession(current) && clock !== '11:30' && clock !== '15:00'
})
const busy = computed(() => Object.values(inFlight.value).some(Boolean) || extrasBusy.value)
const pauseLabel = computed(() => hidden.value ? '页面隐藏，自动刷新暂停' : paused.value ? '自动刷新已暂停' : '')
const clockText = computed(() => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(now.value))
const finite = (v) => typeof v === 'number' && Number.isFinite(v)
function num(v, digits = 2) { return finite(v) ? v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '-' }
function signed(v, suffix = '') { return finite(v) ? `${v > 0 ? '+' : ''}${num(v)}${suffix}` : '-' }
function tone(v) { return finite(v) && v !== 0 ? v > 0 ? 'up' : 'down' : '' }
function statusLabel(status) { return ({ ok: '已更新', closed: '收盘快照', snapshot: '休市快照', stale: '陈旧数据', loading: '采集中', unavailable: '数据不可用' })[status] || '等待数据' }
function dataBadgeClass(status) {
  if (status === 'stale' || status === 'unavailable' || status === 'error') return 'degraded'
  if (status === 'loading') return 'loading'
  if (status === 'closed' || status === 'snapshot') return 'snapshot'
  return 'ok'
}
function sourceTime(value) {
  if (!value) return NaN
  const str = String(value)
  return Date.parse(/Z$|[+-]\d{2}:?\d{2}$/.test(str) ? str : `${str.replace(' ', 'T')}+08:00`)
}
function dateText(value) {
  if (!value) return '-'
  const stamp = sourceTime(value)
  if (!Number.isFinite(stamp)) return '-'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(stamp)
}
function safeUrl(value) { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : '' } catch { return '' } }
function countdown(key) { return Math.max(0, Math.ceil((deadlines.value[key] - now.value) / 1000)) }
function interval(key) { return (key === 'market' || key === 'extras') && marketOpen.value ? 30000 : 60000 }

function tradingMinute(time) {
  const match = String(time || '').match(/\s(\d{2}):(\d{2}):\d{2}$/)
  if (!match) return null
  const minute = Number(match[1]) * 60 + Number(match[2])
  if (minute >= 570 && minute < 690) return minute - 570
  if (minute >= 780 && minute < 900) return minute - 660
  return null
}
const charts = computed(() => indexDefinitions.map((definition) => {
  const ix = market.value.indexes?.find((item) => item.code === definition.code) || { ...definition, status: 'loading' }
  const points = (ix.points || []).filter((p) => finite(p.value) && tradingMinute(p.time) !== null).slice().sort((a, b) => a.time.localeCompare(b.time))
  const values = points.map((p) => p.value)
  const minuteLow = values.length ? Math.min(...values) : null
  const minuteHigh = values.length ? Math.max(...values) : null
  if (finite(ix.prev_close)) values.push(ix.prev_close)
  const min = values.length ? Math.min(...values) : 0
  const max = values.length ? Math.max(...values) : 0
  const padding = Math.max((max - min) * 0.12, Math.abs(max) * 0.0003, 0.01)
  const low = min - padding
  const high = max + padding
  const y = (value) => 124 - (value - low) / (high - low) * 108
  const plot = points.map((p) => ({ ...p, x: 8 + tradingMinute(p.time) / 240 * 384, y: y(p.value) }))
  const path = plot.map((p, i) => `${!i || tradingMinute(p.time) - tradingMinute(plot[i - 1].time) > 2 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ')
  return { ...definition, ...ix, plot, path, minuteHigh, minuteLow }
}))
function indexStatus(ix) {
  if (marketError.value) return ix.plot.length ? 'stale' : 'unavailable'
  if (ix.status !== 'ok') return ix.status || 'loading'
  if (!ix.plot.length) return 'unavailable'
  if (marketOpen.value) {
    const age = now.value - sourceTime(ix.data_time)
    return Number.isFinite(age) && age >= 0 && age <= 180000 ? 'ok' : 'stale'
  }
  return 'snapshot'
}
const primaryIndex = computed(() => charts.value[0] || {})
const focusPath = computed(() => {
  const plot = primaryIndex.value.plot || []
  return plot.map((point, index) => {
    const x = 14 + (point.x / 400) * 612
    const y = 8 + (point.y / 164) * 172
    const previous = plot[index - 1]
    const gap = previous && tradingMinute(point.time) - tradingMinute(previous.time) > 2
    return (!index || gap ? 'M' : 'L') + x.toFixed(2) + ',' + y.toFixed(2)
  }).join(' ')
})
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

function quoteFor(item, bucket) {
  const fallback = { price: item.close, pct_chg: item.pct_chg, quote_at: item.quote_at, quote_kind: item.close == null ? 'missing' : 'stored' }
  const quote = bucket.items[item.code] || fallback
  // A previous response or a paused tab must never extend a realtime claim.
  const canBeRealtime = bucket.status === 'ok' && session.value.calendar_verified && marketOpen.value
  return effectiveQuote(canBeRealtime ? quote : { ...quote, realtime: false }, now.value)
}
function watchQuote(item) { return quoteFor(item, watchlistQuotes.value) }
function holdingQuote(item) {
  const quote = quoteFor(item, portfolioQuotes.value)
  const stamp = String(quote.quote_at || '').slice(0, 10)
  // Quotes predating the position cannot establish whether its stop was touched.
  if (!/^\d{4}-\d{2}-\d{2}$/.test(stamp) || !item.buy_date || stamp < item.buy_date) {
    return { ...quote, price: null, quote_kind: 'missing', realtime: false }
  }
  return quote
}
function quoteLabel(quote, status) { return `${status === 'stale' ? '刷新失败 · ' : ''}${portfolioQuoteLabel(quote)}` }
function shortQuoteLabel(quote, status) {
  if (quote.quote_kind === 'missing' || !finite(quote.price)) return '暂无'
  const stamp = quote.quote_at ? (String(quote.quote_at).length === 10 ? String(quote.quote_at).slice(5) : dateText(quote.quote_at)) : '时点未知'
  if (status === 'stale') return `上次 ${stamp}`
  return `${quote.quote_kind === 'stored' ? '收盘' : quote.realtime ? '实时' : '非实时'} ${stamp}`
}
const holdingRisk = computed(() => holdings.value.items.reduce((result, item) => {
  const quote = holdingQuote(item)
  if (!finite(quote.price) || !finite(item.stop_price) || item.stop_price <= 0) result.missing++
  else if (quote.price <= item.stop_price) result.triggered++
  return result
}, { triggered: 0, missing: 0 }))
const portfolioQuoteStatus = computed(() => {
  if (holdings.value.status === 'loading' || portfolioQuotes.value.status === 'loading') return '读取中'
  if (holdings.value.status !== 'ok' || portfolioQuotes.value.status === 'stale') return '刷新失败，显示上次结果'
  if (!holdings.value.items.length) return '无持仓'
  const quotes = holdings.value.items.map(holdingQuote)
  const realtime = quotes.filter((q) => q.realtime).length
  const missing = quotes.filter((q) => !finite(q.price)).length
  return `${portfolioQuotes.value.errors.length ? '来源降级 · ' : ''}${realtime} 只实时 · ${quotes.length - realtime - missing} 只非实时 · ${missing} 只缺失`
})
const bannerTitle = computed(() => {
  if (marketError.value) return market.value.indexes?.some((ix) => ix.points?.length) ? '行情连接异常，显示上次行情' : '行情连接异常，暂无可用行情'
  if (marketOpen.value) {
    const statuses = charts.value.map(indexStatus)
    if (statuses.every((status) => status === 'loading')) return '行情正在采集中'
    if (statuses.some((status) => status !== 'ok')) return '部分指数行情暂不可用或已陈旧'
    return '指数分时更新中'
  }
  if (session.value.state === 'trading') return '交易时段已结束 · 等待交易状态更新'
  if (session.value.state === 'closed') return '已收盘 · 行情状态见各指数'
  if (session.value.state === 'lunch') return '午间休市 · 等待下午行情'
  return session.value.label || '市场状态待确认'
})
const bannerCopy = computed(() => marketError.value
  ? '数据源没有返回新行情，请查看各指数的时点与状态。'
  : marketOpen.value
    ? '指数为 1 分钟分时，不代表逐笔行情；行情来源和时点在卡片内标注。'
    : '非交易时段行情按休市快照或已落库数据展示，不标记为实时。')
const qualityItems = computed(() => {
  const statuses = charts.value.map(indexStatus)
  const bad = statuses.filter((status) => status === 'stale' || status === 'unavailable').length
  const pending = statuses.some((status) => status === 'loading')
  const allCurrent = statuses.every((status) => status === 'ok')
  const allSnapshots = statuses.every((status) => status === 'closed' || status === 'snapshot')
  const indexValue = bad ? (bad === statuses.length ? '不可用或陈旧' : '部分异常') : pending ? '采集中' : allCurrent ? '1分钟更新' : allSnapshots ? '休市快照' : '部分异常'
  return [
    { label: '指数行情', state: bad || (!pending && !allCurrent && !allSnapshots) ? 'degraded' : pending ? 'stored' : allCurrent ? 'live' : 'snapshot', value: indexValue, time: dateText(primaryIndex.value.data_time) },
    { label: '资金方向', state: marketError.value || flow.value.status !== 'ok' ? 'degraded' : 'stored', value: marketError.value ? '陈旧' : flow.value.status === 'ok' ? (flow.value.data_time ? '已更新' : '时点未核验') : statusLabel(flow.value.status), time: dateText(flow.value.data_time) },
    { label: '市场新闻', state: newsError.value || news.value.status !== 'ok' ? 'degraded' : 'stored', value: newsError.value ? '陈旧' : statusLabel(news.value.status), time: dateText(news.value.data_time) },
    { label: '组合行情', state: holdings.value.status === 'stale' || portfolioQuotes.value.status === 'stale' || portfolioQuotes.value.errors.length ? 'degraded' : 'stored', value: holdings.value.status === 'loading' ? '读取中' : holdings.value.items.length ? '逐股标注' : holdings.value.status === 'ok' ? '暂无持仓' : '读取失败', time: portfolioQuoteStatus.value },
  ]
})
async function fetchExtras() {
  if (disposed || hidden.value || extrasBusy.value) return
  const seq = ++extrasSeq
  const controller = new AbortController()
  extrasController = controller
  extrasBusy.value = true
  try {
    const options = { silent: true, signal: controller.signal }
    const [watch, alert, holding, quote, watchQuoteResult] = await Promise.allSettled([
      api.get('/api/watchlist', options),
      api.get('/api/watchlist/alerts', options),
      api.get('/api/holdings', options),
      api.get('/api/portfolio/quotes?scope=holdings', options),
      api.get('/api/portfolio/quotes?scope=watchlist', options),
    ])
    if (disposed || seq !== extrasSeq || controller.signal.aborted) return
    const listResult = (result, previous) => result.status === 'fulfilled' && Array.isArray(result.value)
      ? { items: result.value, status: 'ok' }
      : { ...previous, status: 'stale' }
    const quoteResult = (result, previous) => result.status === 'fulfilled' && result.value && result.value.items && typeof result.value.items === 'object' && !Array.isArray(result.value.items) && Array.isArray(result.value.errors)
      ? { ...result.value, status: 'ok' }
      : { ...previous, status: 'stale' }
    watchlist.value = listResult(watch, watchlist.value)
    alerts.value = listResult(alert, alerts.value)
    holdings.value = listResult(holding, holdings.value)
    portfolioQuotes.value = quoteResult(quote, portfolioQuotes.value)
    watchlistQuotes.value = quoteResult(watchQuoteResult, watchlistQuotes.value)
  } finally {
    if (!disposed && seq === extrasSeq) {
      extrasBusy.value = false
      deadlines.value.extras = Date.now() + interval('extras')
    }
  }
}

function preserveFeed(previous, incoming, field) {
  if (!incoming) return previous ? { ...previous, status: 'stale', error: '本次响应缺少该数据' } : { [field]: [], status: 'unavailable' }
  const failed = incoming.status !== 'ok'
  const keep = failed && !incoming[field]?.length && previous?.[field]?.length
  return keep ? { ...previous, ...incoming, [field]: previous[field], data_time: previous.data_time, source: previous.source, status: 'stale' } : incoming
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
        // Only reuse the old quote with its own points; never mix a new series
        // with an older session's previous close or percentage change.
        if (incoming?.status !== 'ok' && !incoming?.points?.length && previous?.points?.length) return { ...merged, ...Object.fromEntries(['last', 'pct_chg', 'prev_close'].map((field) => [field, previous[field]])), ...ix }
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
  return Promise.all([...['market', 'news', 'strategy'].map(fetchResource), fetchExtras()])
}
function togglePause() { paused.value = !paused.value; if (!paused.value) tick() }
function tick() {
  now.value = Date.now()
  if (paused.value || hidden.value || disposed) return
  for (const key of ['market', 'news', 'strategy']) if (now.value >= deadlines.value[key]) fetchResource(key)
  if (now.value >= deadlines.value.extras) fetchExtras()
}
function onVisibility() {
  hidden.value = document.hidden
  if (hidden.value) {
    for (const key of Object.keys(controllers)) { sequences[key]++; controllers[key].abort(); inFlight.value[key] = false }
    extrasSeq++
    extrasController?.abort()
    extrasBusy.value = false
    deadlines.value.extras = 0
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
  extrasSeq++
  extrasController?.abort()
  for (const controller of Object.values(controllers)) controller.abort()
})
</script>

<style scoped>
.dashboard-shell { --dash-bg:#f4f7fb; --dash-surface:#fff; --dash-border:#dfe7f0; --dash-border-soft:#edf1f6; --dash-text:#172234; --dash-muted:#738197; --dash-blue:#246bce; --dash-blue-soft:#edf5ff; --dash-red:#e34d59; --dash-green:#15936b; --dash-amber:#c78316; min-height:100%; padding:20px 24px 28px; background:var(--dash-bg); color:var(--dash-text); font-size:13px; line-height:1.5; }
.dashboard-shell,.dashboard-shell *{box-sizing:border-box}.dashboard-shell h1,.dashboard-shell h2,.dashboard-shell h3,.dashboard-shell p{margin:0}.dashboard-shell a{color:var(--dash-blue);text-decoration:none}.dashboard-shell button{font:inherit}.dashboard-shell button:focus-visible,.dashboard-shell a:focus-visible{outline:2px solid #4c91e6;outline-offset:2px}.dashboard-shell .up{color:var(--dash-red)}.dashboard-shell .down{color:var(--dash-green)}.dashboard-shell time,.dashboard-shell b,.dashboard-shell strong{font-variant-numeric:tabular-nums}
.dashboard-topbar{display:flex;align-items:center;gap:24px;padding:0 0 18px;border-bottom:1px solid var(--dash-border)}.brand-block{display:flex;align-items:center;gap:11px;min-width:220px}.brand-mark{width:38px;height:38px;display:grid;place-items:center;border-radius:10px;background:#e4f0ff;color:var(--dash-blue);font-size:20px}.brand-block h1{font-size:23px;letter-spacing:-.3px;line-height:1.2}.brand-block p{margin-top:3px;color:var(--dash-muted);font-size:11px}.topbar-context{display:flex;align-items:center;flex-wrap:wrap;gap:9px;color:var(--dash-muted);font-size:12px}.context-date,.session-pill,.context-time{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}.session-pill{padding:4px 9px;border-radius:999px;background:#f0f2f5;color:#687588;font-weight:600}.session-pill.trading{background:#e5f6ef;color:var(--dash-green)}.status-dot{width:7px;height:7px;display:inline-block;border-radius:50%;background:#aab4c1}.session-pill.trading .status-dot,.status-dot.ok{background:#2daf82}.topbar-actions{margin-left:auto;display:flex;align-items:center;flex-wrap:wrap;justify-content:flex-end;gap:7px}.refresh-hint{color:var(--dash-muted);font-size:11px;white-space:nowrap}.toolbar-button{display:inline-flex;align-items:center;gap:5px;min-height:31px;padding:5px 10px;border:1px solid var(--dash-border);border-radius:7px;background:var(--dash-surface);color:#465469;cursor:pointer}.toolbar-button:hover{border-color:#b4c7df;background:#f8fbff}.toolbar-button.primary{border-color:var(--dash-blue);background:var(--dash-blue);color:#fff}.toolbar-button:disabled{opacity:.62;cursor:wait}.toolbar-button.icon-only{width:32px;justify-content:center;padding:5px}.dashboard-notice{margin:12px 0 0;padding:8px 11px;border:1px solid #f1d394;border-radius:8px;color:#8e651c;background:#fff9e9}
.market-status-banner{display:flex;align-items:center;gap:12px;margin:18px 0 14px;padding:11px 14px;border:1px solid #bfe6d5;border-radius:10px;background:#effbf5}.market-status-banner.closed{border-color:#cbdcf2;background:#f2f7fd}.market-status-banner.degraded{border-color:#f0c8ce;background:#fff4f5}.banner-icon{width:28px;height:28px;display:grid;place-items:center;border-radius:50%;background:#27a779;color:#fff;font-size:17px}.market-status-banner.closed .banner-icon{background:#6a91bd}.market-status-banner.degraded .banner-icon{background:var(--dash-red)}.banner-copy{display:flex;flex-direction:column;gap:1px;min-width:0}.banner-copy strong{font-size:14px}.banner-copy span{color:var(--dash-muted);font-size:11px;overflow-wrap:anywhere}.quality-chip{margin-left:auto;padding:4px 8px;border-radius:999px;color:var(--dash-green);background:#ddf4ea;font-size:11px;white-space:nowrap}.quality-chip.degraded{color:var(--dash-amber);background:#fff4dc}
.index-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid var(--dash-border);border-radius:10px;overflow:hidden;background:var(--dash-surface);box-shadow:0 1px 3px rgba(30,58,88,.03)}.index-card{min-width:0;padding:14px 15px 11px;border-right:1px solid var(--dash-border-soft)}.index-card:last-child{border-right:0}.index-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}.index-card h2{font-size:13px;line-height:1.3}.index-card h2 small{color:var(--dash-muted);font-weight:500;font-size:10px;margin-left:4px}.index-source{display:block;margin-top:3px;color:var(--dash-muted);font-size:10px}.data-badge{display:inline-flex;align-items:center;gap:4px;color:var(--dash-green);font-size:10px;white-space:nowrap}.data-badge>i{width:6px;height:6px;border-radius:50%;background:currentColor}.data-badge.warning,.data-badge.degraded{color:var(--dash-amber)}.data-badge.loading{color:var(--dash-blue)}.index-quote{display:flex;align-items:baseline;gap:9px;margin:10px 0 2px}.index-quote strong{font-family:Consolas,monospace;font-size:24px;letter-spacing:-.5px}.index-quote>span{font-family:Consolas,monospace;font-size:13px}.index-chart-mini{position:relative;height:94px;margin-top:4px}.chart{width:100%;height:100%;overflow:visible}.grid-line{stroke:#e8eef5;stroke-width:.8}.price-line{stroke:var(--dash-blue);stroke-width:1.8;vector-effect:non-scaling-stroke}.price-line.up{stroke:var(--dash-red)}.price-line.down{stroke:var(--dash-green)}.focus-line{stroke-width:2.2}.chart-empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;color:var(--dash-muted);border:1px dashed #d8e1eb;border-radius:7px;background:#fbfcfe;font-size:12px}.chart-empty .el-icon{color:#97aac0;font-size:22px}.chart-tip{position:absolute;top:2px;right:2px;padding:4px 7px;border:1px solid #cbd9e8;border-radius:5px;background:#fff;color:#516276;box-shadow:0 3px 10px rgba(42,70,100,.1);font-family:Consolas,monospace;font-size:10px;pointer-events:none}.index-card-foot{display:flex;justify-content:space-between;gap:5px;color:var(--dash-muted);font-size:10px}
.workspace-grid{display:grid;grid-template-columns:minmax(0,1fr) 340px;align-items:start;gap:14px;margin-top:14px}.workspace-main{display:grid;grid-template-columns:minmax(0,1.28fr) minmax(280px,.72fr);gap:14px;min-width:0}.workspace-main .funds-card{grid-column:1/-1}.workspace-aside{display:grid;gap:14px;min-width:0}.card{min-width:0;border:1px solid var(--dash-border);border-radius:10px;background:var(--dash-surface);box-shadow:0 1px 3px rgba(30,58,88,.03)}.card-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:15px 16px 12px;border-bottom:1px solid var(--dash-border-soft)}.card-header.compact{padding:13px 14px 10px}.card-header h2{margin-top:3px;font-size:15px;line-height:1.25}.card-header h2 small{color:var(--dash-muted);font-size:11px;font-weight:500;margin-left:5px}.card-header h2 em{display:inline-grid;min-width:19px;height:19px;place-items:center;margin-left:5px;border-radius:999px;color:#fff;background:var(--dash-red);font-size:10px;font-style:normal}.eyebrow{color:#8192a8;font-size:9px;font-weight:700;letter-spacing:1.1px}.text-link{display:inline-flex;align-items:center;gap:3px;color:var(--dash-blue);font-size:11px;white-space:nowrap}
.focus-content{display:grid;grid-template-columns:160px minmax(0,1fr);gap:14px;padding:14px 16px 9px}.focus-quote{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:3px}.focus-quote strong{font-family:Consolas,monospace;font-size:31px;letter-spacing:-1px}.focus-quote>span{font-family:Consolas,monospace;font-size:15px}.focus-quote small{margin-top:7px;color:var(--dash-muted);font-size:10px;line-height:1.6}.focus-chart{min-width:0;height:190px}.focus-svg{width:100%;height:100%}.focus-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;padding:10px 16px 14px;border-top:1px solid var(--dash-border-soft)}.focus-stats div{display:flex;flex-direction:column;gap:2px;min-width:0}.focus-stats span{color:var(--dash-muted);font-size:10px}.focus-stats b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:Consolas,monospace;font-size:12px;font-weight:600}
.watchlist-table{padding:0 15px 10px}.watchlist-row{display:grid;grid-template-columns:minmax(135px,1fr) minmax(65px,.7fr) 50px 70px;align-items:center;gap:8px;min-height:37px;border-bottom:1px solid #f0f3f7;font-size:11px}.watchlist-row:last-child{border-bottom:0}.watchlist-row.table-head{min-height:30px;color:var(--dash-muted);font-size:10px}.stock-cell{display:flex;align-items:center;gap:5px;min-width:0}.stock-cell .el-icon{color:#edae31;flex:none}.stock-cell b{font-family:Consolas,monospace;font-size:11px}.stock-cell small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--dash-muted)}.row-status{display:inline-flex;align-items:center;gap:4px;color:var(--dash-green);font-size:10px;white-space:nowrap}.card-empty{min-height:150px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;padding:22px 14px;text-align:center;color:var(--dash-muted)}.card-empty .el-icon{color:#a3b4c7;font-size:24px}.card-empty strong{color:#54667b;font-size:12px;font-weight:600}.card-empty span{font-size:11px}.card-empty a{margin-top:3px;font-size:11px}
.segmented{display:flex}.segmented button{padding:4px 10px;border:1px solid var(--dash-border);border-radius:0;background:#fff;color:var(--dash-muted);cursor:pointer;font-size:11px}.segmented button:first-child{border-radius:5px 0 0 5px}.segmented button:last-child{margin-left:-1px;border-radius:0 5px 5px 0}.segmented button[aria-pressed=true]{z-index:1;border-color:#8db7e9;background:var(--dash-blue-soft);color:var(--dash-blue)}.flow-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:13px 16px 10px}.flow-summary div{display:flex;flex-direction:column;gap:2px}.flow-summary span{color:var(--dash-muted);font-size:10px}.flow-summary b{font-family:Consolas,monospace;font-size:19px;font-weight:600}.flow-summary em{color:var(--dash-muted);font-size:10px;font-style:normal;font-weight:400}.light-flow{padding:5px 16px 11px}.flow-row{display:grid;grid-template-columns:100px minmax(0,1fr) 68px;align-items:center;gap:9px;min-height:23px}.board-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#5b6d82;font-size:10px}.bar-space{position:relative;height:20px}.zero-line{position:absolute;inset:0 auto 0 50%;border-left:1px solid #cdd9e6}.flow-bar{position:absolute;top:6px;height:9px;border-radius:5px;background:#77b5f3}.flow-bar.up{background:#e77d89}.flow-bar.down{background:#65c9a6}.flow-row strong{text-align:right;font-family:Consolas,monospace;font-size:10px;font-weight:600}.card-footer{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:5px 10px;padding:9px 15px;border-top:1px solid var(--dash-border-soft);color:var(--dash-muted);font-size:10px}.card-footer .degraded{color:var(--dash-amber)}
.alert-list{margin:0;padding:0 14px 8px;list-style:none}.alert-item{display:grid;grid-template-columns:26px minmax(0,1fr) auto;align-items:center;gap:7px;padding:10px 0;border-bottom:1px solid #f0f3f7}.alert-item:last-child{border-bottom:0}.alert-icon{width:23px;height:23px;display:grid;place-items:center;border-radius:7px;color:var(--dash-amber);background:#fff3d9}.alert-item.danger .alert-icon{color:var(--dash-red);background:#ffeaed}.alert-item.success .alert-icon{color:var(--dash-green);background:#e5f6ef}.alert-item div{display:flex;flex-direction:column;gap:2px;min-width:0}.alert-item b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;font-weight:600}.alert-item small{color:var(--dash-muted);font-size:10px;line-height:1.4}.alert-item button,.alert-item a{padding:3px 6px;border:0;border-radius:4px;color:var(--dash-blue);background:var(--dash-blue-soft);cursor:pointer;font-size:10px;text-decoration:none}.quality-list{padding:5px 14px 9px}.quality-row{display:grid;grid-template-columns:13px minmax(0,1fr) auto;align-items:center;gap:7px;min-height:33px;border-bottom:1px solid #f0f3f7}.quality-row:last-child{border-bottom:0}.quality-state{width:8px;height:8px;border-radius:50%;background:#b8c1cc}.quality-state.live{background:#2daf82}.quality-state.snapshot{background:#5e91c9}.quality-state.stored{background:#8796a8}.quality-state.degraded{background:var(--dash-amber)}.quality-label{color:#52657a;font-size:11px}.quality-row b{color:#4a5d72;font-size:10px;font-weight:600}.quality-row small{grid-column:2/-1;color:var(--dash-muted);font-size:9px;margin-top:-7px}.quality-note{display:flex;gap:6px;margin:2px 14px 13px;padding:7px 8px;border-radius:6px;color:var(--dash-muted);background:#f6f9fc;font-size:10px;line-height:1.5}.risk-main{display:flex;align-items:center;gap:13px;padding:15px 14px 12px}.risk-ring{width:76px;height:76px;display:flex;flex-direction:column;align-items:center;justify-content:center;border:6px solid #d7e0ea;border-radius:50%;color:#6f7f93;flex:none}.risk-ring.guarded{border-color:#f3c77a;color:var(--dash-amber)}.risk-ring strong{font-size:14px;line-height:1.2}.risk-ring small{margin-top:2px;color:var(--dash-muted);font-size:9px}.risk-copy{display:flex;flex-direction:column;gap:3px;min-width:0}.risk-copy b{color:var(--dash-blue);font-size:15px}.risk-copy span{color:#53677d;font-size:11px}.risk-copy small{color:var(--dash-muted);font-size:10px}.risk-foot{display:flex;justify-content:space-between;gap:8px;padding:9px 14px 12px;border-top:1px solid var(--dash-border-soft);color:var(--dash-muted);font-size:10px}.compact-risk{min-height:125px}
.lower-grid{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(300px,.9fr);gap:14px;margin-top:14px}.news-list{max-height:300px;overflow-y:auto}.light-news{padding:0 15px}.news-item{padding:11px 0 10px 12px;border-left:1px solid #d9e1ea;position:relative}.news-item::before{content:'';position:absolute;width:6px;height:6px;top:17px;left:-3px;border-radius:50%;background:#9eb0c4}.news-item.important::before{background:var(--dash-red)}.news-meta{display:flex;flex-wrap:wrap;align-items:center;gap:5px 9px;color:var(--dash-muted);font-size:10px}.important-flag{padding:1px 5px;border-radius:4px;color:var(--dash-red);background:#ffedf0;font-size:9px}.news-item h3{margin-top:4px;font-size:12px;line-height:1.55}.news-content{display:-webkit-box;margin-top:3px!important;overflow:hidden;color:#65768a;font-size:11px;line-height:1.65;-webkit-box-orient:vertical;-webkit-line-clamp:2}.news-content.expanded{display:block}.news-actions{display:flex;gap:13px;margin-top:5px;font-size:10px}.news-actions button{padding:0;border:0;color:var(--dash-blue);background:transparent;cursor:pointer}.strategy-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:15px}.strategy-metrics div{display:flex;flex-direction:column;gap:3px;min-width:0}.strategy-metrics span,.strategy-metrics small{color:var(--dash-muted);font-size:10px}.strategy-metrics b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#43576d;font-size:17px}.strategy-metrics b.accent{color:var(--dash-blue)}.strategy-tags{display:flex;flex-wrap:wrap;gap:6px;padding:0 15px 13px}.strategy-tags span{padding:3px 7px;border:1px solid #f1d1d4;border-radius:5px;color:var(--dash-red);background:#fff5f6;font-size:10px}.card-note{padding:0 15px 16px;color:var(--dash-muted);font-size:11px}.dashboard-footer{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:7px 15px;padding-top:13px;color:var(--dash-muted);font-size:10px}
.watchlist-row{grid-template-columns:minmax(90px,1fr) minmax(105px,1fr) 48px}.quote-cell{display:flex;flex-direction:column;gap:1px;min-width:0}.row-status.muted{color:var(--dash-muted)}.data-note{padding:7px 0;color:var(--dash-muted);font-size:10px;line-height:1.5}.flow-note{padding:0 16px 10px}.point-marker{fill:var(--dash-blue)}.data-badge.snapshot{color:#6689b2}.strategy-details{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 12px;padding:0 15px 12px;color:var(--dash-muted);font-size:10px}.strategy-details span{display:flex;justify-content:space-between;gap:8px}.strategy-details b{color:#53677d}.warning-note{color:var(--dash-amber)}.strategy-lists{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:3px 15px 14px}.strategy-lists h3{display:flex;justify-content:space-between;gap:5px;margin-bottom:6px;font-size:11px}.strategy-lists h3 small,.strategy-lists p{color:var(--dash-muted);font-size:10px;font-weight:400}.strategy-list-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:6px;padding:5px 0;border-bottom:1px solid var(--dash-border-soft);font-size:10px}.strategy-list-row span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.strategy-list-row span:nth-child(2){color:var(--dash-muted)}
@media (max-width:1280px){.dashboard-topbar{gap:15px}.workspace-grid{grid-template-columns:minmax(0,1fr) 310px}.workspace-main{grid-template-columns:minmax(0,1fr)}.workspace-main .funds-card{grid-column:auto}}
@media (max-width:1000px){.index-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.index-card:nth-child(2){border-right:0}.index-card:nth-child(-n+2){border-bottom:1px solid var(--dash-border-soft)}.workspace-grid,.lower-grid{grid-template-columns:1fr}.workspace-aside{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:760px){.dashboard-shell{padding:13px 11px 20px}.dashboard-topbar{align-items:flex-start;flex-wrap:wrap;gap:11px}.brand-block{min-width:0}.topbar-context{width:100%;order:3}.topbar-actions{margin-left:auto}.refresh-hint{display:none}.market-status-banner{align-items:flex-start}.quality-chip{margin-left:auto}.index-grid{grid-template-columns:1fr}.index-card,.index-card:nth-child(2){border-right:0;border-bottom:1px solid var(--dash-border-soft)}.index-card:last-child{border-bottom:0}.workspace-aside{grid-template-columns:1fr}.focus-content{grid-template-columns:1fr;gap:6px}.focus-quote{flex-direction:row;align-items:baseline;flex-wrap:wrap}.focus-quote small{width:100%;margin-top:0}.focus-chart{height:160px}.focus-stats{grid-template-columns:repeat(2,1fr);row-gap:9px}.flow-row{grid-template-columns:76px minmax(0,1fr) 58px;gap:6px}.card-header{padding-left:13px;padding-right:13px}.watchlist-table{padding-left:12px;padding-right:12px;overflow-x:auto}.watchlist-row{min-width:270px}.strategy-lists{grid-template-columns:1fr}.dashboard-footer{align-items:flex-start;flex-direction:column}}
@media (prefers-reduced-motion:reduce){.dashboard-shell *,.dashboard-shell *::before{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
</style>
