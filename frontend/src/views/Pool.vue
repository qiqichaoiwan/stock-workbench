<template>
  <div class="workbench-page pool-page">
    <el-alert v-if="riskGuard === true" class="workbench-alert" type="warning" :closable="false" style="margin-bottom:16px"
              title="弱市守门已触发:主池暂停新增入选(存量继续按评级跟踪),此为规则层硬约束" />
    <el-alert v-if="riskGuard === null" class="workbench-alert" type="info" :closable="false" style="margin-bottom:16px"
              title="守门状态加载失败，无法确认是否触发。">
      <el-button size="small" link @click="loadRiskGuard">重试</el-button>
    </el-alert>

    <el-card class="page-card workbench-card">
      <template #header>
        <div class="card-head">
          <el-radio-group v-model="poolType" @change="load">
            <el-radio-button value="main">总选股池(Top30)</el-radio-button>
            <el-radio-button value="track">赛道池</el-radio-button>
          </el-radio-group>
          <el-select v-if="poolType === 'track'" v-model="trackId" placeholder="选择赛道"
                     style="width:180px" @change="load">
            <el-option v-for="t in tracks" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
          <span v-if="poolType === 'track' && currentTrack" class="muted" style="font-size:12px">
            成员 {{ fmtInt(currentTrack.member_count) }} 只 · 来源{{ trackSourceLabel(currentTrack.member_source) }}
          </span>
          <span class="grow" />
          <span v-if="loadError" class="data-quality-label degraded">选股池读取失败</span>
          <span v-else-if="!loading && !rows.length" class="data-quality-label">暂无选股池行情</span>
          <span v-else class="data-quality-label" :class="{ degraded: effectiveQuoteMeta.errors?.length }" :title="quoteStatusText(effectiveQuoteMeta, quoteLoading)">{{ quoteStatusText(effectiveQuoteMeta, quoteLoading) }}</span>
          <el-input v-model="search" placeholder="搜索代码 / 名称" clearable style="width:200px" />
          <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        </div>
      </template>
      <el-alert v-if="loadError" class="quote-alert" type="error" :title="loadError" :closable="false" show-icon />
      <el-alert v-if="rows.length && quoteMeta.errors?.length" class="quote-alert" type="warning" :closable="false" show-icon title="行情刷新失败，保留最近一次可用行情；请稍后重试。" />
      <el-empty v-if="!loading && !loadError && poolType === 'track' && allTrackEmpty" description="赛道成分尚未同步">
        <template #description>
          <p>赛道成分尚未同步</p>
          <p class="muted" style="font-size:12px;line-height:1.6">
            赛道成分需先在行业页完成同步后,赛道池才有数据;<br />
            本地网络受限时同步可能降级(503),云端部署后自动恢复。
          </p>
        </template>
        <el-button type="primary" size="small" @click="goSyncTracks">去同步成分</el-button>
      </el-empty>
      <div class="table-scroll" v-else>
      <el-table class="workbench-table" :data="pagedRows" size="small" v-loading="loading" stripe border>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column label="名称" min-width="110">
          <template #default="{ row }">{{ cleanName(row.name) }}</template>
        </el-table-column>
        <el-table-column label="现价" width="90" align="right">
          <template #default="{ row }">
            <span v-if="quoteOf(row)" class="num" :title="quoteTitle(row)">{{ fmtNum(quoteOf(row).price) }}</span>
            <span v-else class="muted">-</span>
            <div v-if="quoteOf(row)" class="muted" style="font-size:10px">{{ quoteTitle(row) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="今日涨跌幅" width="110" align="right">
          <template #default="{ row }">
            <span v-if="quoteOf(row) && quoteOf(row).pct_chg !== null && quoteOf(row).pct_chg !== undefined"
                  :class="upDownClass(quoteOf(row).pct_chg)" :title="quoteTitle(row)">{{ fmtPct(quoteOf(row).pct_chg) }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="评级" width="90">
          <template #default="{ row }">
            <el-tag :type="ratingType(row.rating)">{{ ratingLabel(row.rating) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="综合评分" width="100" sortable :sort-by="'score'">
          <template #default="{ row }">
            <span class="num" :style="{ fontWeight: row.score >= 65 ? 700 : 400 }">{{ fmtNum(row.score, 1) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="入选价" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.in_price) }}</template>
        </el-table-column>
        <el-table-column prop="trade_date" label="入选日期" width="110" />
        <el-table-column label="命中理由" min-width="320">
          <template #default="{ row }">
            <el-tooltip placement="top" :disabled="row.reasons.length <= 3">
              <template #content>
                <div v-for="r in row.reasons" :key="r">{{ r }}</div>
              </template>
              <span>
                <el-tag v-for="r in row.reasons.slice(0, 3)" :key="r" size="small" style="margin:2px" type="info">{{ r }}</el-tag>
                <el-tag v-if="row.reasons.length > 3" size="small" type="info" style="margin:2px">+{{ row.reasons.length - 3 }}</el-tag>
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column v-if="canOperate" label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-popconfirm title="手动出池?" @confirm="forceOut(row)">
              <template #reference>
                <el-button size="small" link type="warning">手动出池</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      </div>
      <div v-if="!allTrackEmpty" class="pager">
        <el-pagination layout="total, sizes, prev, pager, next" :total="filteredRows.length"
                       :page-sizes="[20, 50, 100]" v-model:page-size="pageSize"
                       v-model:current-page="page" />
      </div>
      <p v-if="!allTrackEmpty" class="muted" style="font-size:12px;margin:6px 0 0">前端分页 · 共 {{ filteredRows.length }} 只(服务端全量取回,已按评分全局排序);行情显示逐股时间与状态,源不可用时显示已落库日线或缺失;交易时段每 20 秒自动刷新。</p>
    </el-card>

    <el-card class="page-card workbench-card">
      <template #header><div class="card-head">出池留档(含原因,可回溯)<span class="grow" /><el-button size="small" @click="loadHistory">刷新</el-button></div></template>
      <el-alert v-if="historyError" type="error" :title="historyError" :closable="false" show-icon />
      <el-table class="workbench-table" :data="history" size="small" max-height="300" stripe border>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column label="名称" min-width="110">
          <template #default="{ row }">{{ cleanName(row.name) }}</template>
        </el-table-column>
        <el-table-column prop="pool_type" label="池" width="80">
          <template #default="{ row }">{{ row.pool_type === 'main' ? '总池' : '赛道' }}</template>
        </el-table-column>
        <el-table-column label="入选价" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.in_price) }}</template>
        </el-table-column>
        <el-table-column prop="trade_date" label="入选日" width="110" />
        <el-table-column prop="out_date" label="出池日" width="110" />
        <el-table-column prop="out_reason" label="出池原因" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { cleanName, fmtNum, fmtInt, fmtPct, ratingLabel, ratingType, upDownClass } from '../utils/fmt'
import { isTradingSession, quoteStatusText, quoteItemLabel, markQuoteRefreshFailed } from '../utils/poolQuotes'
import { effectiveQuote } from '../utils/portfolioQuotes'
import { useUserStore } from '../store'

const store = useUserStore()
const route = useRoute()
const router = useRouter()
const canOperate = computed(() => store.isAnalyst)

const poolType = ref('main')
const trackId = ref(null)
const tracks = ref([])
const rows = ref([])
const history = ref([])
const loading = ref(false)
const loadError = ref('')
const historyError = ref('')
const search = ref('')
const page = ref(1)
const pageSize = ref(20)
// riskGuard 三态:false=未触发 / true=生效 / null=加载失败(显式区分失败态)
const riskGuard = ref(undefined)
let loadSeq = 0
let historySeq = 0
let riskSeq = 0
let active = false

// 行情:现价 / 今日涨跌幅(实时快照优先,失败降级已落库日线,口径在页头标注)
const quotes = ref({})
const quoteMeta = ref({})
const quoteLoading = ref(false)
const quoteClock = ref(Date.now())
let quoteSeq = 0
let quoteTimer = null
let quoteClockTimer = null

function quoteOf(row) {
  const quote = quotes.value[String(row?.code || '')]
  if (!quote) return null
  const current = effectiveQuote(quote, quoteClock.value)
  // A quote that was live during the session becomes a reference snapshot as
  // soon as the market closes, even if its freshness window remains.
  if (current.realtime && !isTradingSession(new Date(quoteClock.value))) {
    return { ...current, realtime: false }
  }
  return current
}

function quoteTitle(row) {
  return quoteItemLabel(quoteOf(row))
}

// 顶部汇总必须跟逐股标签使用同一份“当前时刻”口径。页面休眠、网络超时或
// 交易时段结束后，服务端返回的 realtime 标志可能已经过期，不能继续显示“均为实时”。
const effectiveQuoteMeta = computed(() => {
  const meta = quoteMeta.value || {}
  const displayed = rows.value.map((row) => quoteOf(row))
  const realtimeCount = displayed.filter((quote) => quote?.realtime).length
  const storedCount = displayed.filter((quote) => quote?.quote_kind === 'stored').length
  const missingCount = displayed.filter((quote) => !quote || quote.quote_kind === 'missing').length
  return {
    ...meta,
    realtime: displayed.length > 0 && missingCount === 0 && realtimeCount === displayed.length,
    realtime_count: realtimeCount,
    counts: { ...(meta.counts || {}), stored: storedCount, missing: missingCount },
  }
})

async function loadQuotes() {
  const seq = (quoteSeq += 1)
  if (!rows.value.length) {
    quotes.value = {}
    quoteMeta.value = {}
    quoteLoading.value = false
    return
  }
  quoteLoading.value = true
  try {
    const q = { pool_type: poolType.value }
    if (poolType.value === 'track' && trackId.value) q.track_id = trackId.value
    const data = await api.get('/api/screener/pool/quotes', { params: q, silent: true })
    if (seq !== quoteSeq) return // 竞态:丢弃过期行情响应
    quotes.value = data?.items || {}
    quoteMeta.value = data || {}
  } catch {
    if (seq !== quoteSeq) return
    quotes.value = markQuoteRefreshFailed(quotes.value)
    quoteMeta.value = { ...quoteMeta.value, realtime: false, realtime_count: 0,
      as_of: quoteMeta.value.as_of || new Date().toISOString(), errors: ['refresh_failed'] }
  } finally {
    if (seq === quoteSeq) quoteLoading.value = false
  }
}

function scheduleQuoteRefresh() {
  if (quoteTimer) { clearInterval(quoteTimer); quoteTimer = null }
  quoteTimer = setInterval(() => {
    if (document.visibilityState === 'hidden') return
    if (loading.value) return
    if (isTradingSession()) {
      // A slow provider must not accumulate requests or continually discard
      // every response while the previous request is still in flight.
      if (!quoteLoading.value) loadQuotes()
    } else if (quoteMeta.value.realtime || quoteMeta.value.realtime_count) {
      quotes.value = Object.fromEntries(Object.entries(quotes.value).map(([code, quote]) =>
        [code, { ...quote, realtime: false }]))
      quoteMeta.value = { ...quoteMeta.value, realtime: false, realtime_count: 0 }
    }
  }, 20000)
}

function onVisibilityChange() {
  if (document.visibilityState !== 'visible') return
  if (loading.value) return
  if (!quoteLoading.value) loadQuotes()
  scheduleQuoteRefresh()
}

// 赛道tab:当前选中赛道,全部为 0 时显空状态引导卡
const currentTrack = computed(() =>
  tracks.value.find((t) => t.id === trackId.value) || null)
const allTrackEmpty = computed(() =>
  poolType.value === 'track' && tracks.value.length > 0 &&
  tracks.value.every((t) => Number(t.member_count) <= 0))

function trackSourceLabel(s) {
  return { concept_board: '东财', concept_board_sina: '新浪', keyword: '关键词',
    concept_board_ths: '同花顺核验', concept_board_local: '本地',
    eastmoney: '东财', sina: '新浪', ths: '同花顺核验', local: '本地',
    local_ths_verified: '同花顺核验' }[s] || '-'
}

function goSyncTracks() {
  router.push('/industries').then(() => {
    nextTick(() => {
      document.getElementById('track-mgmt')?.scrollIntoView({ behavior: 'smooth' })
    })
  })
}

const filteredRows = computed(() => {
  const kw = search.value.trim().toLowerCase()
  const filtered = !kw ? rows.value : rows.value.filter((r) => String(r.code || '').toLowerCase().includes(kw) || String(r.name || '').toLowerCase().includes(kw))
  return [...filtered].sort((a, b) => (Number(b.score) || -Infinity) - (Number(a.score) || -Infinity))
})
const pagedRows = computed(() =>
  filteredRows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))

