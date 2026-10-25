<template>
  <div>
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>命中提醒</span>
          <span class="grow" />
          <el-button size="small" @click="loadAlerts">刷新</el-button>
        </div>
      </template>
      <div v-if="alerts.length">
        <el-alert v-for="a in alerts" :key="a.code + a.rule + a.created_at"
                  type="warning" :closable="false" show-icon style="margin-bottom:8px"
                  :title="`${a.code} ${a.rule}`" :description="`${a.message || ''} ${a.created_at || ''}`" />
      </div>
      <el-empty v-else description="暂无命中提醒" :image-size="60">
        <template #description>
          <p>暂无命中提醒,价格触及三价或涨跌幅阈值后自动出现</p>
          <p class="muted" style="font-size:12px">由盘中 watch_alert 任务每 30 分钟扫描;提醒只读不改止损,仅提示</p>
        </template>
      </el-empty>
    </el-card>

    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>自选预警</span>
          <span class="grow" />
          <el-button type="primary" size="small" @click="openAdd">新增自选</el-button>
        </div>
      </template>
      <div class="table-scroll">
      <el-table :data="rows" size="small" v-loading="loading" stripe border>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column prop="name" label="名称" min-width="110" show-overflow-tooltip />
        <el-table-column label="现价" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.close) }}</template>
        </el-table-column>
        <el-table-column label="涨跌幅" width="100" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.pct_chg)">{{ fmtPct(row.pct_chg) }}</span>
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
      <el-empty v-if="!loading && !rows.length" description="暂无自选,点击右上新增" :image-size="60">
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
import { fmtNum, fmtPct, upDownClass } from '../utils/fmt'

const rows = ref([])
const alerts = ref([])
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

onMounted(() => { load(); loadAlerts() })
onBeforeUnmount(() => { seq += 1 })

async function load() {
  const cur = (seq += 1)
  loading.value = true
  try {
    const data = await api.get('/api/watchlist', { silent: true }).catch(() => [])
    if (cur !== seq) return
    rows.value = Array.isArray(data) ? data : []
  } finally {
    if (cur === seq) loading.value = false
  }
}

async function loadAlerts() {
  try {
    const data = await api.get('/api/watchlist/alerts', { silent: true }).catch(() => [])
    alerts.value = Array.isArray(data) ? data : []
  } catch { alerts.value = [] }
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
  saving.value = true
  try {
    const payload = {
      code: form.code,
      cost_price: form.cost_price, take_profit: form.take_profit,
      stop_loss: form.stop_loss, alert_pct: form.alert_pct, note: form.note || '',
    }
    if (editing.value) await api.put(`/api/watchlist/${editing.value.id}`, payload, { silent: true })
    else await api.post('/api/watchlist', payload, { silent: true })
    ElMessage.success('已保存')
    dlg.value = false
    await load()
  } catch { /* silent 请求由调用方兜底:保存失败保持对话框,不抛全局 */ } finally {
    saving.value = false
  }
}

async function remove(row) {
  try {
    await api.delete(`/api/watchlist/${row.id}`, { silent: true })
    ElMessage.success('已删除')
    await load()
  } catch { /* silent:失败静默 */ }
}
</script>

<style scoped>
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
</style>
