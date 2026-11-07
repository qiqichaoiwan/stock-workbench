<template>
  <div class="workbench-page watchlist-page">
    <el-card class="page-card workbench-card alerts-card">
      <template #header>
        <div class="card-head">
          <span>命中提醒</span>
          <span class="grow" />
          <el-button size="small" :loading="alertsLoading" @click="loadAlerts">刷新</el-button>
        </div>
      </template>
      <el-alert v-if="alertsError" type="error" :title="alertsError" :closable="false" show-icon />
      <div v-if="alerts.length">
        <el-alert v-for="a in alerts" :key="a.code + a.rule + a.created_at"
                  type="warning" :closable="false" show-icon style="margin-bottom:8px"
                  :title="`${a.code} ${a.rule}`" :description="`${a.message || ''} ${a.created_at || ''}`" />
      </div>
      <el-empty v-else-if="!alertsLoading && !alertsError" description="暂无命中提醒" :image-size="60">
        <template #description>
          <p>暂无命中提醒,价格触及三价或涨跌幅阈值后自动出现</p>
          <p class="muted" style="font-size:12px">由盘中「{{ taskName('watch_alert') }}」任务每 30 分钟扫描;提醒只读不改止损,仅提示</p>
        </template>
      </el-empty>
    </el-card>

    <el-card class="page-card workbench-card">
      <template #header>
        <div class="card-head">
          <span>自选预警</span>
          <span class="grow" />
          <el-button size="small" :loading="loading" @click="load">刷新列表</el-button>
          <el-button size="small" @click="refreshQuotes">刷新行情</el-button>
          <el-button type="primary" size="small" @click="openAdd">新增自选</el-button>
        </div>
      </template>
      <el-alert v-if="listError" type="error" :title="listError" :closable="false" show-icon />
      <div class="quote-status-banner" :class="{ degraded: quoteError }" role="status">
        <span class="status-dot" aria-hidden="true" />
        <span>{{ quoteError || '行情每 30 秒刷新，预警只使用可核验的新鲜盘中行情。' }}</span>
        <span class="grow" />
        <span class="muted">数据源与时间按单股展示</span>
      </div>
      <div class="table-scroll">
      <el-table class="workbench-table" :data="rows" size="small" v-loading="loading" stripe border>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column prop="name" label="名称" min-width="110" show-overflow-tooltip />
        <el-table-column label="现价" width="210" align="right">
          <template #default="{ row }">{{ fmtNum(quoteFor(row).price) }}<div class="muted quote-note">{{ portfolioQuoteLabel(quoteFor(row)) }}</div></template>
        </el-table-column>
        <el-table-column label="涨跌幅" width="100" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(quoteFor(row).pct_chg)">{{ fmtPct(quoteFor(row).pct_chg) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="成本" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.cost_price) }}</template>
        </el-table-column>
        <el-table-column label="止盈" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.take_profit) }}</template>
        </el-table-column>
        <el-table-column label="止损" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.stop_loss) }}</template>
        </el-table-column>
        <el-table-column label="预警%" width="80" align="right">
          <template #default="{ row }">{{ row.alert_pct ?? '-' }}</template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-popconfirm title="删除该自选?" @confirm="remove(row)">
              <template #reference>
                <el-button size="small" link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      </div>
      <el-empty v-if="!loading && !listError && !rows.length" description="暂无自选,点击右上新增" :image-size="60">
        <template #description>
          <p>暂无自选,点击右上新增</p>
          <p class="muted" style="font-size:12px">成本=盈亏参考价,止盈/止损=触发提醒线,预警%=单日涨跌幅阈值;任一项填 0 表示不设</p>
        </template>
      </el-empty>
    </el-card>

    <el-dialog v-model="dlg" :title="editing ? '编辑自选' : '新增自选'" width="440px">
      <el-form :model="form" :rules="rules" ref="formRef" label-width="90px">
        <el-form-item label="代码" prop="code">
          <el-input v-model="form.code" maxlength="6" placeholder="6 位股票代码" :disabled="!!editing" />
        </el-form-item>
        <el-form-item label="成本价" prop="cost_price">
          <el-input-number v-model="form.cost_price" :precision="2" :min="0" style="width:100%" />
        </el-form-item>
        <el-form-item label="止盈价" prop="take_profit">
          <el-input-number v-model="form.take_profit" :precision="2" :min="0" style="width:100%" />
        </el-form-item>
        <el-form-item label="止损价" prop="stop_loss">
          <el-input-number v-model="form.stop_loss" :precision="2" :min="0" style="width:100%" />
        </el-form-item>
        <el-form-item label="预警%" prop="alert_pct">
          <el-input-number v-model="form.alert_pct" :precision="1" :min="0" style="width:100%" />
        </el-form-item>
        <el-form-item label="备注" prop="note">
          <el-input v-model="form.note" maxlength="200" placeholder="备注(可选)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { effectiveQuote, portfolioQuoteLabel } from '../utils/portfolioQuotes'