watch(search, () => { page.value = 1 })

async function load() {
  const seq = (loadSeq += 1)
  loading.value = true
  loadError.value = ''
  // The selected pool may have changed; never keep quotes from the previous
  // filter while a new list is being loaded.
  quoteSeq += 1
  quoteLoading.value = false
  rows.value = []
  quotes.value = {}
  quoteMeta.value = {}
  try {
    const q = { pool_type: poolType.value }
    if (poolType.value === 'track' && trackId.value) q.track_id = trackId.value
    const data = await api.get('/api/screener/pool', { params: q, silent: true })
    if (seq !== loadSeq) return // 竞态:丢弃过期响应
    const items = Array.isArray(data) ? data : (data?.items || data?.data)
    if (!Array.isArray(items)) throw new Error('invalid pool response')
    rows.value = items
    page.value = 1
  } catch {
    if (seq === loadSeq) {
      rows.value = []
      loadError.value = '选股池读取失败，请点击刷新重试。'
    }
    return
  } finally {
    if (seq === loadSeq) loading.value = false
  }
  // 行情独立异步加载:列表先渲染,行情慢/失败都不阻塞表格
  await loadQuotes()
  if (active && seq === loadSeq) scheduleQuoteRefresh()
}

async function forceOut(row) {
  await api.post(`/api/screener/pool/${row.id}/out`, null, { params: { reason: 'manual' } })
  ElMessage.success('已出池')
  await Promise.all([load(), loadHistory()])
}

