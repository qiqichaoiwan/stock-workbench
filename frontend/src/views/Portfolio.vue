<template>
  <div class="workbench-page portfolio-page">
    <el-card class="page-card workbench-card">
      <template #header>
        <div class="card-head">
          <span>我的持仓(G03 三阶段止损 · 硬规则代码强制)</span>
          <span class="grow" />
          <el-button size="small" @click="refreshQuotes">刷新行情</el-button>
          <el-button type="primary" size="small" @click="dialog = true">录入持仓</el-button>
        </div>
      </template>
      <div class="quote-status-banner" :class="{ degraded: quoteError || loadError }" role="status">
        <span class="status-dot" aria-hidden="true" />
        <span>{{ quoteError || loadError || '行情每 30 秒刷新；止损阶段由收盘后巡检推进。' }}</span>
        <span class="grow" />
        <span class="muted">实时 / 收盘快照按单股标注</span>
      </div>
      <div v-if="loadError" class="load-error" role="alert">
        <span>{{ loadError }}</span>
        <el-button size="small" type="danger" plain :loading="loading" @click="load">重试</el-button>
      </div>
      <el-table class="workbench-table" :data="rows" size="small" v-loading="loading" stripe border>
        <el-table-column prop="code" label="代码" width="85" />
        <el-table-column label="名称" min-width="100">
          <template #default="{ row }">{{ cleanName(row.name) }}</template>
        </el-table-column>
        <el-table-column label="买入价" width="85" align="right">
          <template #default="{ row }">{{ fmtNum(row.buy_price) }}</template>
        </el-table-column>
        <el-table-column label="现价" width="210" align="right">
          <template #default="{ row }">{{ fmtNum(quoteFor(row).price) }}<div class="muted quote-note">{{ portfolioQuoteLabel(quoteFor(row)) }}</div></template>
        </el-table-column>
        <el-table-column label="浮盈%" width="90" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(unrealized(row))">{{ fmtPct(unrealized(row)) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="保本价" width="85" align="right">
          <template #default="{ row }">{{ fmtNum(row.breakeven_price) }}</template>
        </el-table-column>
        <el-table-column label="止损价" width="85" align="right">
          <template #default="{ row }">{{ fmtNum(row.stop_price) }}</template>
        </el-table-column>
        <el-table-column label="阶段" width="70">
          <template #default="{ row }">
            <el-tooltip :content="stageTip(row.stop_stage)" placement="top">
              <el-tag :type="row.stop_stage === 'A' ? 'info' : row.stop_stage === 'B' ? 'warning' : 'success'" size="small">{{ row.stop_stage }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="期间最高" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.highest_price) }}</template>
        </el-table-column>
        <el-table-column label="池评级" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.pool_rating?.rating" :type="ratingType(row.pool_rating.rating)" size="small">
              {{ ratingLabel(row.pool_rating.rating) }}
            </el-tag>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="风险" width="95">
          <template #default="{ row }">
            <el-tag v-if="quoteFor(row).price != null && quoteFor(row).price <= row.stop_price" type="danger" size="small">{{ quoteFor(row).realtime ? "触及止损" : "参考价触及" }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link :loading="row._ai" @click="ai(row)">AI分析</el-button>
            <el-button size="small" type="danger" link @click="openSell(row)">卖出</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !loadError && !rows.length" description="暂无持仓,点击右上角「录入持仓」开始" :image-size="70">
        <template #description>
          <p>暂无持仓,点击右上角「录入持仓」开始</p>
          <p class="muted" style="font-size:12px">行情按来源更新，止损阶段由交易日 15:50 巡检推进;卖出后自动计入交易历史</p>
        </template>
      </el-empty>
    </el-card>

    <el-dialog v-model="dialog" title="录入持仓(系统自动计算保本价与止损线)" width="480">
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="110px">
        <el-form-item label="股票代码" prop="code">
          <el-input v-model="form.code" maxlength="6" placeholder="6位代码,如 600519" style="width:200px" />
        </el-form-item>
        <el-form-item label="买入价" prop="buy_price">
          <el-input-number v-model="form.buy_price" :precision="2" :min="0.01" :max="99999" />
        </el-form-item>
        <el-form-item label="数量" prop="quantity">
          <el-input-number v-model="form.quantity" :min="100" :step="100" />
        </el-form-item>
        <el-form-item label="买入日期" prop="buy_date">
          <el-date-picker v-model="form.buy_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="初始止损%">
          <el-input-number v-model="form.stop_pct" :min="1" :max="30" />
          <span class="muted" style="margin-left:8px;font-size:12px">会议口径:最高点回撤10-15%必须卖出,默认10</span>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.note" maxlength="200" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="add">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="sellDialog" title="卖出登记(计入历史审计)" width="440">
      <el-form label-width="110px">
        <el-form-item label="卖出价">
          <el-input-number v-model="sellForm.sell_price" :precision="2" :min="0.01" />
        </el-form-item>
        <el-form-item label="卖出日期">
          <el-date-picker v-model="sellForm.sell_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="sellForm.note" maxlength="200" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sellDialog = false">取消</el-button>
        <el-button type="danger" :loading="selling" @click="confirmSell">确认已卖出</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="aiDialog" title="AI 分析(参考建议,非决策;止损线不受影响)" width="560">
      <template v-if="aiResult">
        <p style="line-height:1.7">{{ aiResult.analysis }}</p>
        <h4>卖出点位预测</h4>
        <el-table :data="aiResult.sell_points" size="small">
          <el-table-column label="点位" width="120" align="right">
            <template #default="{ row }">{{ fmtNum(row.price) }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="依据" />
        </el-table>
        <p class="muted" style="margin-top:8px">{{ aiResult.risk_note }} · 模型:{{ aiResult.model }}</p>
        <p class="muted" style="font-size:12px">{{ aiResult.disclaimer }}</p>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { effectiveQuote, portfolioQuoteLabel, todayInShanghai } from '../utils/portfolioQuotes'
import { isTradingSession, markQuoteRefreshFailed } from '../utils/poolQuotes'
import { cleanName, fmtNum, fmtPct, ratingLabel, ratingType, upDownClass } from '../utils/fmt'

const rows = ref([])
const quotes = ref({})
const quoteError = ref('')
const loadError = ref('')
let timer = null
let clockTimer = null
const quoteClock = ref(Date.now())
let quoteSeq = 0
let quoteRequestId = 0
let quoteBusy = false
const loading = ref(false)
const dialog = ref(false)
const sellDialog = ref(false)
const aiDialog = ref(false)
const aiResult = ref(null)
const saving = ref(false)
const selling = ref(false)
const current = ref(null)
const formRef = ref()
const form = ref({ code: '', buy_price: 10, quantity: 100, buy_date: todayInShanghai(), stop_pct: 10, note: '' })
const sellForm = ref({ sell_price: 0, sell_date: todayInShanghai(), note: '' })

const formRules = {
  code: [
    { required: true, message: '请输入股票代码', trigger: 'blur' },
    { pattern: /^\d{6}$/, message: '代码为 6 位数字', trigger: 'blur' },
  ],
  buy_price: [{ required: true, message: '请输入买入价', trigger: 'blur' }],
  quantity: [{ required: true, message: '请输入数量', trigger: 'blur' }],
  buy_date: [{ required: true, message: '请选择买入日期', trigger: 'change' }],
}

function stageTip(s) {
  return { A: '阶段A:初始止损(禁上移)', B: '阶段B:已上移至保本价', C: '阶段C:创新高后动态跟踪' }[s] || s
}

onMounted(() => {
  load(); timer = setInterval(() => { if (isTradingSession()) refreshQuotes() }, 30000)
  clockTimer = setInterval(() => { quoteClock.value = Date.now() }, 1000)
  document.addEventListener('visibilitychange', onVisibilityChange)
})
onBeforeUnmount(() => {
  clearInterval(timer); clearInterval(clockTimer); invalidateQuotes()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
function onVisibilityChange() {
  quoteClock.value = Date.now()
  if (document.visibilityState === 'visible' && isTradingSession()) refreshQuotes()
}

function quoteFor(row) {
  const quote = quotes.value[row.code] || { price: row.close, quote_at: row.quote_at,
    quote_kind: row.close == null ? 'missing' : 'stored' }
  // 建仓之前的行情不能计算持仓盈亏或触发止损显示。
  if (quote.quote_at && quote.quote_at.slice(0, 10) < row.buy_date) return { quote_kind: 'missing', price: null }
  return effectiveQuote(quote, quoteClock.value)
}
function unrealized(row) {
  const price = quoteFor(row).price
  return price == null || !row.buy_price ? null : (price / row.buy_price - 1) * 100
}
async function refreshQuotes() {
  if (quoteBusy || !rows.value.length) return
  quoteBusy = true
  const requestId = ++quoteRequestId
  const seq = ++quoteSeq
  try {
    const data = await api.get('/api/portfolio/quotes?scope=holdings', { silent: true })
    if (seq !== quoteSeq) return
    quotes.value = data.items || {}
    quoteError.value = (data.errors || []).join('；')
  } catch {
    // Keep the last known snapshot during transient network failures.
    if (seq === quoteSeq) {
      // A failed refresh must revoke any previous realtime claim immediately;
      // otherwise the 90-second client freshness window can mislabel stale
      // data as live while the provider is unreachable.
      quotes.value = markQuoteRefreshFailed(quotes.value)
      quoteError.value = '行情读取失败，继续显示上次行情。'
    }
  } finally {
    // A newer list load may have invalidated this request and started another
    // one. Only the active request may release the busy guard.
    if (requestId === quoteRequestId) quoteBusy = false
  }
}

function invalidateQuotes() {
  quoteSeq += 1
  quoteRequestId += 1
  quoteBusy = false
  // A list reload can change the symbols. Do not allow a response for the
  // previous list to appear against the new rows while the fresh snapshot is
  // being fetched.
  quotes.value = {}
}

let loadSeq = 0
async function load() {
  const cur = ++loadSeq
  loading.value = true
  loadError.value = ''
  invalidateQuotes()
  try {
    const data = await api.get('/api/holdings', { silent: true })
    if (cur !== loadSeq) return
    if (!Array.isArray(data)) throw new Error('invalid holdings response')
    rows.value = data
    await refreshQuotes()
  } catch {
    if (cur === loadSeq) {
      loadError.value = `持仓列表读取失败${rows.value.length ? '，显示上次结果' : ''}；请点击重试。`
    }
  } finally {
    if (cur === loadSeq) loading.value = false
  }
}

async function add() {
  await formRef.value.validate()
  saving.value = true
  try {
    const r = await api.post('/api/holdings', form.value)
    ElMessage.success(`已录入,保本价 ${fmtNum(r.breakeven_price)},初始止损 ${fmtNum(r.initial_stop_price)}`)
    dialog.value = false
    await load()
  } finally {
    saving.value = false
  }
}

function openSell(row) {
  current.value = row
  sellForm.value = { sell_price: quoteFor(row).price, sell_date: todayInShanghai(), note: '' }
  sellDialog.value = true
}

async function confirmSell() {
  if (!sellForm.value.sell_price) return ElMessage.warning('请输入卖出价')
  selling.value = true
  try {
    const r = await api.post(`/api/holdings/${current.value.id}/sell`, sellForm.value)
    ElMessage.success(`已登记,盈亏 ${fmtNum(r.pnl)} 元(${fmtPct(r.pnl_pct)}),触发:${r.exit_trigger}`)
    sellDialog.value = false
    await load()
  } finally {
    selling.value = false
  }
}

async function ai(row) {
  row._ai = true
  try {
    aiResult.value = await api.post(`/api/holdings/${row.id}/ai-analysis`)
    aiDialog.value = true
  } finally {
    row._ai = false
  }
}
</script>

<style scoped>
.quote-note { font-size: 11px; line-height: 1.3; }
.load-error { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; padding: 9px 12px; border: 1px solid #f2c5c5; border-radius: 8px; background: #fff5f5; color: #b42318; font-size: 13px; }
.workbench-card { border-radius: var(--radius-card, 12px); border-color: var(--line-soft, #e6ebf2); box-shadow: var(--shadow-card, 0 4px 16px rgba(24, 44, 72, .05)); }
.workbench-table :deep(.el-table__header th) { background: #f7f9fc; color: #617089; font-size: 12px; }
.workbench-table :deep(.el-table__row:hover > td) { background: #f4f8ff !important; }
.quote-status-banner { display: flex; align-items: center; gap: 8px; min-height: 34px; padding: 7px 10px; margin-bottom: 12px; border: 1px solid #cbe9dc; border-radius: 8px; background: #f1fbf6; color: #236a50; font-size: 12px; }
.quote-status-banner.degraded { border-color: #f0d7a5; background: #fff9ed; color: #8b5b00; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: #2fbb82; box-shadow: 0 0 0 3px rgba(47, 187, 130, .12); }
.degraded .status-dot { background: #d99518; box-shadow: 0 0 0 3px rgba(217, 149, 24, .12); }
@media (max-width: 768px) { .quote-status-banner { align-items: flex-start; flex-wrap: wrap; } .quote-status-banner .grow { display: none; } }
</style>
