<template>
  <div>
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>任务中心(非交易日自动跳过;每步失败自动重试并写日志)</span>
          <span class="grow" />
          <el-button v-if="canOperate" type="primary" size="small" @click="dialog = true">手动触发</el-button>
        </div>
      </template>
      <el-table :data="pagedLogs" size="small" v-loading="loading" max-height="560" stripe border>
        <el-table-column prop="id" label="#" width="70" sortable />
        <el-table-column label="任务" width="170">
          <template #default="{ row }">{{ taskDesc[row.task_name] || row.task_name }}</template>
        </el-table-column>
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ fmtDT(row.run_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="90" align="right">
          <template #default="{ row }">{{ fmtDuration(row.duration_ms) }}</template>
        </el-table-column>
        <el-table-column prop="detail" label="结果" show-overflow-tooltip />
      </el-table>
      <el-empty v-if="!loading && !logs.length" description="暂无任务日志" :image-size="60">
        <template #description>
          <p>暂无任务日志,按初始化顺序手动触发后可见</p>
          <p class="muted" style="font-size:12px">建议顺序:sync_members → sync_basic → sync_tracks → backfill_history → sync_financials → 其余任务</p>
        </template>
      </el-empty>
      <div class="pager">
        <el-pagination layout="total, prev, pager, next" :total="logs.length" :page-size="pageSize"
                       v-model:current-page="page" />
      </div>
      <p class="muted" style="font-size:12px;margin:6px 0 0">前端分页 · 共 {{ logs.length }} 条(服务端 limit=200 取回)</p>
    </el-card>

    <el-dialog v-model="dialog" title="手动触发任务" width="440">
      <el-select v-model="taskName" placeholder="选择任务" style="width:100%">
        <el-option v-for="(t, name) in taskDesc" :key="name" :label="t" :value="name" />
      </el-select>
      <p class="muted" style="font-size:12px">首次初始化建议顺序:sync_members → sync_basic → sync_tracks → backfill_history → sync_financials → 其余任务</p>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="running" @click="run">执行(后台任务可能耗时数分钟)</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { fmtDT, fmtDuration } from '../utils/fmt'
import { useUserStore } from '../store'

const store = useUserStore()
const canOperate = computed(() => store.isAnalyst)
const logs = ref([])
const loading = ref(false)
const dialog = ref(false)
const taskName = ref('')
const running = ref(false)
const page = ref(1)
const pageSize = 20
const pagedLogs = computed(() => logs.value.slice((page.value - 1) * pageSize, page.value * pageSize))
let pollTimer = null
let pollToken = 0

const taskDesc = {
  sync_members: '同步指数成分(候选域)',
  sync_basic: '同步股票基础信息',
  sync_tracks: '同步赛道成分',
  tracks_backfill: '一键回填赛道成分',
  sync_quotes: '收盘快照落库(日线)',
  intraday_snapshot: '盘中快照',
  sync_valuation: '估值快照',
  industry_daily: '行业日度监测+拥挤度',
  commodities: '商品涨价跟踪',
  market_state: '市场状态计算',
  signals: '技术信号扫描',
  sync_financials: '财务增量(业绩报表)',
  strategy: '策略筛选+入池出池',
  news: '新闻采集甄别',
  weekly_reports: '生成周报(全体用户)',
  backfill_history: '历史日线回填(慢,数十年)',
}
const statusLabels = { success: '成功', failed: '失败', running: '运行中', skipped: '跳过' }
function statusLabel(s) { return statusLabels[s] || s }
function statusType(s) {
  return { success: 'success', failed: 'danger', running: 'primary', skipped: 'info' }[s] || 'info'
}

onMounted(load)
onBeforeUnmount(() => {
  pollToken += 1
  if (pollTimer) clearTimeout(pollTimer)
})

async function load() {
  loading.value = true
  try {
    const data = await api.get('/api/screener/tasks', { params: { limit: 200 }, silent: true })
    logs.value = Array.isArray(data) ? data : (data?.items || data?.data || [])
    page.value = Math.min(page.value, Math.max(1, Math.ceil(logs.value.length / pageSize)))
  } finally {
    loading.value = false
  }
}

async function run() {
  if (!taskName.value || running.value) return
  running.value = true
  pollToken += 1
  const token = pollToken
  if (pollTimer) clearTimeout(pollTimer)
  try {
    const target = taskName.value
    const startedId = Math.max(0, ...logs.value.map((l) => Number(l.id) || 0))
    const r = await api.post(`/api/screener/tasks/${target}/run`, { years: 5 })
    ElMessage.success(r.message || '已提交后台执行')
    dialog.value = false
    let tries = 0
    const poll = async () => {
      if (token !== pollToken) return
      tries += 1
      try { await load() } catch { /* 请求错误由 API 层提示 */ }
      if (token !== pollToken) return
      const hit = logs.value.find((l) => l.task_name === target && Number(l.id) > startedId && l.status !== 'running')
      if (hit) {
        ElMessage({ type: hit.status === 'success' ? 'success' : 'error', message: `${taskDesc[target] || target} ${statusLabel(hit.status)}(${fmtDuration(hit.duration_ms, '未知')})` })
        pollTimer = null
        return
      }
      if (tries < 36) pollTimer = setTimeout(poll, 5000)
      else pollTimer = null
    }
    pollTimer = setTimeout(poll, 1000)
  } finally {
    running.value = false
  }
}
</script>
