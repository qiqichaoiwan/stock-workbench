<template>
  <div>
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>龙虎榜</span>
          <span class="grow" />
          <el-button size="small" @click="load">刷新</el-button>
        </div>
      </template>
      <div class="table-scroll">
      <el-table :data="lhb" size="small" v-loading="lhbLoading" stripe border>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column prop="name" label="名称" min-width="110" show-overflow-tooltip />
        <el-table-column label="收盘" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.close) }}</template>
        </el-table-column>
        <el-table-column label="涨跌幅" width="100" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.pct_chg)">{{ fmtPct(row.pct_chg) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="龙虎净额" width="120" align="right">
          <template #default="{ row }">{{ fmtNum(row.lhb_net) }}</template>
        </el-table-column>
        <el-table-column label="命中游资" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="t in tradersOf(row)" :key="t" size="small" type="warning" style="margin:2px">{{ t }}</el-tag>
            <span v-if="!tradersOf(row).length" class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="trade_date" label="交易日" width="110" />
      </el-table>
      </div>
      <el-empty v-if="!lhbLoading && !lhb.length" description="暂无龙虎榜数据" :image-size="60">
        <template #description>
          <p>暂无龙虎榜数据,收盘后 17:10 lhb_sync 落库后可见</p>
          <p class="muted" style="font-size:12px">周末/非交易日无数据属预期;交易日晚间仍空请去任务中心手动运行 lhb_sync</p>
        </template>
      </el-empty>
    </el-card>

    <el-card class="page-card">
      <template #header>游资席位</template>
      <div class="table-scroll">
      <el-table :data="seats" size="small" v-loading="seatLoading" stripe border>
        <el-table-column prop="seat" label="席位" min-width="170" show-overflow-tooltip />
        <el-table-column prop="trader" label="游资" width="130" />
        <el-table-column prop="style" label="风格" min-width="150" show-overflow-tooltip />
        <el-table-column prop="risk" label="风险" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="row.risk && String(row.risk).includes('高') ? 'danger' : 'info'">{{ row.risk || '-' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      </div>
      <el-empty v-if="!seatLoading && !seats.length" description="暂无游资席位数据" :image-size="60" />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import api from '../api'
import { fmtNum, fmtPct, upDownClass } from '../utils/fmt'

const lhb = ref([])
const seats = ref([])
const lhbLoading = ref(false)
const seatLoading = ref(false)
let seq = 0

function tradersOf(row) {
  const v = row.matched_traders
  if (Array.isArray(v)) return v.filter(Boolean)
  if (typeof v === 'string' && v.trim()) {
    try {
      const arr = JSON.parse(v)
      if (Array.isArray(arr)) return arr.filter(Boolean)
    } catch { /* 非 JSON 则按分隔符切分 */ }
    return v.split(/[,;，；|]/).map((s) => s.trim()).filter(Boolean)
  }
  return []
}

async function load() {
  const cur = (seq += 1)
  lhbLoading.value = seatLoading.value = true
  try {
    const [a, b] = await Promise.all([
      api.get('/api/screener/lhb', { silent: true }).catch(() => []),
      api.get('/api/screener/hot-money-seats', { silent: true }).catch(() => []),
    ])
    if (cur !== seq) return
    lhb.value = Array.isArray(a) ? a : []
    seats.value = Array.isArray(b) ? b : []
  } finally {
    if (cur === seq) lhbLoading.value = seatLoading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => { seq += 1 })
</script>

<style scoped>
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
</style>
