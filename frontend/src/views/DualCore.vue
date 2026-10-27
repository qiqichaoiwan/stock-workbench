<template>
  <div class="dual-core">
    <!-- 打印页头:屏幕上隐藏 -->
    <div class="print-only print-head">
      <h2>双核共振 · 主升浪狙击战法 每日交易检查清单</h2>
      <div class="print-meta">
        <span>日期:{{ asOfText }}</span>
        <span>今日底色:{{ verdict.level || '-' }}</span>
        <span>仓位上限:{{ verdict.position_text || '-' }}</span>
        <span>883994 创新高:{{ bNewHighText }}</span>
      </div>
    </div>

    <!-- 顶部状态条 -->
    <div class="dc-status no-print">
      <div class="status-item">
        <span class="status-label">数据日</span>
        <strong>{{ asOfText }}</strong>
        <el-tag size="small" effect="plain" type="info">同花顺指数</el-tag>
      </div>
      <div class="status-item">
        <span class="status-label">今日底色</span>
        <el-tag size="small" :type="levelTagType(verdict.level)">{{ verdict.level || '-' }}</el-tag>
        <el-tag v-if="ready" size="small" effect="plain">仓位 {{ verdict.position_text }}</el-tag>
        <el-tag v-if="verdict.veto" size="small" type="danger" effect="dark">一票否决</el-tag>
      </div>
      <span class="grow" />
      <el-button size="small" :loading="loading" @click="load">刷新数据</el-button>
      <el-button v-if="canOperate" size="small" type="primary" :loading="syncing" @click="syncData">立即同步</el-button>
      <el-button size="small" :disabled="!ready" @click="printCard">打印清单</el-button>
    </div>
    <p class="dc-note muted no-print">
      数据来自同花顺指数日线(883957 同花顺全A / 883994 昨日打首板表现),收盘后 16:50 由「{{ taskName('dual_core') }}」落库;
      周线由日线聚合,非实时行情。自动项按收盘价判定,人工项与复盘按用户逐日保存。
    </p>

    <el-alert v-if="loadError" class="no-print" type="error" :closable="false" show-icon :title="loadError">
      <template #default><el-button size="small" @click="load">重试</el-button></template>
    </el-alert>
    <el-alert v-else-if="payload?.stale" class="no-print" type="warning" :closable="false" show-icon
              :title="`双核数据不同步:${staleText}(业务日 ${asOfText})。判定基于各核最新收盘,请先运行「${taskName('dual_core')}」对齐`" />
    <el-alert v-else-if="!ready && !loading" class="no-print" type="info" :closable="false" show-icon
              :title="notReadyText">
      <template #default>
        <el-button v-if="canOperate" size="small" type="primary" :loading="syncing" @click="syncData">
          立即同步双核指数
        </el-button>
      </template>
    </el-alert>

    <!-- 今日结论 -->
    <el-card class="page-card verdict-card">
      <template #header>
        <div class="card-head">
          <span>今日结论 · 多周期共振判定</span>
          <span class="grow" />
          <el-tag :type="levelTagType(verdict.level)" effect="dark">{{ verdict.level || '无数据' }}</el-tag>
          <el-tag v-if="ready" size="small" effect="plain">仓位上限 {{ verdict.position_text }}</el-tag>
        </div>
      </template>
      <div class="verdict-body" v-loading="loading">
        <p class="verdict-line">
          <strong class="verdict-level" :class="levelClass(verdict.level)">{{ verdict.level || '无数据' }}</strong>
          <span v-if="verdict.veto" class="veto-badge">一票否决 · 禁止接力打板</span>
          <span class="muted">建议仓位 {{ verdict.position_text || '-' }}</span>
        </p>
        <ul v-if="verdict.reasons?.length" class="reason-list">
          <li v-for="(r, i) in verdict.reasons" :key="i">{{ r }}</li>
        </ul>
        <p v-else-if="ready" class="muted">无判定理由</p>
        <ul v-if="divNotes.length" class="div-notes">
          <li v-for="(n, i) in divNotes" :key="i">⚠️ {{ n }}</li>
        </ul>
        <div v-if="ready" class="flag-row">
          <el-tag v-for="f in flags" :key="f.label" size="small" effect="plain" :type="f.type">
            {{ f.label }}·{{ f.text }}
          </el-tag>
        </div>
      </div>
    </el-card>

    <!-- 双核多周期面板 -->
    <el-row :gutter="12">
      <el-col v-for="core in cores" :key="core.code" :xs="24" :md="12">
        <el-card class="page-card core-card">
          <template #header>
            <div class="card-head">
              <span>{{ core.name }}</span>
              <el-tag size="small" effect="plain">{{ core.code }}</el-tag>
              <span class="grow" />
              <span class="core-close" :class="upDown(core.pct_chg)">{{ fmt(core.close) }}</span>
              <span :class="upDown(core.pct_chg)">{{ fmtPct(core.pct_chg) }}</span>
            </div>
          </template>
          <div v-if="core.ready">
            <svg class="spark" viewBox="0 0 300 60" preserveAspectRatio="none" role="img"
                 :aria-label="`${core.name} 近 60 个交易日收盘走势`">
              <polyline :points="sparkPoints(core.code)" fill="none"
                        :stroke="sparkColor(core.code)" stroke-width="1.6" />
            </svg>
            <p class="spark-caption muted">近 60 个交易日收盘 · 红涨绿跌</p>
            <el-descriptions :column="descColumns" size="small" border class="core-desc">
              <el-descriptions-item label="现价">{{ fmt(core.close) }}</el-descriptions-item>
              <el-descriptions-item label="今日高/低">{{ fmt(core.high) }} / {{ fmt(core.low) }}</el-descriptions-item>
              <el-descriptions-item label="昨日高/低">{{ fmt(core.prev_high) }} / {{ fmt(core.prev_low) }}</el-descriptions-item>
              <el-descriptions-item label="历史最高(不含当日)">{{ fmt(core.ath_high) }}</el-descriptions-item>
              <el-descriptions-item label="5 日线">{{ fmt(core.ma5) }}</el-descriptions-item>
              <el-descriptions-item label="周线 5/10/20">{{ fmt(core.wma5, 2) }} / {{ fmt(core.wma10, 2) }} / {{ fmt(core.wma20, 2) }}</el-descriptions-item>
              <el-descriptions-item label="本周涨跌">{{ fmtPct(core.w_pct) }}</el-descriptions-item>
              <el-descriptions-item label="周线 MACD">DIF {{ fmt(core.w_macd_dif, 3) }} · DEA {{ fmt(core.w_macd_dea, 3) }}</el-descriptions-item>
            </el-descriptions>
            <div class="state-row">
              <el-tag v-for="t in coreTags(core)" :key="t.text" size="small" effect="plain" :type="t.type">
                {{ t.text }}
              </el-tag>
            </div>
            <p class="core-cap muted">日线 {{ core.bars }} 根 · 周线 {{ core.weekly_bars }} 根</p>
          </div>
          <el-empty v-else description="该指数历史数据不足" :image-size="48" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 四步清单 -->
    <el-card class="page-card checklist-card">
      <template #header>
        <div class="card-head">
          <span>每日交易检查清单(文档二 · 打印卡)</span>
          <el-tag v-if="ready" size="small" effect="plain">业务日 {{ asOfText }}</el-tag>
          <span class="grow" />
          <span class="muted save-hint no-print">{{ saveHint }}</span>
          <el-button class="no-print" size="small" :loading="saving" :disabled="!ready" @click="saveNow">保存清单</el-button>
        </div>
      </template>
      <div v-for="s in steps" :key="s.step" class="step-block">
        <h4 class="step-title"><span class="step-no">{{ s.step }}</span>{{ s.title }}</h4>
        <div v-for="c in s.items" :key="c.key" class="check-row"
             :class="{ 'check-veto': c.veto, 'check-fail': c.auto && c.pass === false, 'check-ok': c.auto && c.pass === true }">
          <div class="check-state">
            <template v-if="c.auto">
              <span class="state-icon" :class="passClass(c.pass)">{{ passIcon(c.pass) }}</span>
            </template>
            <template v-else>
              <span class="print-only manual-box">{{ items[c.key] ? '☑' : '☐' }}</span>
              <el-checkbox v-model="items[c.key]" class="no-print" :disabled="!ready" />
            </template>
          </div>
          <div class="check-body">
            <div class="check-label">
              {{ c.label }}
              <el-tag v-if="c.veto" size="small" type="danger" effect="plain">一票否决</el-tag>
              <el-tag v-else-if="!c.auto" class="manual-tag" size="small" type="info" effect="plain">人工项</el-tag>
            </div>
            <div v-if="c.value || c.hint" class="check-meta">
              <span v-if="c.value" class="check-value">{{ c.value }}</span>
              <span v-if="c.hint" class="muted check-hint">{{ c.hint }}</span>
            </div>
            <div v-if="c.key === 'position_cap'" class="cap-row">
              <span class="muted">实际仓位上限</span>
              <el-input-number v-model="review.cap" :min="0" :max="100" :step="5" size="small"
                               controls-position="right" class="no-print" :disabled="!ready" />
              <span class="print-only">____%</span>
              <span class="muted">%</span>
            </div>
          </div>
        </div>
        <div v-if="s.step === 2 && industryTop.length" class="industry-chips">
          <span class="muted">当日行业涨幅前三:</span>
          <el-tag v-for="r in industryTop" :key="r.name" size="small" effect="plain">{{ r.name }} {{ fmtPct(r.pct_chg) }}</el-tag>
        </div>
      </div>
      <p v-if="!checks.length" class="muted">暂无清单(双核指数落库后自动生成)</p>
    </el-card>

    <!-- 盘后复盘 -->
    <el-card class="page-card review-card">
      <template #header>
        <div class="card-head">
          <span>盘后复盘记录(时间:{{ reviewTime }})</span>
        </div>
      </template>
      <div class="review-item">
        <label class="review-q">1. 今日 883994 是否创历史新高?</label>
        <el-radio-group v-model="review.q1" :disabled="!ready">
          <el-radio value="是">是</el-radio>
          <el-radio value="否">否</el-radio>
        </el-radio-group>
        <span class="muted review-hint">系统判定:{{ bNewHighText }}</span>
      </div>
      <div class="review-item">
        <label class="review-q">2. 今日操作是否完全符合多周期共振?</label>
        <el-radio-group v-model="review.q2" :disabled="!ready">
          <el-radio value="是">是</el-radio>
          <el-radio value="否">否</el-radio>
        </el-radio-group>
      </div>
      <div class="review-item block">
        <label class="review-q">3. 今日失误反思(情绪、纪律、周期判断)</label>
        <el-input v-model="review.q3" type="textarea" :rows="3" maxlength="500" show-word-limit
                  :disabled="!ready" placeholder="例:情绪上头扫了跟风板,未等 883994 创新高;明日只做首板/中军" />
      </div>
      <p class="iron-rule">
        交易铁律:周期共振我重拳出击,日线背离我坚决空仓。周线保护日线,日线保护 5 日线。
        不创新高,宁可不做;跌破退军,绝不抗单!
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../store'
import { taskName } from '../utils/tasks'

