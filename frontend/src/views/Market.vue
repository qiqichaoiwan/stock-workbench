<template>
  <div>
    <div class="market-status">
      <div class="status-item">
        <span class="status-label">指数</span>
        <strong>{{ indexAsOfText }}</strong>
      </div>
      <div class="status-item">
        <span class="status-label">板块资金</span>
        <strong>{{ flowAsOfText }}</strong>
        <el-tag v-if="flowStale" size="small" effect="plain" type="warning">早于最新数据日</el-tag>
        <el-tag v-if="flowOffSession" size="small" effect="plain" type="info">{{ offSessionLabel(flowSnap) }}</el-tag>
      </div>
      <span class="grow" />
      <el-button size="small" :loading="ixLoading || cmdLoading || flowLoading" @click="load">刷新数据</el-button>
    </div>
    <p class="market-note muted">时间均为北京时间；指数展示最近收盘值，资金与总览展示最近采集时点，均非实时行情。</p>
    <el-alert v-if="statError" :title="statError" type="error" :closable="false" show-icon />
    <el-alert v-else-if="!statLoading && !hasStat" :title="`暂无市场总览数据，请在任务中心运行「${taskName('market_stat')}」`" type="info" :closable="false" />
    <el-card v-if="hasStat" class="page-card">
      <template #header>
        <div class="card-head">
          <span>市场总览</span>
          <span class="grow" />
          <span class="muted" style="font-size:12px">{{ statAsOfText }} · 来源:{{ stat.source || '-' }}</span>
          <el-tag v-if="statOffSession" size="small" effect="plain" type="warning">{{ offSessionLabel(stat.snap_time) }}</el-tag>
        </div>
      </template>
      <el-row :gutter="12">
        <el-col :xs="12" :sm="6"><el-statistic title="上涨家数" :value="stat.advance ?? '-'" /></el-col>
        <el-col :xs="12" :sm="6"><el-statistic title="下跌家数" :value="stat.decline ?? '-'" /></el-col>
        <el-col :xs="12" :sm="6"><el-statistic title="涨停 / 跌停" :value="limitText" /></el-col>
        <el-col :xs="12" :sm="6"><el-statistic title="成交额(亿)" :value="amountText" /></el-col>
      </el-row>
      <p v-if="stat.source_note" class="muted" style="font-size:12px;margin:8px 0 0">数据来源：{{ stat.source_note }}</p>
    </el-card>

    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>主要指数</span>
          <el-tag v-if="latestIndexDate" size="small" effect="plain">数据日 {{ latestIndexDate }} · 收盘 15:00</el-tag>
          <span class="grow" />
          <span class="muted" style="font-size:12px">每交易日 16:45 定时落库 · 东财日线含成交额，备源日成交额取新浪快照</span>
        </div>
      </template>
      <div class="table-scroll">
      <el-table :data="indexes" size="small" v-loading="ixLoading" stripe border>
        <el-table-column prop="name" label="指数" min-width="110" />
        <el-table-column prop="code" label="代码" width="100" />
        <el-table-column label="点位" width="120" align="right">
          <template #default="{ row }"><span class="num">{{ fmtNum(row.close) }}</span></template>
        </el-table-column>
        <el-table-column label="涨跌幅" width="110" align="right">
          <template #default="{ row }">
            <span class="num" :class="upDownClass(row.pct_chg)">{{ fmtPct(row.pct_chg) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="成交额(亿)" width="130" align="right">
          <template #default="{ row }">
            <span v-if="row.amount" class="num">{{ fmtNum(row.amount / 1e8) }}</span>
            <el-tooltip v-else content="该日成交额缺失：东财日线未取到，新浪快照也无值" placement="top">
              <span class="muted">-</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="数据日" width="140">
          <template #default="{ row }">{{ tradeDateText(row.trade_date) }}</template>
        </el-table-column>
        <el-table-column label="点位源" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.source === 'em' ? 'success' : 'info'">
              {{ sourceLabel(row.source) }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      </div>
      <el-alert v-if="ixError" :title="ixError" type="error" :closable="false" show-icon />
      <el-empty v-if="!ixLoading && !ixError && !indexes.length" :description="`暂无指数数据,请先在任务中心运行「${taskName('sync_indexes')}」`" :image-size="60">
        <template #description>
          <p>暂无指数数据,请先在任务中心运行「{{ taskName('sync_indexes') }}」后可见</p>
          <p class="muted" style="font-size:12px">指数在交易日收盘后 16:45 落库；盘中展示最近一次收盘数据，周末及非交易日无新增</p>
        </template>
      </el-empty>
    </el-card>

    <!-- 行业资金:调 /fund-flow,点击行拉 history 画迷你趋势 -->
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>板块资金</span>
          <el-tag v-if="flowSnap" size="small" effect="plain">{{ flowAsOfText }}</el-tag>
          <span class="grow" />
          <el-radio-group v-model="boardType" size="small" @change="loadFlow">
            <el-radio-button value="industry">行业</el-radio-button>
            <el-radio-button value="concept">概念</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <div class="table-scroll">
      <el-table :data="flows" size="small" v-loading="flowLoading" stripe border
                highlight-current-row @row-click="onFlowRow">
        <el-table-column prop="board_name" label="板块" min-width="130" show-overflow-tooltip />
        <el-table-column label="主力净流入(亿)" width="130" align="right">
          <template #default="{ row }">
            <span class="num" :class="upDownClass(row.net_inflow)">{{ fmtNum(row.net_inflow) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="90">
          <template #default="{ row }">{{ flowSourceLabel(row.source) }}</template>
        </el-table-column>
      </el-table>
      </div>
      <el-alert v-if="flows.length && flowStale" type="warning" :closable="false" show-icon
                :title="`资金快照数据日 ${flowDateText} 早于最新指数数据日 ${latestIndexDate}，请在任务中心运行「${taskName('fund_flow')}」更新`" />
      <el-alert v-else-if="flows.length && flowOffSession" type="info" :closable="false" show-icon
                :title="`采集时点在交易时段外（${offSessionLabel(flowSnap)}），展示的是最近交易日的收盘累计值`" />
      <el-alert v-if="flowError" :title="flowError" type="error" :closable="false" show-icon />
      <el-empty v-if="!flowLoading && !flowError && !flows.length" description="暂无板块资金数据" :image-size="60">
        <template #description>
          <p>暂无板块资金数据,请先在任务中心运行「{{ taskName('fund_flow') }}」后可见</p>
          <p class="muted" style="font-size:12px">盘中快照 9:35 起每 30 分钟更新;点击表格行可看该板块近 30 日净流入趋势</p>
        </template>
      </el-empty>

      <!-- 选中板块近30日迷你趋势(div 条形 + 原生 table,不引入图表库) -->
      <div v-if="flowSel" style="margin-top:12px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <b>{{ flowSel }}</b>
          <span class="muted" style="font-size:12px">近30日净流入(亿,点击上方行切换)</span>
          <span class="grow" />
          <el-button size="small" link @click="clearHistory">收起</el-button>
        </div>
        <div v-loading="histLoading" style="min-height:80px">
          <el-alert v-if="histError" :title="histError" type="error" :closable="false" show-icon />
          <el-empty v-else-if="!histLoading && !flowHist.length" description="该板块暂无历史资金数据" :image-size="40" />
          <div v-if="flowHist.length" class="mini-bars">
            <el-tooltip v-for="h in flowHist" :key="h.snap_date"
                        :content="`${h.snap_date}: ${fmtNum(h.net_inflow)}`" placement="top">
              <div class="mini-bar" :class="Number(h.net_inflow) >= 0 ? 'pos' : 'neg'"
                   :style="{ height: barH(h.net_inflow) + 'px' }" />
            </el-tooltip>
          </div>
          <div v-if="flowHist.length" class="table-scroll" style="margin-top:8px">
            <table class="native-mini">
              <tr><th>日期</th><th style="text-align:right">净流入(亿)</th></tr>
              <tr v-for="h in flowHist" :key="h.snap_date">
                <td>{{ h.snap_date }}</td>
                <td style="text-align:right" :class="upDownClass(h.net_inflow)">{{ fmtNum(h.net_inflow) }}</td>
              </tr>
            </table>
          </div>
        </div>
      </div>
    </el-card>

    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>商品周期</span>
          <span class="grow" />
          <span v-if="commodityDate" class="muted" style="font-size:12px">数据日 {{ commodityDate }}</span>
        </div>
      </template>
      <div class="table-scroll">
      <el-table :data="commodities" size="small" v-loading="cmdLoading" stripe border>
        <el-table-column prop="series_name" label="品种" min-width="110" />
        <el-table-column label="信号" width="90">
          <template #default="{ row }">
            <el-tag :type="row.signal === '启动' ? 'danger' : row.signal === '持续' ? 'warning' : 'info'"
                    size="small">{{ row.signal || '-' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="20日动量%" width="120" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.mom_20)">{{ fmtPct(row.mom_20) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="related_track" label="关联赛道" min-width="120" />
        <el-table-column label="数据日" width="140">
          <template #default="{ row }">{{ tradeDateText(row.trade_date) }}</template>
        </el-table-column>
      </el-table>
      </div>
      <el-alert v-if="cmdError" :title="cmdError" type="error" :closable="false" show-icon />
      <el-empty v-if="!cmdLoading && !cmdError && !commodities.length" description="暂无商品数据" :image-size="60">
        <template #description>
          <p>暂无商品数据,请先在任务中心运行「{{ taskName('commodities') }}」后可见</p>
          <p class="muted" style="font-size:12px">商品周期 16:20 落库;仅作赛道确认参考,不作入场依据</p>
        </template>
      </el-empty>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import api from '../api'
import { fmtNum, fmtPct, upDownClass } from '../utils/fmt'
import { taskName } from '../utils/tasks'

const indexes = ref([])
const commodities = ref([])
const ixLoading = ref(false)
const cmdLoading = ref(false)
const statLoading = ref(true)
const ixError = ref('')
const cmdError = ref('')
const statError = ref('')
const flowError = ref('')
const histError = ref('')

// 市场总览
const stat = ref({})
const hasStat = computed(() => stat.value && Object.keys(stat.value).length > 0
  && (stat.value.advance != null || stat.value.amount_total != null))
const limitText = computed(() => `${stat.value.limit_up ?? '-'} / ${stat.value.limit_down ?? '-'}`)
const amountText = computed(() => stat.value.amount_total != null
  ? fmtNum(Number(stat.value.amount_total)) : '-')
const statOffSession = computed(() => outsideSession(stat.value.snap_time))
const statAsOfText = computed(() => asOfText(stat.value.snap_time))

// 时间口径:snap_time / trade_date 均为北京时间;指数日线是收盘值(15:00)
const latestIndexDate = computed(() => {
  const days = indexes.value.map((r) => String(r.trade_date || '').slice(0, 10)).filter(Boolean)
  return days.length ? days.sort().at(-1) : ''
})
const indexAsOfText = computed(() => latestIndexDate.value
  ? `${latestIndexDate.value} 收盘` : '暂无数据')
const commodityDate = computed(() => String(commodities.value[0]?.trade_date || '').slice(0, 10))

// 板块资金
const boardType = ref('industry')
const flows = ref([])
const flowLoading = ref(false)
const flowSel = ref('')
const flowHist = ref([])
const histLoading = ref(false)
let seq = 0
let flowSeq = 0
let histSeq = 0

// 指数来源:东财含成交额,腾讯/新浪仅点位与涨跌
const SOURCE_LABELS = { em: '东财', tx: '腾讯', sina: '新浪' }
function sourceLabel(s) { return SOURCE_LABELS[s] || s || '-' }
function flowSourceLabel(s) {
  return ({ eastmoney: '东财', em: '东财', sina: '新浪' })[s] || s || '-'
}

// 时间展示:后端返回 'YYYY-MM-DDTHH:mm:ss'(北京时间),拆分日期/时间并标注盘前/盘后采集
function splitDateTime(v) {
  const m = String(v || '').replace('T', ' ').match(/^(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?/)
  return { date: m?.[1] || '', time: m?.[2] || '' }
}
function dateOf(v) { return splitDateTime(v).date || '-' }
function outsideSession(v) {
  const phase = sessionPhase(v)
  return phase === 'preopen' || phase === 'afterclose'
}
// 采集时点:盘前/盘中/盘后;盘后值即当日收盘累计,盘前值仍是上一交易日
function sessionPhase(v) {
  const { time } = splitDateTime(v)
  if (!time) return ''
  const [h, m] = time.split(':').map(Number)
  const minutes = h * 60 + m
  if (minutes < 9 * 60 + 30) return 'preopen'
  if (minutes > 15 * 60) return 'afterclose'
  return 'intraday'
}
function asOfText(v) {
  const { date, time } = splitDateTime(v)
  if (!date) return '暂无数据'
  const phase = sessionPhase(v)
  if (phase === 'preopen') return `${date} 盘前采集 ${time}`
  if (phase === 'afterclose') return `${date} 收盘值(采集 ${time})`
  return `${date} 快照 ${time}`
}
function offSessionLabel(v) {
  return sessionPhase(v) === 'preopen' ? '盘前采集' : '盘后采集'
}
function tradeDateText(v) {
  const s = String(v || '').slice(0, 10)
  if (!s) return '-'
  const d = new Date(`${s}T00:00:00Z`)
  if (Number.isNaN(d.getTime())) return s
  return `${s} 周${'日一二三四五六'[d.getUTCDay()]}`
}

const flowSnap = computed(() => flows.value[0]?.snap_time || '')
const flowDateText = computed(() => dateOf(flowSnap.value))
const flowAsOfText = computed(() => asOfText(flowSnap.value))
const flowOffSession = computed(() => outsideSession(flowSnap.value))
const flowStale = computed(() => !!flowDateText.value && flowDateText.value !== '-'
  && !!latestIndexDate.value && flowDateText.value < latestIndexDate.value)

function barH(v) {
  const xs = flowHist.value.map((h) => Math.abs(Number(h.net_inflow) || 0))
  const mx = Math.max(...xs, 1)
  return 4 + Math.round((Math.abs(Number(v) || 0) / mx) * 56)
}

function clearHistory() {
  histSeq += 1
  flowSel.value = ''
  flowHist.value = []
  histError.value = ''
  histLoading.value = false
}

async function loadFlow() {
  const cur = (flowSeq += 1)
  clearHistory()
  flows.value = []
  flowError.value = ''
  flowLoading.value = true
  try {
    const data = await api.get('/api/screener/fund-flow',
      { params: { board_type: boardType.value, limit: 20 }, silent: true })
    if (cur !== flowSeq) return
    flows.value = Array.isArray(data) ? data : []
  } catch {
    if (cur === flowSeq) flowError.value = '板块资金加载失败，请点击刷新数据重试'
  } finally {
    if (cur === flowSeq) flowLoading.value = false
  }
}

async function onFlowRow(row) {
  clearHistory()
  flowSel.value = row.board_name
  const cur = (histSeq += 1)
  histLoading.value = true
  try {
    const data = await api.get('/api/screener/fund-flow/history',
      { params: { board_name: row.board_name, board_code: row.board_code || undefined,
                  board_type: boardType.value, days: 30 }, silent: true })
    if (cur !== histSeq) return
    flowHist.value = Array.isArray(data) ? data : []
  } catch {
    if (cur === histSeq) histError.value = '资金历史加载失败，请重新点击板块重试'
  } finally {
    if (cur === histSeq) histLoading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => { seq += 1; flowSeq += 1; histSeq += 1 })

async function load() {
  const cur = (seq += 1)
  ixLoading.value = cmdLoading.value = statLoading.value = true
  ixError.value = cmdError.value = statError.value = ''
  const flowPromise = loadFlow()
  try {
    const [ix, cmd, ms] = await Promise.allSettled([
      api.get('/api/screener/indexes', { silent: true }),
      api.get('/api/screener/commodities', { silent: true }),
      api.get('/api/screener/market-stat/latest', { silent: true }),
    ])
    if (cur !== seq) return // 竞态:丢弃过期响应
    indexes.value = ix.status === 'fulfilled' && Array.isArray(ix.value) ? ix.value : []
    commodities.value = cmd.status === 'fulfilled' && Array.isArray(cmd.value) ? cmd.value : []
    stat.value = ms.status === 'fulfilled' && ms.value && typeof ms.value === 'object'
      && !Array.isArray(ms.value) ? ms.value : {}
    if (ix.status === 'rejected') ixError.value = '指数加载失败，请点击刷新数据重试'
    if (cmd.status === 'rejected') cmdError.value = '商品周期加载失败，请点击刷新数据重试'
    if (ms.status === 'rejected') statError.value = '市场总览加载失败，请点击刷新数据重试'
  } finally {
    if (cur === seq) ixLoading.value = cmdLoading.value = statLoading.value = false
  }
  await flowPromise
}
</script>

<style scoped>
.market-status { display: flex; align-items: center; flex-wrap: wrap; gap: 14px;
  padding: 10px 14px; margin-bottom: 6px; background: #fff;
  border: 1px solid #ebeef5; border-radius: 6px; }
.status-item { display: flex; align-items: center; gap: 8px; }
.status-label { color: #909399; font-size: 12px; }
.status-item strong { font-size: 14px; font-variant-numeric: tabular-nums; }
.market-note { font-size: 12px; margin: 0 0 12px; }
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.native-mini { border-collapse: collapse; font-size: 12px; min-width: 280px; }
.native-mini th, .native-mini td { border: 1px solid #ebeef5; padding: 4px 8px; }
.native-mini th { background: #f5f7fa; font-weight: 600; }
.mini-bars { display: flex; align-items: flex-end; gap: 3px; min-height: 64px;
  padding: 8px 4px 2px; border: 1px solid #ebeef5; border-radius: 4px; background: #fafbfc; }
.mini-bar { width: 10px; border-radius: 2px 2px 0 0; cursor: default; }
.mini-bar.pos { background: #f56c6c; }
.mini-bar.neg { background: #67c23a; }
</style>
