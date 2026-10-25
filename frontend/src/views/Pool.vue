<template>
  <div>
    <el-alert v-if="riskGuard" type="warning" :closable="false" style="margin-bottom:16px"
              title="弱市守门已触发:主池暂停新增入选(存量继续按评级跟踪),此为规则层硬约束" />

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
          <span class="grow" />
          <el-input v-model="search" placeholder="搜索代码 / 名称" clearable style="width:200px" />
        </div>
      </template>
      <el-table :data="pagedRows" size="small" v-loading="loading" stripe>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column label="名称" min-width="110">
          <template #default="{ row }">{{ cleanName(row.name) }}</template>
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
      <div class="pager">
        <el-pagination layout="total, sizes, prev, pager, next" :total="filteredRows.length"
                       :page-sizes="[20, 50, 100]" v-model:page-size="pageSize"
                       v-model:current-page="page" />
      </div>
    </el-card>

    <el-card class="page-card">
      <template #header>出池留档(含原因,可回溯)</template>
      <el-table :data="history" size="small" max-height="300" stripe>
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
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { cleanName, fmtNum, ratingLabel, ratingType } from '../utils/fmt'
import { useUserStore } from '../store'

const store = useUserStore()
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
const riskGuard = ref(false)

const filteredRows = computed(() => {
  const kw = search.value.trim().toLowerCase()
  if (!kw) return rows.value
  return rows.value.filter((r) => r.code.includes(kw) || String(r.name || '').toLowerCase().includes(kw))
})
const pagedRows = computed(() =>
  filteredRows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))

watch(search, () => { page.value = 1 })

async function load() {
  loading.value = true
  try {
    const q = { pool_type: poolType.value }
    if (poolType.value === 'track' && trackId.value) q.track_id = trackId.value
    rows.value = await api.get('/api/screener/pool', { params: q })
    page.value = 1
  } finally {
    loading.value = false
  }
}

async function forceOut(row) {
  await api.post(`/api/screener/pool/${row.id}/out`, null, { params: { reason: 'manual' } })
  ElMessage.success('已出池')
  await Promise.all([load(), loadHistory()])
}

async function loadHistory() {
  history.value = await api.get('/api/screener/pool/history')
}

onMounted(async () => {
  tracks.value = await api.get('/api/screener/tracks')
  await loadHistory()
  await load()
  // 弱市守门横幅(来自仪表盘同一数据源)
  try {
    const d = await api.get('/api/screener/dashboard')
    riskGuard.value = !!d.risk_guard
  } catch { riskGuard.value = false }
})
</script>
