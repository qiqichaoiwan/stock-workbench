<template>
  <div>
    <el-card class="page-card">
      <template #header>交易复盘统计(绝对收益口径;风控框架负责"不死",alpha 需另行验证)</template>
      <el-descriptions :column="6" size="small" v-if="stats.count > 0" border>
        <el-descriptions-item label="交易数">{{ fmtInt(stats.count) }}</el-descriptions-item>
        <el-descriptions-item label="胜率">{{ fmtPct(stats.win_rate) }}</el-descriptions-item>
        <el-descriptions-item label="平均盈利">
          <span class="up">{{ fmtNum(stats.avg_win) }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="平均亏损">
          <span class="down">{{ fmtNum(stats.avg_loss) }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="盈亏比">{{ fmtNum(stats.profit_factor) }}</el-descriptions-item>
        <el-descriptions-item label="总盈亏">
          <span :class="upDownClass(stats.total_pnl)">{{ fmtNum(stats.total_pnl) }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <el-empty v-else description="暂无交易记录,卖出持仓后自动生成" :image-size="70" />
    </el-card>

    <el-card class="page-card">
      <template #header>卖出历史(含费盈亏 · 离场触发原因 · 可审计)</template>
      <el-table :data="rows" size="small" v-loading="loading" max-height="560" stripe>
        <el-table-column prop="sell_date" label="卖出日" width="105" />
        <el-table-column prop="code" label="代码" width="85" />
        <el-table-column label="名称" min-width="100">
          <template #default="{ row }">{{ cleanName(row.name) }}</template>
        </el-table-column>
        <el-table-column label="买入价" width="85" align="right">
          <template #default="{ row }">{{ fmtNum(row.buy_price) }}</template>
        </el-table-column>
        <el-table-column label="卖出价" width="85" align="right">
          <template #default="{ row }">{{ fmtNum(row.sell_price) }}</template>
        </el-table-column>
        <el-table-column label="数量" width="90" align="right">
          <template #default="{ row }">{{ fmtInt(row.quantity) }}</template>
        </el-table-column>
        <el-table-column label="盈亏" width="130" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.pnl)">{{ fmtNum(row.pnl) }} ({{ fmtPct(row.pnl_pct) }})</span>
          </template>
        </el-table-column>
        <el-table-column prop="holding_days" label="持有天数" width="90" align="right">
          <template #default="{ row }">{{ fmtInt(row.holding_days) }}</template>
        </el-table-column>
        <el-table-column label="触发" width="110">
          <template #default="{ row }">
            <el-tag :type="triggerType(row.exit_trigger)" size="small">{{ triggerLabel(row.exit_trigger) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import api from '../api'
import { cleanName, fmtInt, fmtNum, fmtPct, upDownClass } from '../utils/fmt'

const rows = ref([])
const stats = ref({ count: 0 })
const loading = ref(false)
const triggerLabels = { stop_loss: '初始止损', stop_breakeven: '保本离场', trailing: '移动止损', manual: '手动', ai_ref: 'AI参考' }
function triggerLabel(t) { return triggerLabels[t] || t }
function triggerType(t) {
  return { stop_loss: 'warning', stop_breakeven: 'info', trailing: 'success', manual: 'info', ai_ref: 'danger' }[t] || 'info'
}

onMounted(async () => {
  loading.value = true
  try {
    rows.value = await api.get('/api/trades')
    stats.value = await api.get('/api/trades/stats')
  } finally {
    loading.value = false
  }
})
</script>
