<template>
  <div>
    <el-alert v-if="riskGuard === true" type="warning" :closable="false" style="margin-bottom:16px"
              title="弱市守门已触发:主池暂停新增入选(存量继续按评级跟踪),此为规则层硬约束" />
    <el-alert v-if="riskGuard === null" type="info" :closable="false" style="margin-bottom:16px"
              title="守门状态加载失败(网络或服务端异常),当前按未触发展示,入选操作请谨慎" />

    <el-card class="page-card">
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
          <span class="muted" style="font-size:12px" :title="quoteStatusText(quoteMeta, quoteLoading)">{{ quoteStatusText(quoteMeta, quoteLoading) }}</span>
          <el-input v-model="search" placeholder="搜索代码 / 名称" clearable style="width:200px" />
        </div>
      </template>
      <el-empty v-if="poolType === 'track' && allTrackEmpty" description="赛道成分尚未同步">
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
      <el-table :data="pagedRows" size="small" v-loading="loading" stripe border>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column label="名称" min-width="110">
          <template #default="{ row }">{{ cleanName(row.name) }}</template>
        </el-table-column>
        <el-table-column label="现价" width="90" align="right">
          <template #default="{ row }">
            <span v-if="quoteOf(row)" class="num" :title="quoteTitle(row)">{{ fmtNum(quoteOf(row).price) }}</span>
            <span v-else class="muted">-</span>
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
      <p v-if="!allTrackEmpty" class="muted" style="font-size:12px;margin:6px 0 0">前端分页 · 共 {{ filteredRows.length }} 只(服务端全量取回,已按评分全局排序);现价/涨跌幅优先取服务端实时行情(新浪批量报价),失败时降级为全市场快照缓存或已落库日线并标注来源;交易时段每 20 秒自动刷新,非交易时段为最近收盘快照。</p>
    </el-card>

    <el-card class="page-card">
      <template #header>出池留档(含原因,可回溯)</template>
      <el-table :data="history" size="small" max-height="300" stripe border>
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
import { isTradingSession, quoteStatusText, quoteStamp } from '../utils/poolQuotes'
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
const search = ref('')
const page = ref(1)
const pageSize = ref(20)
// riskGuard 三态:false=未触发 / true=生效 / null=加载失败(显式区分失败态)
const riskGuard = ref(false)
let loadSeq = 0

// 行情:现价 / 今日涨跌幅(实时快照优先,失败降级已落库日线,口径在页头标注)
const quotes = ref({})
const quoteMeta = ref({})
const quoteLoading = ref(false)
let quoteSeq = 0
let quoteTimer = null

function quoteOf(row) {
  return quotes.value[String(row?.code || '')] || null
}

function quoteTitle(row) {
  const quote = quoteOf(row)
  if (!quote) return ''
  if (quote.quote_kind === 'stored') return `已落库日线 ${quote.trade_date || ''}`.trim()
  const at = quoteStamp(quoteMeta.value.snapshot_at)
  const kind = quoteMeta.value.realtime ? '实时行情' : '行情快照'
  return at ? `${kind} ${at}` : kind
}

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
    // 刷新失败保留上一份行情,避免轮询抖动把已有现价/涨跌幅刷成空
  } finally {
    if (seq === quoteSeq) quoteLoading.value = false
  }
}

function scheduleQuoteRefresh() {
  if (quoteTimer) { clearInterval(quoteTimer); quoteTimer = null }
  if (!isTradingSession()) return // 非交易时段不轮询,避免无意义打源
  quoteTimer = setInterval(() => {
    if (document.visibilityState === 'hidden') return
    loadQuotes()
  }, 20000)
}

function onVisibilityChange() {
  if (document.visibilityState !== 'visible') return
  loadQuotes()
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
  try {
    const q = { pool_type: poolType.value }
    if (poolType.value === 'track' && trackId.value) q.track_id = trackId.value
    const data = await api.get('/api/screener/pool', { params: q, silent: true })
    if (seq !== loadSeq) return // 竞态:丢弃过期响应
    rows.value = Array.isArray(data) ? data : (data?.items || data?.data || [])
    page.value = 1
  } finally {
    if (seq === loadSeq) loading.value = false
  }
  // 行情独立异步加载:列表先渲染,行情慢/失败都不阻塞表格
  await loadQuotes()
  scheduleQuoteRefresh()
}

async function forceOut(row) {
  await api.post(`/api/screener/pool/${row.id}/out`, null, { params: { reason: 'manual' } })
  ElMessage.success('已出池')
  await Promise.all([load(), loadHistory()])
}

async function loadHistory() {
  try {
    const data = await api.get('/api/screener/pool/history', { silent: true })
    history.value = Array.isArray(data) ? data : (data?.items || data?.data || [])
  } catch { history.value = [] }
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
  applyQuerySearch()
  document.addEventListener('visibilitychange', onVisibilityChange)
  try {
    const list = await api.get('/api/screener/tracks', { silent: true })
    tracks.value = Array.isArray(list) ? list : []
    // trackId 为空默认选中首个 member_count>0 的赛道;已有 seq-token 竞态保留(load 内仍校验)
    if (trackId.value == null) {
      const first = tracks.value.find((t) => Number(t.member_count) > 0)
      if (first) trackId.value = first.id
    }
  } catch { tracks.value = [] }
  await loadHistory()
  await load()
  // 弱市守门横幅(来自仪表盘同一数据源);失败置 null 显式披露,不再静默当 false
  try {
    const d = await api.get('/api/screener/dashboard', { silent: true })
    riskGuard.value = !!d.risk_guard
  } catch { riskGuard.value = null }
})
onBeforeUnmount(() => {
  loadSeq += 1
  quoteSeq += 1 // 丢弃在途行情响应
  if (quoteTimer) { clearInterval(quoteTimer); quoteTimer = null }
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>