const store = useUserStore()
const canOperate = computed(() => store.isAnalyst)
const isMobile = ref(window.matchMedia('(max-width: 768px)').matches)
const descColumns = computed(() => (isMobile.value ? 1 : 2))

const payload = ref(null)
const loading = ref(false)
const syncing = ref(false)
const saving = ref(false)
const loadError = ref('')
const savedAt = ref('')

const items = reactive({})
const review = reactive({ q1: '', q2: '', q3: '', cap: null })

// 防止「读取清单 → 回填表单」触发自动保存
let hydrating = true
let saveTimer = null
let saveQueued = false
let refreshTimers = []

const STEP_META = [
  { step: 1, title: '第一步 · 开盘前与盘中 择时检查(一票否决)' },
  { step: 2, title: '第二步 · 盘中 选股检查(多周期过滤)' },
  { step: 3, title: '第三步 · 执行 扣动扳机' },
  { step: 4, title: '第四步 · 风控与纪律(收盘前 10 分钟)' },
]

const asOf = computed(() => payload.value?.as_of || '')
const asOfText = computed(() => asOf.value || '暂无数据')
const ready = computed(() => !!payload.value?.ready)
const data = computed(() => payload.value?.data || {})
const verdict = computed(() => payload.value?.verdict || {})
const divNotes = computed(() => payload.value?.divergence?.notes || [])
const industryTop = computed(() => payload.value?.industry_top || [])
const checks = computed(() => payload.value?.checks || [])
const steps = computed(() => STEP_META.map((m) => ({
  ...m, items: checks.value.filter((c) => c.step === m.step),
})))

