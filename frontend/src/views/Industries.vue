<template>
  <div>
    <!-- 顶部状态条:数据日 + 指标覆盖度 + 刷新 -->
    <div class="ind-status">
      <div class="status-item">
        <span class="status-label">行业数据日</span>
        <strong>{{ asOfText }}</strong>
        <el-tag v-if="summary.stale" size="small" effect="plain" type="warning">
          早于最新交易日 {{ summary.expected }}
        </el-tag>
      </div>
      <div class="status-item">
        <span class="status-label">指标覆盖</span>
        <el-tooltip v-for="c in coverageTags" :key="c.key" :content="c.hint" placement="top">
          <el-tag size="small" effect="plain" :type="c.ok ? 'success' : 'info'">
            {{ c.label }}·{{ c.ok ? '可用' : '积累中' }}
          </el-tag>
        </el-tooltip>
      </div>
      <span class="grow" />
      <el-button size="small" :loading="loading || radarLoading" @click="refreshAll">刷新数据</el-button>
    </div>
    <p class="ind-note muted">
      时间均为北京时间;行业截面由收盘后 16:15「{{ taskName('industry_daily') }}」落库,展示最近一个收盘日,非实时行情。
    </p>

    <el-alert v-if="loadError" type="error" :closable="false" show-icon :title="loadError">
      <template #default><el-button size="small" @click="refreshAll">重试</el-button></template>
    </el-alert>
    <el-alert v-else-if="summary.stale" type="warning" :closable="false" show-icon
              :title="`行业数据日 ${summary.as_of} 早于最新交易日 ${summary.expected},请在任务中心运行「${taskName('industry_daily')}」更新`" />
    <el-alert v-if="degraded" type="info" :closable="false" show-icon
              :title="`当前为降级口径(东财行业日线不可达 → 新浪行业快照):动量需累计 22/62 个交易日历史(已积累 ${summary.history_days} 天),拥挤度需 60 天,资金维度暂不可用;表中「积累中 / -」是口径缺失,不是 0`" />

    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>新兴赛道雷达</span>
          <el-tag v-if="radarAsOf" size="small" effect="plain">数据日 {{ radarAsOf }}</el-tag>
          <span class="muted hide-mobile" style="font-size:12px">启动期优先 · 识别而非预测</span>
          <span class="grow" />
          <el-button size="small" link :loading="radarLoading" @click="loadRadar">刷新雷达</el-button>
        </div>
      </template>
      <p class="radar-hint muted">
        排序 = 0.4×阶段(启动 1 / 发酵 0.6) + 0.35×动量加速分位 + 0.25×新闻突增;启动期 = mom60 低于全行业中位且 mom20 转正、且不在 SC-U08 回避名单内。SC-U08 拥挤行业硬置底。
      </p>
      <div class="table-scroll">
        <el-table v-if="radar.length" :data="radar" size="small" v-loading="radarLoading" stripe border
                  :row-class-name="radarRowClass">
          <el-table-column prop="name" label="行业/赛道" min-width="130" :fixed="isMobile ? false : 'left'" show-overflow-tooltip />
          <el-table-column label="阶段" width="150">
            <template #default="{ row }">
              <el-tag :type="stageType(row.stage)" size="small">{{ row.stage_text }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="20日动量%" width="110" align="right">
            <template #default="{ row }">
              <span v-if="!momReady" class="muted">积累中</span>
              <span v-else :class="upDownClass(row.mom_20)">{{ fmtPct(row.mom_20) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="60日动量%" width="110" align="right">
            <template #default="{ row }">
              <span v-if="!momReady" class="muted">积累中</span>
              <span v-else :class="upDownClass(row.mom_60)">{{ fmtPct(row.mom_60) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="动量加速" width="100" align="right" class-name="hide-mobile">
            <template #default="{ row }">
              <span v-if="!momReady" class="muted">积累中</span>
              <span v-else :class="upDownClass(row.accel)" style="font-weight:600">{{ fmtPct(row.accel) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="拥挤度" width="100" align="right" class-name="hide-mobile">
            <template #default="{ row }">
              <span v-if="!crowdingReady" class="muted">-</span>
              <span v-else :class="row.ratio_score > 300 ? 'up' : ''">{{ fmtNum(row.ratio_score, 1) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="新闻3日/突增" width="115" align="right">
            <template #header>
              <el-tooltip content="新闻按赛道/概念标签匹配(如 AI算力/电机),与行业分类名不同体系;无匹配即 0 条" placement="top">
                <span>新闻3日/突增</span>
              </el-tooltip>
            </template>
            <template #default="{ row }">
              {{ row.news_3d }} 条 / <span :class="row.news_surge >= 2 ? 'up' : ''">×{{ fmtNum(row.news_surge, 1) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="雷达分" width="90" sortable prop="score" align="right">
            <template #default="{ row }">{{ fmtNum(row.score, 2) }}</template>
          </el-table-column>
          <el-table-column v-if="canOperate" label="操作" width="130" :fixed="isMobile ? false : 'right'">
            <template #default="{ row }">
              <el-button v-if="!row.tracked" size="small" link type="primary" @click="adopt(row)">加入赛道跟踪</el-button>
              <el-tag v-else size="small" type="success">已跟踪</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <p v-if="radar.length && newsTotal === 0" class="radar-hint muted">
        近 10 日新闻暂无与行业名匹配的条目(新闻只打赛道/概念标签),因此新闻维度对本次排序无贡献。
      </p>
      <el-empty v-if="!radarLoading && !radar.length" description="暂无雷达数据">
        <template #description>
          <p>暂无雷达数据</p>
          <p class="muted" style="font-size:12px">先运行「{{ taskName('industry_daily') }}」任务生成行业截面后可见</p>
        </template>
      </el-empty>
    </el-card>

    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>行业景气榜(截面相对分位)</span>
          <el-tag v-if="summary.as_of" size="small" effect="plain">数据日 {{ summary.as_of }}</el-tag>
          <el-tag v-if="summary.avoid_count" size="small" effect="plain" type="danger">回避 {{ summary.avoid_count }} 个</el-tag>
          <span class="grow" />
          <el-button v-if="canOperate" size="small" type="primary" link :loading="syncingIndustry"
                     @click="syncIndustry">立即同步行业数据</el-button>
        </div>
      </template>
      <template v-if="rows.length">
        <div class="table-scroll">
          <el-table :data="rows" size="small" v-loading="loading" max-height="480" stripe border
                    :cell-style="cellStyle">
            <el-table-column prop="industry_name" label="行业" min-width="120" :fixed="isMobile ? false : 'left'" show-overflow-tooltip />
            <el-table-column label="景气评分" sortable prop="heat_score" width="110" align="right">
              <template #default="{ row }">{{ fmtNum(row.heat_score, 1) }}</template>
            </el-table-column>
            <el-table-column label="20日动量%" sortable prop="mom_20" width="115" align="right">
              <template #default="{ row }">
                <span v-if="!momReady" class="muted">积累中</span>
                <span v-else :class="upDownClass(row.mom_20)">{{ fmtPct(row.mom_20) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="60日动量%" sortable prop="mom_60" width="115" align="right" class-name="hide-mobile">
              <template #default="{ row }">
                <span v-if="!momReady" class="muted">积累中</span>
                <span v-else :class="upDownClass(row.mom_60)">{{ fmtPct(row.mom_60) }}</span>
              </template>
            </el-table-column>
            <el-table-column sortable prop="ratio_score" width="120" align="right">
              <template #header>
                <el-tooltip content="RATIO(行业成交额/全市场)偏离自身 MA120 > 300% → 触发 SC-U08 20 交易日回避" placement="top">
                  <span>拥挤度偏离</span>
                </el-tooltip>
              </template>
              <template #default="{ row }">
                <span v-if="!crowdingReady" class="muted">-</span>
                <span v-else :class="row.ratio_score > 300 ? 'up' : ''" style="font-weight:600">{{ fmtNum(row.ratio_score, 1) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="主力净流入(亿)" sortable prop="fund_flow" width="135" align="right">
              <template #header>
                <el-tooltip content="东财板块今日主力净流入;降级快照下取不到该字段" placement="top">
                  <span>主力净流入(亿)</span>
                </el-tooltip>
              </template>
              <template #default="{ row }">
                <!-- 0 为真实零值着色显示;null/'' 或维度不可用显示 '-',与 0 严格区分 -->
                <span v-if="!flowReady" class="muted">-</span>
                <span v-else-if="row.fund_flow === null || row.fund_flow === undefined || row.fund_flow === ''" class="muted">-</span>
                <span v-else :class="upDownClass(row.fund_flow)">{{ fmtNum(row.fund_flow) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="连续流入(天)" sortable prop="flow_streak" width="115" align="right" class-name="hide-mobile">
              <template #default="{ row }">
                <span v-if="!flowReady" class="muted">-</span>
                <span v-else>{{ fmtInt(row.flow_streak) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="当日涨跌%" width="105" align="right">
              <template #default="{ row }">
                <span :class="upDownClass(row.pct_chg)">{{ fmtPct(row.pct_chg) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="口径" width="140" class-name="hide-mobile">
              <template #header>
                <el-tooltip content="景气评分与动量的计算口径:历史滚动 / 降级快照(首日兜底) / 估值维度降权" placement="top">
                  <span>口径</span>
                </el-tooltip>
              </template>
              <template #default>
                <el-tooltip :content="heatModeHint" placement="top">
                  <el-tag :type="heatModeTag.type" size="small">{{ heatModeTag.label }}</el-tag>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column label="状态" min-width="120">
              <template #default="{ row }">
                <el-tag v-if="row.avoid_until" type="danger" size="small">回避至{{ row.avoid_until }}</el-tag>
                <el-tag v-else type="success" size="small">正常</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>
      <el-empty v-if="!loading && !rows.length" description="暂无行业景气数据">
        <template #description>
          <p>暂无行业景气数据</p>
          <p class="muted" style="font-size:12px;line-height:1.6">
            行业数据来自每日 16:15「{{ taskName('industry_daily') }}」任务;<br />
            若东财行业日线接口不可达,会自动降级为新浪行业快照(仅点位与涨跌,动量/资金维度需逐日积累)。
          </p>
        </template>
        <el-button v-if="canOperate" type="primary" size="small" :loading="syncingIndustry"
                   @click="syncIndustry">立即同步</el-button>
      </el-empty>
    </el-card>

    <el-card class="page-card" id="track-mgmt">
      <template #header>
        <div class="card-head">
          <span>赛道管理</span>
          <el-tag v-if="tracks.length" size="small" effect="plain">成分合计 {{ memberTotal }} 只</el-tag>
          <span class="grow" />
          <el-button v-if="canOperate" size="small" @click="backfill" :loading="backfilling">一键回填</el-button>
          <el-button v-if="canOperate" size="small" @click="showAdd = true">新增赛道</el-button>
        </div>
      </template>
      <p class="radar-hint muted">成分优先取东财概念板块;东财不可达时降级为同花顺核验 / 本地展开(数量可能偏少),网络恢复后点「同步成分」可补全。</p>
      <el-alert v-if="localSourcedTracks" type="info" :closable="false" show-icon
                :title="`${localSourcedTracks} 个赛道的成分来自本地降级源,数量可能偏少;网络恢复后逐条「同步成分」即可补全`" />
      <div class="table-scroll">
        <el-table :data="tracks" size="small" stripe border>
          <el-table-column prop="name" label="赛道" min-width="110" show-overflow-tooltip />
          <el-table-column prop="concept_board" label="东财概念板块" min-width="130" show-overflow-tooltip />
          <el-table-column label="新闻关键词" show-overflow-tooltip class-name="hide-mobile">
            <template #default="{ row }">{{ row.keywords || '-' }}</template>
          </el-table-column>
          <el-table-column prop="member_count" label="成分" width="110" align="right">
            <template #default="{ row }">
              <template v-if="Number(row.member_count) > 0">{{ fmtInt(row.member_count) }}</template>
              <el-tooltip v-else-if="row.sync_error" :content="row.sync_error" placement="top">
                <span style="color:#f56c6c">同步失败</span>
              </el-tooltip>
              <span v-else class="muted">未同步</span>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="110">
            <template #header>
              <el-tooltip content="东财=概念板块成分;同花顺核验/本地=东财不可达时的降级来源" placement="top">
                <span>来源</span>
              </el-tooltip>
            </template>
            <template #default="{ row }">
              <span :class="String(row.member_source || '').startsWith('local') ? 'muted' : ''">{{ sourceLabel(row.member_source) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="同步时间" width="130" class-name="hide-mobile">
            <template #default="{ row }">{{ fmtDT(row.synced_at) }}</template>
          </el-table-column>
          <el-table-column label="启用" width="80">
            <template #default="{ row }">
              <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="canOperate" label="操作" min-width="130">
            <template #default="{ row }">
              <el-button size="small" link @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
              <el-button size="small" link type="primary" :loading="row._syncing" @click="sync(row)">同步成分</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>

    <el-dialog v-model="showAdd" title="新增赛道(如:电机/AI硬件)" width="480">
      <el-form label-width="110px">
        <el-form-item label="名称"><el-input v-model="newTrack.name" placeholder="必填" /></el-form-item>
        <el-form-item label="东财概念板块"><el-input v-model="newTrack.concept_board" placeholder="与东财板块名一致" /></el-form-item>
        <el-form-item label="新闻关键词"><el-input v-model="newTrack.keywords" placeholder="逗号分隔" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAdd = false">取消</el-button>
        <el-button type="primary" @click="add">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { fmtDT, fmtInt, fmtNum, fmtPct, upDownClass } from '../utils/fmt'
import { taskName } from '../utils/tasks'
import { useUserStore } from '../store'

const store = useUserStore()
const canOperate = computed(() => store.isAnalyst)
const isMobile = ref(window.matchMedia('(max-width: 768px)').matches)

const rows = ref([])
const tracks = ref([])
const radar = ref([])
const radarLoading = ref(false)
const loading = ref(false)
const loadError = ref('')
const showAdd = ref(false)
const syncingIndustry = ref(false)
const backfilling = ref(false)
const newTrack = ref({ name: '', concept_board: '', keywords: '' })
let loadSeq = 0
let radarSeq = 0
let syncTimers = []

// 行业截面元信息:数据日/覆盖度/历史积累天数。后端 /industries/summary 不可用时
// 用行内数据兜底推导,保证旧后端也能渲染(降级判断不致误报"0"为真值)。
const EMPTY_COVERAGE = { momentum: false, flow: false, crowding: false, valuation: false }
const summary = ref({ as_of: '', rows: 0, history_days: 0, avoid_count: 0,
  expected: '', stale: false, heat_mode: 'empty', coverage: { ...EMPTY_COVERAGE } })

onMounted(() => {
  const mq = window.matchMedia('(max-width: 768px)')
  const onMq = (e) => { isMobile.value = e.matches }
  mq.addEventListener?.('change', onMq)
  refreshAll()
})
onBeforeUnmount(() => {
  loadSeq += 1
  radarSeq += 1
  syncTimers.forEach(clearTimeout)
  syncTimers = []
})

function deriveSummary(list) {
  const asOf = String(list[0]?.trade_date || '').slice(0, 10)
  const coverage = {
    momentum: list.some((r) => Math.abs(Number(r.mom_20) || 0) > 1e-9 || Math.abs(Number(r.mom_60) || 0) > 1e-9),
    flow: list.some((r) => Math.abs(Number(r.fund_flow) || 0) > 1e-9),
    crowding: list.some((r) => Math.abs(Number(r.ratio_score) || 0) > 1e-9),
    valuation: false,
  }
  return {
    as_of: asOf, rows: list.length, history_days: 0,
    avoid_count: list.filter((r) => r.avoid_until).length,
    expected: '', stale: false,
    heat_mode: list.length ? (coverage.momentum ? 'partial' : 'degraded') : 'empty',
    coverage,
  }
}

async function load() {
  const seq = (loadSeq += 1)
  loading.value = true
  loadError.value = ''
  try {
    const [ind, meta, trk] = await Promise.allSettled([
      api.get('/api/screener/industries', { silent: true }),
      api.get('/api/screener/industries/summary', { silent: true }),
      api.get('/api/screener/tracks', { silent: true }),
    ])
    if (seq !== loadSeq) return // 竞态:丢弃过期响应
    rows.value = ind.status === 'fulfilled' && Array.isArray(ind.value) ? ind.value : []
    tracks.value = trk.status === 'fulfilled' && Array.isArray(trk.value) ? trk.value : []
    const m = meta.status === 'fulfilled' && meta.value && typeof meta.value === 'object'
      ? meta.value : null
    summary.value = m
      ? { ...deriveSummary(rows.value), ...m, coverage: { ...EMPTY_COVERAGE, ...(m.coverage || {}) } }
      : deriveSummary(rows.value)
    if (ind.status === 'rejected' || trk.status === 'rejected') {
      loadError.value = '行业/赛道数据加载失败,请点击重试'
    }
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function loadRadar() {
  const seq = (radarSeq += 1)
  radarLoading.value = true
  try {
    const data = await api.get('/api/screener/emerging', { params: { top: 12 }, silent: true })
    if (seq !== radarSeq) return
    // 后端降级首日的动量是 0(不是 proxy):前端统一按 summary.coverage.momentum
    // 显示「积累中」,不再依赖 stage_text 文案做特判。
    radar.value = Array.isArray(data) ? data : []
  } finally {
    if (seq === radarSeq) radarLoading.value = false
  }
}

async function refreshAll() {
  await Promise.all([load(), loadRadar()])
}

const momReady = computed(() => !!summary.value.coverage?.momentum)
const flowReady = computed(() => !!summary.value.coverage?.flow)
const crowdingReady = computed(() => !!summary.value.coverage?.crowding)
const degraded = computed(() => rows.value.length > 0
  && (summary.value.heat_mode === 'degraded' || !momReady.value))
const asOfText = computed(() => summary.value.as_of
  || String(rows.value[0]?.trade_date || '').slice(0, 10) || '暂无数据')
const radarAsOf = computed(() => String(radar.value[0]?.as_of || '').slice(0, 10))
const newsTotal = computed(() => radar.value.reduce((s, r) => s + (Number(r.news_3d) || 0), 0))
const memberTotal = computed(() => tracks.value.reduce((s, t) => s + (Number(t.member_count) || 0), 0))
const localSourcedTracks = computed(() => tracks.value.filter((t) =>
  String(t.member_source || '').startsWith('local') && Number(t.member_count) > 0).length)

const coverageTags = computed(() => [
  { key: 'momentum', label: '动量', ok: momReady.value, hint: '20/60 日动量需累计 22/62 个交易日历史,降级快照下逐日增厚' },
  { key: 'crowding', label: '拥挤度', ok: crowdingReady.value, hint: 'RATIO 偏离自身 MA120,需累计 60 个交易日' },
  { key: 'flow', label: '资金', ok: flowReady.value, hint: '东财板块今日主力净流入;降级快照取不到,显示 -' },
  { key: 'valuation', label: '估值', ok: !!summary.value.coverage?.valuation, hint: '行业 PE 分位暂无数据源,景气评分中估值维度已降权' },
])

const heatModeTag = computed(() => ({
  degraded: { label: '降级快照', type: 'warning' },
  partial: { label: '历史滚动·估值降权', type: 'info' },
  ok: { label: '历史滚动', type: 'success' },
  empty: { label: '无数据', type: 'info' },
}[summary.value.heat_mode] || { label: '历史滚动', type: 'info' }))

const heatModeHint = computed(() => {
  if (summary.value.heat_mode === 'degraded') {
    return '降级口径:景气评分用当日涨跌幅+成交占比兜底,与完整公式不可比;历史页逐日补齐动量后自动切换'
  }
  if (summary.value.heat_mode === 'partial') {
    return '动量/资金按库内历史滚动计算;行业 PE 分位缺失,景气评分中估值权重归零'
  }
  return '动量/资金/估值均按库内截面分位计算'
})

// 景气评分着色:按当日截面 min-max 归一,高景气偏红(与 A 股红涨口径一致)
const heatBounds = computed(() => {
  const xs = rows.value.map((r) => Number(r.heat_score)).filter((v) => Number.isFinite(v))
  if (!xs.length) return { min: 0, max: 0 }
  return { min: Math.min(...xs), max: Math.max(...xs) }
})

function cellStyle({ row, column }) {
  if (column.property !== 'heat_score') return {}
  const v = Number(row.heat_score)
  if (!Number.isFinite(v)) return {}
  const { min, max } = heatBounds.value
  const ratio = max > min ? (v - min) / (max - min) : 0.5
  return {
    background: `rgba(245, 108, 108, ${(0.04 + 0.22 * ratio).toFixed(3)})`,
    fontWeight: ratio > 0.66 ? 600 : 400,
  }
}

function radarRowClass({ row }) {
  return row.stage === 'avoid' ? 'row-avoid' : ''
}

function stageType(s) {
  return { starting: 'danger', rising: 'warning', watch: 'info', avoid: 'success' }[s] || 'info'
}

// member_source 中文:concept_board 东财 / concept_board_sina 新浪 / keyword 关键词 /
// concept_board_ths 同花顺核验 / concept_board_local 本地 / eastmoney 东财 /
// sina 新浪 / ths 同花顺核验 / local 本地 / local_ths_verified 同花顺核验(- 为空)
function sourceLabel(s) {
  return { concept_board: '东财', concept_board_sina: '新浪', keyword: '关键词',
    concept_board_ths: '同花顺核验', concept_board_local: '本地',
    eastmoney: '东财', sina: '新浪', ths: '同花顺核验', local: '本地',
    local_ths_verified: '同花顺核验' }[s] || '-'
}

// 把雷达发现的行业一键纳入赛道管理(成分股在东财可用环境同步)
// 雷达行只有行业名(东财概念板块映射需人工补填),因此统一提示补填后再同步成分
async function adopt(row) {
  await api.post('/api/screener/tracks', {
    name: row.name, concept_board: row.concept_board || '', keywords: row.name, enabled: true,
    note: `新兴赛道雷达采纳(${row.stage_text})`,
  })
  ElMessage.success(row.concept_board
    ? `已把「${row.name}」加入赛道管理,可点击同步成分`
    : `已把「${row.name}」加入赛道管理;请在赛道管理中补填东财概念板块后再同步成分`)
  await refreshAll()
}

async function syncIndustry() {
  syncingIndustry.value = true
  try {
    const r = await api.post('/api/screener/tasks/industry_daily/run')
    ElMessage.success(r.message || '已提交后台执行')
    // 后台执行完毕前再拉取两次结果;定时器可取消,卸载时清理
    syncTimers.forEach(clearTimeout)
    syncTimers = [
      setTimeout(() => refreshAll(), 8000),
      setTimeout(() => refreshAll(), 25000),
    ]
  } finally {
    syncingIndustry.value = false
  }
}

async function toggle(row) {
  const prev = row.enabled
  row.enabled = !prev
  try {
    await api.put(`/api/screener/tracks/${row.id}`, { name: row.name, concept_board: row.concept_board,
      industry_index: row.industry_index, keywords: row.keywords, enabled: row.enabled, note: row.note || '' })
  } catch {
    row.enabled = prev // 失败回滚开关,避免 UI 与服务端不一致(API 层已全局提示错误)
  }
}

async function sync(row) {
  row._syncing = true
  try {
    const r = await api.post(`/api/screener/tracks/${row.id}/sync`)
    const added = r?.added ?? 0
    const src = sourceLabel(r?.source)
    ElMessage.success(`新增成分 ${added} 只(来源${src})`)
    await load()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '同步失败'
    ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  } finally {
    row._syncing = false
  }
}

async function backfill() {
  backfilling.value = true
  try {
    // 后端已改后台线程执行(7 赛道×多源×重试最坏分钟级):接口立即返回 queued,
    // 结果看任务日志(tracks_backfill)与赛道列表,这里延迟刷新两轮
    const r = await api.post('/api/screener/tracks/backfill')
    if (r?.queued) {
      ElMessage.success(r.message || '已提交后台执行')
      syncTimers.forEach(clearTimeout)
      syncTimers = [
        setTimeout(() => load(), 15000),
        setTimeout(() => load(), 45000),
      ]
    } else {
      // 兼容旧同步返回结构
      const results = r?.results || {}
      const names = Object.keys(results)
      const total = names.reduce((s, k) => s + Number(results[k]?.added || 0), 0)
      const errs = r?.errors || []
      if (errs.length) ElMessage.warning(`回填完成:${names.length} 赛道新增 ${total} 只,部分失败:${errs.slice(0, 2).join(';')}`)
      else ElMessage.success(`回填完成:${names.length} 赛道新增 ${total} 只`)
      await load()
    }
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '回填失败'
    ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  } finally {
    backfilling.value = false
  }
}

async function add() {
  if (!newTrack.value.name.trim()) return ElMessage.warning('请填写赛道名称')
  await api.post('/api/screener/tracks', {
    name: newTrack.value.name,
    concept_board: newTrack.value.concept_board,
    keywords: newTrack.value.keywords,
  })
  showAdd.value = false
  ElMessage.success('已新增,点击「同步成分」拉取成分股')
  await load()
}
</script>

<style scoped>
/* 375px 下表格(约1035px)不溢出:容器横滚 + 优先列保留,其余列省略 tooltip */
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.ind-status { display: flex; align-items: center; flex-wrap: wrap; gap: 14px;
  padding: 10px 14px; margin-bottom: 6px; background: #fff;
  border: 1px solid #ebeef5; border-radius: 6px; }
.status-item { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.status-label { color: #909399; font-size: 12px; }
.status-item strong { font-size: 14px; font-variant-numeric: tabular-nums; }
.ind-note { font-size: 12px; margin: 0 0 12px; }
.radar-hint { font-size: 12px; margin: 0 0 8px; line-height: 1.6; }
:deep(.row-avoid td) { background: #fdf6ec !important; }
@media (max-width: 768px) {
  .hide-mobile { display: none !important; }
}
</style>
