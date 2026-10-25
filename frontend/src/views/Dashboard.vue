<template>
  <div v-loading="loading">
    <el-card v-if="indexes.length" class="page-card" style="margin-bottom:16px">
      <div style="display:flex;gap:16px;overflow-x:auto">
        <div v-for="ix in indexes" :key="ix.code" style="min-width:140px">
          <div class="muted" style="font-size:12px">{{ ix.name }}</div>
          <div style="font-size:18px;font-weight:700">{{ fmtNum(ix.close) }}</div>
          <div :class="upDownClass(ix.pct_chg)" style="font-size:12px">{{ fmtPct(ix.pct_chg) }} · {{ ix.trade_date }}</div>
        </div>
      </div>
    </el-card>
    <el-row :gutter="16">
      <el-col :xs="24" :sm="24" :md="8">
        <el-card class="page-card state-card">
          <template #header>市场状态</template>
          <template v-if="d.market_state">
            <div class="stat-num">{{ d.market_state.trend_label }} · {{ d.market_state.cycle_stage }}</div>
            <div class="stat-sub">建议仓位 {{ d.market_state.position_range }}(PM02 草案,待验证)</div>
            <el-progress :percentage="positionPct" :stroke-width="14" :show-text="false"
                         :color="positionColor" style="margin:10px 0 4px" />
            <el-descriptions :column="2" size="small" style="margin-top:8px">
              <el-descriptions-item label="日期">{{ d.market_state.trade_date }}</el-descriptions-item>
              <el-descriptions-item label="市场宽度">{{ fmtNum(d.market_state.breadth, 1) }}%</el-descriptions-item>
              <el-descriptions-item label="成交额60日分位">{{ fmtNum(d.market_state.amount_pct_60d, 1) }}%</el-descriptions-item>
              <el-descriptions-item label="全A估值分位">{{ fmtNum(d.market_state.valuation_pct, 1) }}%</el-descriptions-item>
              <el-descriptions-item label="融资/成交额">{{ fmtNum(d.market_state.margin_ratio, 4) }}</el-descriptions-item>
              <el-descriptions-item label="风险收紧">
                <el-tag :type="d.risk_guard ? 'danger' : 'success'" size="small">{{ d.risk_guard ? '生效' : '未触发' }}</el-tag>
              </el-descriptions-item>
            </el-descriptions>
          </template>
          <el-empty v-else-if="loadError" description="市场状态加载失败，请检查网络后重试" :image-size="60">
            <el-button size="small" type="primary" @click="load">重试</el-button>
          </el-empty>
          <el-empty v-else description="暂无市场状态,请先在任务中心运行 market_state 任务" :image-size="60" />
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="24" :md="8">
        <el-card class="page-card state-card">
          <template #header>
            拥挤度回避名单(SC-U08,待验证)
            <el-tag v-if="d.market_wide_crowded" type="danger" size="small">全市场过度拥挤,建议整体降仓</el-tag>
          </template>
          <el-tag v-for="name in d.crowd_avoid" :key="name" type="danger" style="margin:4px">{{ name }}</el-tag>
          <el-empty v-if="!d.crowd_avoid?.length" description="当前无拥挤行业" :image-size="60"
                    title="SC-U08:行业成交占比偏离自身 MA120 超 300% 即列入回避" />
        </el-card>
        <el-card class="page-card state-card">
          <template #header>商品涨价信号(供需跟踪)</template>
          <el-table :data="commodities" size="small" max-height="220" stripe border>
            <el-table-column prop="series_name" label="品种" width="90" />
            <el-table-column label="信号" width="80">
              <template #default="{ row }">
                <el-tag :type="row.signal === '启动' ? 'danger' : row.signal === '持续' ? 'warning' : 'info'"
                        size="small">{{ row.signal || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="20日动量%" width="100" align="right">
              <template #default="{ row }">
                <span :class="upDownClass(row.mom_20)">{{ fmtPct(row.mom_20) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="related_track" label="关联赛道" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="24" :md="8">
        <el-card class="page-card state-card">
          <template #header>选股池表现(自入选累计,绝对/相对双口径)</template>
          <template v-if="d.pool_perf?.trade_date">
            <div class="stat-num" :class="upDownClass(d.pool_perf.avg_cum_pct)">
              {{ fmtPct(d.pool_perf.avg_cum_pct) }}
            </div>
            <div class="stat-sub">
              {{ fmtInt(d.pool_perf.count) }} 只 · 相对全A超额
              <span :class="upDownClass(d.pool_perf.avg_excess_pct)">{{ fmtPct(d.pool_perf.avg_excess_pct) }}</span>
              · {{ d.pool_perf.trade_date }}
            </div>
          </template>
          <el-empty v-else description="暂无池表现数据" :image-size="60" />
        </el-card>
        <el-card class="page-card state-card">
          <template #header>今日技术信号<span v-if="signalDate" class="muted" style="font-size:12px;margin-left:8px">{{ signalDate }}</span></template>
          <el-table :data="d.signals" size="small" max-height="240" stripe border>
            <el-table-column prop="code" label="代码" width="85" />
          <el-table-column label="名称" min-width="90">
            <template #default="{ row }">{{ nameOrCode(row) }}</template>
          </el-table-column>
            <el-table-column label="信号" width="120">
              <template #default="{ row }">
                <el-tag :type="row.type === 'buy_setup' ? 'danger' : row.type === 'rs_weak' ? 'warning' : 'info'"
                        size="small">{{ typeLabel(row.type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="强度" align="right">
              <template #default="{ row }">{{ fmtNum(row.strength, 1) }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!d.signals?.length" description="今日暂无信号" :image-size="50" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import api from '../api'
import { cleanName, fmtInt, fmtNum, fmtPct, upDownClass } from '../utils/fmt'

const d = ref({})
const indexes = ref([])
const loading = ref(false)
const loadError = ref(false)
let loadSeq = 0
const labels = {
  buy_setup: '入场组合', bottom_fractal: '底分型', red_streak: '连红',
  volume_surge: '量能放大', rs_strong: '强于行业', rs_weak: '弱于行业',
}
function typeLabel(t) { return labels[t] || t }

// 建议仓位区间(如 "50%-70%" / "50%~70%" / "50%—70%")取中值作可视化
// 兼容:整数/小数、两侧 %、半角连字符、波浪线、中文破折号(——/—/–)
const positionPct = computed(() => {
  const m = String(d.value.market_state?.position_range || '')
    .match(/(\d+(?:\.\d+)?)\s*%?\s*[-~～—–─―﹣－]+\s*(\d+(?:\.\d+)?)\s*%?/)
  if (!m) return 0
  return Math.round((Number(m[1]) + Number(m[2])) / 2)
})
const positionColor = computed(() =>
  (positionPct.value >= 70 ? '#f56c6c' : positionPct.value >= 40 ? '#e6a23c' : '#67c23a'))

// 商品表:仅展示有信号或有动量的品种,避免整表无信号占位
const commodities = computed(() => {
  const list = d.value.rising_commodities || []
  return list.filter((c) => c.signal || Math.abs(Number(c.mom_20) || 0) > 0.5).slice(0, 8)
})

// 信号缺名:批量后仍缺则回退显示 code，避免整列 '-'
function nameOrCode(row) {
  return cleanName(row.name) || row.code || '-'
}

// 今日技术信号日期标题:取返回的最大 trade_date(后端已按时间倒序)
const signalDate = computed(() => {
  const dates = (d.value.signals || []).map((s) => s.trade_date).filter(Boolean)
  return dates.length ? dates.sort().at(-1) : ''
})

onMounted(load)
onBeforeUnmount(() => { loadSeq += 1 })

async function load() {
  const seq = (loadSeq += 1)
  loading.value = true
  loadError.value = false
  try {
    const data = await api.get('/api/screener/dashboard', { silent: true })
    if (seq !== loadSeq) return // 竞态:丢弃过期响应
    d.value = data || {}
    try {
      const ix = await api.get('/api/screener/indexes', { silent: true })
      if (seq === loadSeq) indexes.value = Array.isArray(ix) ? ix : []
    } catch { if (seq === loadSeq) indexes.value = [] } // 指数无数据整条隐藏,不弹错
  } catch {
    if (seq !== loadSeq) return
    loadError.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}
</script>

<style scoped>
.state-card { min-height: 240px; }
</style>