const cores = computed(() => [
  { code: '883957', name: '同花顺全A(沪深京)', ...(data.value['883957'] || {}) },
  { code: '883994', name: '昨日打首板表现', ...(data.value['883994'] || {}) },
])

const bNewHigh = computed(() => data.value['883994']?.new_high_intraday)
const bNewHighText = computed(() => (bNewHigh.value === true ? '是(盘中已创)'
  : bNewHigh.value === false ? '否(未创)' : '历史不足'))

const notReadyText = computed(() => payload.value?.message
  || '双核指数尚未落库,请先在任务中心运行「双核指数同步」')
const staleText = computed(() => (payload.value?.stale_cores || []).join('、') || '-')

const flags = computed(() => {
  const v = verdict.value
  return [
    flag('周线格局', v.weekly_ok),
    flag('日线发令枪', v.daily_ok),
    flag('5 日线共振', v.ma5_ok),
  ]
})
function flag(label, val) {
  if (val === true) return { label, text: '配合', type: 'success' }
  if (val === false) return { label, text: '不配合', type: 'danger' }
  return { label, text: '历史不足', type: 'info' }
}

const saveHint = computed(() => {
  if (saving.value) return '保存中…'
  if (savedAt.value) return `已保存 ${savedAt.value.replace('T', ' ').slice(0, 16)}`
  return '勾选后自动保存'
})