import { isTradingSession, markQuoteRefreshFailed } from '../utils/poolQuotes'
import { fmtNum, fmtPct, upDownClass } from '../utils/fmt'
import { taskName } from '../utils/tasks'

const rows = ref([])
const quotes = ref({})
const quoteError = ref('')
let quoteSeq = 0
let quoteRequestId = 0
let quoteBusy = false
let timer = null
let clockTimer = null
const quoteClock = ref(Date.now())
const alerts = ref([])
const alertsLoading = ref(false)
const alertsError = ref('')
const listError = ref('')
const loading = ref(false)
const dlg = ref(false)
const saving = ref(false)
const editing = ref(null)
const formRef = ref(null)
const form = reactive({ code: '', cost_price: null, take_profit: null, stop_loss: null, alert_pct: null, note: '' })
const rules = {
  code: [
    { required: true, message: '请输入代码', trigger: 'blur' },
    { pattern: /^\d{6}$/, message: '代码必须为 6 位数字', trigger: 'blur' },
  ],
}
let seq = 0
let alertsSeq = 0

onMounted(() => {
  load(); loadAlerts(); timer = setInterval(() => { if (isTradingSession()) refreshQuotes() }, 30000)
  clockTimer = setInterval(() => { quoteClock.value = Date.now() }, 1000)
  document.addEventListener('visibilitychange', onVisibilityChange)
})
onBeforeUnmount(() => {
  seq += 1; alertsSeq += 1; invalidateQuotes(); clearInterval(timer); clearInterval(clockTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
function onVisibilityChange() {
  quoteClock.value = Date.now()
  if (document.visibilityState === 'visible' && isTradingSession()) refreshQuotes()
}

function quoteFor(row) {
  const quote = quotes.value[row.code] || { price: row.close, pct_chg: row.pct_chg,
    quote_at: row.quote_at, quote_kind: row.close == null ? 'missing' : 'stored' }
  return effectiveQuote(quote, quoteClock.value)
}
async function refreshQuotes() {
  if (quoteBusy || !rows.value.length) return
  quoteBusy = true
  const requestId = ++quoteRequestId
  const cur = ++quoteSeq
  try {
    const data = await api.get('/api/portfolio/quotes?scope=watchlist', { silent: true })
    if (cur !== quoteSeq) return
    quotes.value = data.items || {}
    quoteError.value = (data.errors || []).join('；')
  } catch {
    if (cur === quoteSeq) {
      // Do not keep a realtime label after a failed refresh. Each quote keeps
      // its price for continuity, but is explicitly marked as a prior result.
      quotes.value = markQuoteRefreshFailed(quotes.value)
      quoteError.value = '行情读取失败，继续显示上次行情。'
    }
  } finally {
    if (requestId === quoteRequestId) quoteBusy = false
  }
}

function invalidateQuotes() {
  quoteSeq += 1
  quoteRequestId += 1
  quoteBusy = false
  // A list reload can replace symbols. Clear the old map so a response for
  // the previous list cannot be rendered as a quote for the new rows.
  quotes.value = {}
}

async function load() {
  const cur = (seq += 1)
  loading.value = true
  listError.value = ''
  quoteError.value = ''
  invalidateQuotes()
  try {
    const data = await api.get('/api/watchlist', { silent: true })
    if (cur !== seq) return
    if (!Array.isArray(data)) throw new Error('invalid watchlist response')
    rows.value = data
    await refreshQuotes()
  } catch {
    if (cur === seq) {
      listError.value = `自选列表读取失败${rows.value.length ? '，显示上次结果' : ''}；请点击刷新列表重试。`
      quoteError.value = '列表读取失败，行情等待重试。'
    }
  } finally {
    if (cur === seq) loading.value = false
  }
}

async function loadAlerts() {
  const cur = ++alertsSeq
  alertsError.value = ''
  alertsLoading.value = true
  try {
    const data = await api.get('/api/watchlist/alerts', { silent: true })
    if (cur !== alertsSeq) return
    if (!Array.isArray(data)) throw new Error('invalid alerts response')
    alerts.value = data
  } catch {
    if (cur === alertsSeq) alertsError.value = `预警读取失败${alerts.value.length ? '，显示上次结果' : ''}；请点击刷新重试。`
  } finally {
    if (cur === alertsSeq) alertsLoading.value = false
  }
}

function resetForm() {
  form.code = ''; form.cost_price = null; form.take_profit = null
  form.stop_loss = null; form.alert_pct = null; form.note = ''
}

function openAdd() {
  editing.value = null
  resetForm()
  dlg.value = true
}

function openEdit(row) {
  editing.value = row
  form.code = row.code
  form.cost_price = row.cost_price; form.take_profit = row.take_profit
  form.stop_loss = row.stop_loss; form.alert_pct = row.alert_pct
  form.note = row.note || ''
  dlg.value = true
}

async function save() {
  try { await formRef.value?.validate() } catch { return }
  if (!/^\d{6}$/.test(form.code || '')) return ElMessage.warning('代码必须为 6 位数字')
  // 止盈止损交叉校验(两者都填时;0/空 = 不设)
  const tp = Number(form.take_profit) || 0
  const sl = Number(form.stop_loss) || 0
  if (tp > 0 && sl > 0 && tp <= sl) {
    return ElMessage.warning('止盈价应高于止损价(两者都填时)')
  }
  saving.value = true
  try {
    const payload = {
      code: form.code,
      cost_price: form.cost_price ?? 0, take_profit: form.take_profit ?? 0,
      stop_loss: form.stop_loss ?? 0, alert_pct: form.alert_pct ?? 0, note: form.note || '',
    }
    if (editing.value) await api.put(`/api/watchlist/${editing.value.id}`, payload, { silent: true })
    else await api.post('/api/watchlist', payload, { silent: true })
    ElMessage.success('已保存')
    dlg.value = false
    await load()
  } catch (err) {
    // 保存失败保持对话框并显式提示(silent 只是不走全局拦截器,这里必须自己报)
    const msg = err?.response?.data?.detail || err?.message || '保存失败'
    ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  try {
    await api.delete(`/api/watchlist/${row.id}`, { silent: true })
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '删除失败'
    ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
}
</script>

<style scoped>
.quote-note { font-size: 11px; line-height: 1.3; }
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.workbench-card { border-radius: var(--radius-card, 12px); border-color: var(--line-soft, #e6ebf2); box-shadow: var(--shadow-card, 0 4px 16px rgba(24, 44, 72, .05)); }
.workbench-table :deep(.el-table__header th) { background: #f7f9fc; color: #617089; font-size: 12px; }
.workbench-table :deep(.el-table__row:hover > td) { background: #f4f8ff !important; }
.quote-status-banner { display: flex; align-items: center; gap: 8px; min-height: 34px; padding: 7px 10px; margin-bottom: 12px; border: 1px solid #cbe9dc; border-radius: 8px; background: #f1fbf6; color: #236a50; font-size: 12px; }
.quote-status-banner.degraded { border-color: #f0d7a5; background: #fff9ed; color: #8b5b00; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: #2fbb82; box-shadow: 0 0 0 3px rgba(47, 187, 130, .12); }
.degraded .status-dot { background: #d99518; box-shadow: 0 0 0 3px rgba(217, 149, 24, .12); }
@media (max-width: 768px) { .quote-status-banner { align-items: flex-start; flex-wrap: wrap; } .quote-status-banner .grow { display: none; } }
</style>