async function loadHistory() {
  const seq = ++historySeq
  historyError.value = ''
  try {
    const data = await api.get('/api/screener/pool/history', { silent: true })
    if (seq !== historySeq) return
    const items = Array.isArray(data) ? data : (data?.items || data?.data)
    if (!Array.isArray(items)) throw new Error('invalid pool history response')
    history.value = items
  } catch {
    if (seq === historySeq) historyError.value = `出池留档读取失败${history.value.length ? '，显示上次结果' : ''}；请点击刷新重试。`
  }
}

async function loadRiskGuard() {
  const seq = ++riskSeq
  try {
    const data = await api.get('/api/screener/dashboard', { silent: true })
    if (seq !== riskSeq) return
    riskGuard.value = typeof data?.risk_guard === 'boolean' ? data.risk_guard : null
  } catch { if (seq === riskSeq) riskGuard.value = null }
}

// 全局搜股透传:Layout 通过 /pool?q=代码 跳转,这里填搜索框(不再走 localStorage + 整页刷新)
function applyQuerySearch() {
  const q = route.query.q
  if (typeof q === 'string' && q) {
    search.value = q
    page.value = 1
  }
}
watch(() => [route.query.q, route.query.t], applyQuerySearch)

onMounted(async () => {
  active = true
  applyQuerySearch()
  document.addEventListener('visibilitychange', onVisibilityChange)
  quoteClockTimer = setInterval(() => { quoteClock.value = Date.now() }, 1000)
  loadRiskGuard()
  loadHistory()
  try {
    const list = await api.get('/api/screener/tracks', { silent: true })
    tracks.value = Array.isArray(list) ? list : []
    // trackId 为空默认选中首个 member_count>0 的赛道;已有 seq-token 竞态保留(load 内仍校验)
    if (trackId.value == null) {
      const first = tracks.value.find((t) => Number(t.member_count) > 0)
      if (first) trackId.value = first.id
    }
  } catch { tracks.value = [] }
  if (!active) return
  await load()
})
onBeforeUnmount(() => {
  active = false
  loadSeq += 1
  historySeq += 1
  riskSeq += 1
  quoteSeq += 1 // 丢弃在途行情响应
  if (quoteTimer) { clearInterval(quoteTimer); quoteTimer = null }
  if (quoteClockTimer) { clearInterval(quoteClockTimer); quoteClockTimer = null }
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<style scoped>
.workbench-card { border-radius: var(--radius-card, 12px); border-color: var(--line-soft, #e6ebf2); box-shadow: var(--shadow-card, 0 4px 16px rgba(24, 44, 72, .05)); }
.workbench-table :deep(.el-table__header th) { background: #f7f9fc; color: #617089; font-size: 12px; }
.workbench-table :deep(.el-table__row:hover > td) { background: #f4f8ff !important; }
.data-quality-label { color: var(--success, #188c64); font-size: 12px; white-space: nowrap; }
.data-quality-label.degraded { color: var(--warning, #b97700); }
.quote-alert { margin: 0 0 12px; }
@media (max-width: 768px) {
  .pool-page :deep(.card-head) { align-items: stretch; }
  .pool-page :deep(.el-input), .pool-page :deep(.el-select) { width: 100% !important; }
}
</style>