const reviewTime = ref(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))

function fmt(v, digits = 3) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(digits) : '-'
}
function fmtPct(v) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}
function upDown(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? 'up' : 'down'
}
function levelTagType(level) {
  return { 进攻: 'danger', 轻仓试错: 'warning', 防守: 'info', 空仓: 'info' }[level] || 'info'
}
function levelClass(level) {
  return { 进攻: 'lv-attack', 轻仓试错: 'lv-probe', 防守: 'lv-defend', 空仓: 'lv-empty' }[level] || 'lv-none'
}
function passIcon(pass) {
  if (pass === true) return '✓'
  if (pass === false) return '✗'
  return '?'
}
function passClass(pass) {
  if (pass === true) return 'st-ok'
  if (pass === false) return 'st-bad'
  return 'st-na'
}

function coreTags(core) {
  const tags = []
  if (core.new_high_intraday === true) tags.push({ text: '盘中创历史新高', type: 'danger' })
  else if (core.new_high_intraday === false) tags.push({ text: '未创历史新高', type: 'info' })
  if (core.above_ma5 === true) tags.push({ text: '站上 5 日线', type: 'success' })
  else if (core.above_ma5 === false) tags.push({ text: '跌破 5 日线', type: 'danger' })
  if (core.ma5_up === true) tags.push({ text: '5 日线向上', type: 'success' })
  else if (core.ma5_up === false) tags.push({ text: '5 日线走平/向下', type: 'warning' })
  if (core.w_above_ma10 === true) tags.push({ text: '周线站上 10 周线', type: 'success' })
  else if (core.w_above_ma10 === false) tags.push({ text: '周线跌破 10 周线', type: 'danger' })
  if (core.w_above_ma20 === true) tags.push({ text: '周线站上 20 周线', type: 'success' })
  else if (core.w_above_ma20 === false) tags.push({ text: '周线跌破 20 周线', type: 'danger' })
  if (core.w_bull === true) tags.push({ text: '周线多头排列', type: 'success' })
  else if (core.w_bull === false) tags.push({ text: '周线未多头排列', type: 'warning' })
  if (core.w_macd_hist_shrink === true) tags.push({ text: '周 MACD 红柱缩短', type: 'warning' })
  return tags
}

