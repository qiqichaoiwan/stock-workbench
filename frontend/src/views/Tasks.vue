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
        <el-table-column label="任务" min-width="190" show-overflow-tooltip>
          <template #default="{ row }">{{ taskLabel(row.task_name) }}</template>
        </el-table-column>
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ fmtDT(row.run_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row)" size="small">{{ statusLabel(row) }}</el-tag>
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
          <p class="muted" style="font-size:12px">建议顺序:{{ INIT_ORDER_TEXT }}</p>
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
        <el-option v-for="(t, name) in TASK_NAMES" :key="name" :label="t" :value="name" />
      </el-select>
      <div v-if="taskName === 'backfill_history'" style="margin-top:12px">
        <span class="muted" style="font-size:12px;margin-right:6px">回填年数</span>
        <el-input-number v-model="years" :min="1" :max="20" size="small" />
        <p class="muted" style="font-size:12px;margin:6px 0 0">
          增量回填:库内已有的日期自动跳过,重复执行只补最新数据;首次 5 年约需 20-40 分钟,
          进度可直接在本页日志查看(支持后台运行,关闭页面不影响)。
        </p>
      </div>
      <p class="muted" style="font-size:12px">首次初始化建议顺序:{{ INIT_ORDER_TEXT }}</p>
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
import { INIT_ORDER_TEXT, TASK_NAMES, taskName as taskLabel } from '../utils/tasks'
import { useUserStore } from '../store'

const store = useUserStore()
const canOperate = computed(() => store.isAnalyst)
const logs = ref([])
const loading = ref(false)
const dialog = ref(false)
const taskName = ref('')
const years = ref(5)
const running = ref(false)
const page = ref(1)
const pageSize = 20
const pagedLogs = computed(() => logs.value.slice((page.value - 1) * pageSize, page.value * pageSize))
let pollTimer = null
let autoTimer = null
let pollToken = 0

const statusLabels = { success: '成功', failed: '失败', running: '运行中', skipped: '跳过' }
function isPartial(row) {
  return row.status === 'failed' && row.data_quality === 'degraded'
}
function statusLabel(row) {
  if (isPartial(row)) return '部分完成'
  return statusLabels[row.status] || row.status
}
function statusType(row) {
  if (isPartial(row)) return 'warning'
  return { success: 'success', failed: 'danger', running: 'primary', skipped: 'info' }[row.status] || 'info'
}

onMounted(() => {
  load()
  // 后台任务(如历史回填)运行时自动刷新进度,无需手动点触发
  autoTimer = setInterval(() => {
    if (logs.value.some((l) => l.status === 'running')) load()
  }, 10000)
})
onBeforeUnmount(() => {
  pollToken += 1
  if (pollTimer) clearTimeout(pollTimer)
  if (autoTimer) clearInterval(autoTimer)
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
    const body = target === 'backfill_history' ? { years: years.value } : {}
    const r = await api.post(`/api/screener/tasks/${target}/run`, body)
    ElMessage.success(r.message || '已提交后台执行')
    dialog.value = false
    // 回填首次全量可能 20-40 分钟:放宽轮询,进度见运行中日志的 detail
    const maxTries = target === 'backfill_history' ? 1200 : 60
    const interval = target === 'backfill_history' ? 10000 : 5000
    let tries = 0
    const poll = async () => {
      if (token !== pollToken) return
      tries += 1
      try { await load() } catch { /* 请求错误由 API 层提示 */ }
      if (token !== pollToken) return
      const hit = logs.value.find((l) => l.task_name === target && Number(l.id) > startedId && l.status !== 'running')
      if (hit) {
        const type = hit.status === 'success' ? 'success' : isPartial(hit) ? 'warning' : 'error'
        ElMessage({ type, message: `${taskLabel(target)} ${statusLabel(hit)}(${fmtDuration(hit.duration_ms, '未知')})` })
        pollTimer = null
        return
      }
      if (tries < maxTries) pollTimer = setTimeout(poll, interval)
      else {
        ElMessage.info(`${taskLabel(target)} 仍在后台运行,可稍后刷新本页查看结果`)
        pollTimer = null
      }
    }
    pollTimer = setTimeout(poll, 1000)
  } finally {
    running.value = false
  }
}
</script>
