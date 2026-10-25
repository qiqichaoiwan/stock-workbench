<template>
  <el-card class="page-card">
    <template #header>
      <div class="card-head">
        <span>技术信号流(相对口径:底分型/连红/量能偏离/相对强弱)</span>
        <span class="grow" />
        <el-select v-model="sigType" placeholder="全部类型" clearable style="width:180px">
          <el-option v-for="(label, key) in labels" :key="key" :label="label" :value="key" />
        </el-select>
      </div>
    </template>
    <el-table :data="pagedRows" size="small" v-loading="loading" max-height="620" stripe>
      <el-table-column prop="trade_date" label="日期" width="110" />
      <el-table-column prop="code" label="代码" width="90" />
      <el-table-column label="名称" width="100">
        <template #default="{ row }">{{ cleanName(row.name) || '-' }}</template>
      </el-table-column>
      <el-table-column label="信号" width="120">
        <template #default="{ row }">
          <el-tag :type="row.signal_type === 'buy_setup' ? 'danger' : row.signal_type === 'rs_weak' ? 'warning' : 'info'" size="small">
            {{ labels[row.signal_type] || row.signal_type }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="强度" width="90" sortable prop="strength">
        <template #default="{ row }">{{ fmtNum(row.strength, 1) }}</template>
      </el-table-column>
      <el-table-column label="明细" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="muted">{{ detailText(row.detail) }}</span>
        </template>
      </el-table-column>
    </el-table>
    <div class="pager">
      <el-pagination layout="total, prev, pager, next" :total="rows.length" :page-size="pageSize"
                     v-model:current-page="page" />
    </div>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import api from '../api'
import { cleanName, fmtNum } from '../utils/fmt'

const rows = ref([])
const sigType = ref('')
const loading = ref(false)
const page = ref(1)
const pageSize = 20
const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const labels = {
  buy_setup: '入场组合', bottom_fractal: '底分型', red_streak: '连红',
  volume_surge: '量能放大', rs_strong: '强于行业', rs_weak: '弱于行业',
}

function detailText(d) {
  if (!d) return ''
  const parts = []
  if (d.streak) parts.push(`连红${d.streak}天`)
  if (d.vol_dev) parts.push(`量能偏离${d.vol_dev}%`)
  if (d.industry) parts.push(`行业:${d.industry}(20日${d.industry_mom20 ?? '-'}%)`)
  if (d.note) parts.push(d.note)
  return parts.join(' · ')
}

async function load() {
  loading.value = true
  try {
    rows.value = await api.get('/api/screener/signals',
      { params: { signal_type: sigType.value || undefined, limit: 300 } })
    page.value = 1
  } finally {
    loading.value = false
  }
}

watch(sigType, load)
onMounted(load)
</script>