function sparkPoints(code) {
  const arr = (payload.value?.series?.[code] || []).map((p) => Number(p.close)).filter((v) => Number.isFinite(v))
  if (arr.length < 2) return ''
  const mn = Math.min(...arr)
  const mx = Math.max(...arr)
  const span = mx - mn || 1
  return arr.map((v, i) => {
    const x = (i / (arr.length - 1)) * 300
    const y = 55 - ((v - mn) / span) * 50
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}
function sparkColor(code) {
  const arr = payload.value?.series?.[code] || []
  if (arr.length < 2) return '#909399'
  const first = Number(arr[0].close)
  const last = Number(arr[arr.length - 1].close)
  return last >= first ? '#f56c6c' : '#67c23a'
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    payload.value = await api.get('/api/screener/dual-core')
    await loadChecklist(payload.value?.as_of || '')
  } catch (exc) {
    payload.value = null
    loadError.value = '双核数据加载失败,请稍后重试'
  } finally {
    loading.value = false
  }
}

async function loadChecklist(tradeDate) {
  hydrating = true
  try {
    const r = await api.get('/api/screener/dual-core/checklist', {
      params: tradeDate ? { trade_date: tradeDate } : {},
      silent: true,
    })
    Object.keys(items).forEach((k) => delete items[k])
    Object.assign(items, r?.items || {})
    Object.assign(review, { q1: '', q2: '', q3: '', cap: null }, r?.review || {})
    savedAt.value = r?.updated_at || ''
    // 首次进入且用户未填写:用系统判定预填第 1 问
    if (!review.q1 && bNewHigh.value !== null && bNewHigh.value !== undefined) {
      review.q1 = bNewHigh.value ? '是' : '否'
    }
  } catch {
    // 清单读取失败不阻塞判定展示,也不弹全局错误(接口已 silent)
  } finally {
    // 等本轮 watcher 刷新完再允许自动保存,避免 hydrate 覆盖服务端数据
    setTimeout(() => { hydrating = false }, 0)
  }
}

// 人工项与复盘改动 1.2s 防抖自动保存(服务端整份覆盖)
watch(items, () => scheduleSave(), { deep: true })
watch(review, () => scheduleSave(), { deep: true })

function scheduleSave() {
  if (hydrating || !ready.value) return
  clearTimeout(saveTimer)
  saveTimer = setTimeout(saveNow, 1200)
}

async function saveNow() {
  clearTimeout(saveTimer)
  if (!ready.value) return
  if (saving.value) { saveQueued = true; return }
  saving.value = true
  try {
    const r = await api.put('/api/screener/dual-core/checklist', {
      trade_date: asOf.value || undefined,
      items: { ...items },
      review: { ...review },
    })
    savedAt.value = r?.updated_at || new Date().toISOString()
  } catch {
    // 全局拦截器已提示
  } finally {
    saving.value = false
    if (saveQueued) { saveQueued = false; saveNow() }
  }
}

async function syncData() {
  syncing.value = true
  try {
    const r = await api.post('/api/screener/tasks/dual_core/run')
    ElMessage.success(r?.message || '已提交后台执行')
    refreshTimers.forEach(clearTimeout)
    refreshTimers = [setTimeout(load, 8000), setTimeout(load, 25000)]
  } finally {
    syncing.value = false
  }
}

function printCard() {
  window.print()
}

onMounted(load)
onUnmounted(() => {
  refreshTimers.forEach(clearTimeout)
  clearTimeout(saveTimer)
})
</script>

<style scoped>
.dual-core { padding-bottom: 8px; }
.print-only { display: none; }
.dc-status { display: flex; align-items: center; flex-wrap: wrap; gap: 14px;
  padding: 10px 14px; margin-bottom: 6px; background: #fff;
  border: 1px solid #ebeef5; border-radius: 6px; }
.status-item { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.status-label { color: #909399; font-size: 12px; }
.status-item strong { font-size: 14px; font-variant-numeric: tabular-nums; }
.grow { flex: 1; }
.dc-note { font-size: 12px; margin: 0 0 12px; line-height: 1.6; }

.verdict-body { display: flex; flex-direction: column; gap: 10px; }
.verdict-line { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 0; }
.verdict-level { font-size: 30px; line-height: 1.1; letter-spacing: 2px; }
.lv-attack { color: #f56c6c; }
.lv-probe { color: #e6a23c; }
.lv-defend { color: #409eff; }
.lv-empty { color: #606266; }
.lv-none { color: #909399; }
.veto-badge { display: inline-block; padding: 2px 10px; border-radius: 4px;
  background: #fef0f0; color: #f56c6c; border: 1px solid #fbc4c4; font-size: 13px; font-weight: 600; }
.reason-list { margin: 0; padding-left: 20px; line-height: 1.8; }
.reason-list li { font-size: 14px; }
.div-notes { margin: 0; padding: 8px 12px 8px 28px; background: #fdf6ec; border-radius: 4px; line-height: 1.7; }
.div-notes li { font-size: 13px; color: #b88230; }
.flag-row { display: flex; gap: 8px; flex-wrap: wrap; }

.core-close { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }
.spark { width: 100%; height: 64px; display: block; background: #fafafa; border-radius: 4px; }
.spark-caption { font-size: 11px; margin: 4px 0 10px; }
.core-desc :deep(.el-descriptions__label) { width: 132px; }
.state-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.core-cap { font-size: 11px; margin: 8px 0 0; }

.step-block { margin-bottom: 18px; }
.step-block:last-of-type { margin-bottom: 4px; }
.step-title { display: flex; align-items: center; gap: 8px; margin: 0 0 10px;
  font-size: 14px; font-weight: 600; color: #303133; }
.step-no { display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; border-radius: 50%; background: #409eff; color: #fff;
  font-size: 12px; font-weight: 700; flex: none; }
.check-row { display: flex; gap: 10px; padding: 9px 12px; margin-bottom: 8px;
  border: 1px solid #ebeef5; border-left: 4px solid #dcdfe6; border-radius: 4px; background: #fff; }
.check-row.check-ok { border-left-color: #67c23a; }
.check-row.check-fail { border-left-color: #f56c6c; background: #fef8f8; }
.check-row.check-veto { background: #fef0f0; border-left-color: #f56c6c; }
.check-state { flex: none; padding-top: 1px; min-width: 20px; display: flex; align-items: flex-start; }
.state-icon { font-size: 17px; font-weight: 700; line-height: 1.2; }
.st-ok { color: #67c23a; }
.st-bad { color: #f56c6c; }
.st-na { color: #909399; }
.manual-box { font-size: 17px; line-height: 1.2; }
.check-body { flex: 1; min-width: 0; }
.check-label { font-size: 13px; line-height: 1.7; color: #303133; }
.manual-tag { margin-left: 6px; }
.check-meta { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 3px; }
.check-value { font-size: 12px; color: #606266; font-variant-numeric: tabular-nums; }
.check-hint { font-size: 12px; line-height: 1.6; }
.cap-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 12px; }
.industry-chips { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-left: 4px; font-size: 12px; }

.save-hint { font-size: 12px; }
.review-item { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
.review-item.block { flex-direction: column; align-items: stretch; gap: 6px; }
.review-q { font-size: 14px; font-weight: 600; color: #303133; }
.review-hint { font-size: 12px; }
.iron-rule { margin: 6px 0 0; padding: 10px 12px; background: #f5f7fa;
  border-left: 4px solid #409eff; border-radius: 4px; font-size: 13px; line-height: 1.8; color: #606266; }

.print-head { margin-bottom: 10px; }
.print-head h2 { margin: 0 0 6px; font-size: 18px; }
.print-meta { display: flex; flex-wrap: wrap; gap: 16px; font-size: 12px; }
.print-meta span { white-space: nowrap; }

@media (max-width: 768px) {
  .verdict-level { font-size: 24px; }
  .core-desc :deep(.el-descriptions__label) { width: 104px; }
  .dc-status { gap: 8px; }
}

/* 打印:隐藏交互控件,保留清单与结论;卡片不跨页断裂 */
@page { margin: 12mm; }
@media print {
  .print-only { display: block; }
  .no-print { display: none !important; }
  .dual-core { padding: 0; }
  .dual-core :deep(.el-card) { box-shadow: none !important; border: 1px solid #c0c4cc; margin-bottom: 8px; }
  .dual-core :deep(.el-card__header) { padding: 6px 10px; }
  .dual-core :deep(.el-card__body) { padding: 8px 10px; }
  .dual-core :deep(.el-loading-mask) { display: none !important; }
  .check-row { break-inside: avoid; padding: 5px 8px; margin-bottom: 5px; background: #fff !important; }
  .step-block { break-inside: avoid; margin-bottom: 10px; }
  .verdict-level { font-size: 22px; }
  .spark { height: 48px; }
  .iron-rule { background: #fff; border: 1px solid #606266; }
  .div-notes { background: #fff; border: 1px dashed #e6a23c; }
}
</style>
