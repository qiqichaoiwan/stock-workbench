<template>
  <div>
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>我的持仓(G03 三阶段止损 · 硬规则代码强制)</span>
          <span class="grow" />
          <el-button type="primary" size="small" @click="dialog = true">录入持仓</el-button>
        </div>
      </template>
      <el-table :data="rows" size="small" v-loading="loading" stripe>
        <el-table-column prop="code" label="代码" width="85" />
        <el-table-column label="名称" min-width="100">
          <template #default="{ row }">{{ cleanName(row.name) }}</template>
        </el-table-column>
        <el-table-column label="买入价" width="85" align="right">
          <template #default="{ row }">{{ fmtNum(row.buy_price) }}</template>
        </el-table-column>
        <el-table-column label="现价" width="85" align="right">
          <template #default="{ row }">{{ fmtNum(row.close) }}</template>
        </el-table-column>
        <el-table-column label="浮盈%" width="90" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.unrealized_pct)">{{ fmtPct(row.unrealized_pct) }}</span>
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
            <el-tag v-if="row.at_risk" type="danger" size="small">已破止损</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link :loading="row._ai" @click="ai(row)">AI分析</el-button>
            <el-button size="small" type="danger" link @click="openSell(row)">卖出</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !rows.length" description="暂无持仓,点击右上角「录入持仓」开始" :image-size="70" />
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
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { cleanName, fmtNum, fmtPct, ratingLabel, ratingType, upDownClass } from '../utils/fmt'

const rows = ref([])
const loading = ref(false)
const dialog = ref(false)
const sellDialog = ref(false)
const aiDialog = ref(false)
const aiResult = ref(null)
const saving = ref(false)
const selling = ref(false)
const current = ref(null)
const formRef = ref()
const form = ref({ code: '', buy_price: 10, quantity: 100, buy_date: new Date().toISOString().slice(0, 10), stop_pct: 10, note: '' })
const sellForm = ref({ sell_price: 0, sell_date: new Date().toISOString().slice(0, 10), note: '' })

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

onMounted(load)

async function load() {
  loading.value = true
  try {
    rows.value = await api.get('/api/holdings')
  } finally {
    loading.value = false
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
  sellForm.value = { sell_price: row.close, sell_date: new Date().toISOString().slice(0, 10), note: '' }
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
