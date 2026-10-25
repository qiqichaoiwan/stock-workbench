<template>
  <div>
    <!-- 市场总览卡(无数据隐藏) -->
    <el-card v-if="hasStat" class="page-card">
      <template #header>
        <div class="card-head">
          <span>市场总览</span>
          <span class="grow" />
          <span class="muted" style="font-size:12px">{{ stat.snap_time || '' }} · 来源:{{ stat.source || '-' }}</span>
        </div>
      </template>
      <el-row :gutter="12">
        <el-col :xs="12" :sm="6"><el-statistic title="上涨家数" :value="stat.advance ?? '-'" /></el-col>
        <el-col :xs="12" :sm="6"><el-statistic title="下跌家数" :value="stat.decline ?? '-'" /></el-col>
        <el-col :xs="12" :sm="6"><el-statistic title="涨停 / 跌停" :value="limitText" /></el-col>
        <el-col :xs="12" :sm="6"><el-statistic title="成交额(亿)" :value="amountText" /></el-col>
      </el-row>
      <p v-if="stat.source_note" class="muted" style="font-size:12px;margin:8px 0 0">数据来源：{{ stat.source_note }}</p>
    </el-card>

    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>主要指数</span>
          <span class="grow" />
          <span class="muted" style="font-size:12px">收盘后 16:45 sync_indexes 落库 · 无数据不报错</span>
        </div>
      </template>
      <div class="table-scroll">
      <el-table :data="indexes" size="small" v-loading="ixLoading" stripe border>
        <el-table-column prop="name" label="指数" min-width="110" />
        <el-table-column prop="code" label="代码" width="100" />
        <el-table-column label="点位" width="120" align="right">
          <template #default="{ row }">{{ fmtNum(row.close) }}</template>
        </el-table-column>
        <el-table-column label="涨跌幅" width="110" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.pct_chg)">{{ fmtPct(row.pct_chg) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="成交额(亿)" width="130" align="right">
          <template #default="{ row }">{{ row.amount ? fmtNum(row.amount / 1e8) : '-' }}</template>
        </el-table-column>
        <el-table-column prop="trade_date" label="日期" width="110" />
        <el-table-column prop="source" label="来源" width="90" />
      </el-table>
      </div>
      <el-empty v-if="!ixLoading && !indexes.length" description="暂无指数数据,请先在任务中心运行 sync_indexes" :image-size="60">
        <template #description>
          <p>暂无指数数据,请先在任务中心运行 sync_indexes 后可见</p>
          <p class="muted" style="font-size:12px">指数收盘后 16:45 落库,盘中为空属正常;周末/非交易日无新增属预期</p>
        </template>
      </el-empty>
    </el-card>

    <!-- 行业资金:调 /fund-flow,点击行拉 history 画迷你趋势 -->
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>板块资金</span>
          <span class="grow" />
          <el-radio-group v-model="boardType" size="small" @change="loadFlow">
            <el-radio-button value="industry">行业</el-radio-button>
            <el-radio-button value="concept">概念</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <div class="table-scroll">
      <el-table :data="flows" size="small" v-loading="flowLoading" stripe border
                highlight-current-row @row-click="onFlowRow">
        <el-table-column prop="board_name" label="板块" min-width="130" show-overflow-tooltip />
        <el-table-column label="净流入(亿)" width="130" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.net_inflow)">{{ fmtNum(row.net_inflow) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="snap_time" label="快照时间" width="170" />
        <el-table-column prop="source" label="来源" width="90" />
      </el-table>
      </div>
      <el-empty v-if="!flowLoading && !flows.length" description="暂无板块资金数据" :image-size="60">
        <template #description>
          <p>暂无板块资金数据,请先在任务中心运行 fund_flow 后可见</p>
          <p class="muted" style="font-size:12px">盘中快照 9:35 起每 30 分钟更新;点击表格行可看该板块近 30 日净流入趋势</p>
        </template>
      </el-empty>

      <!-- 选中板块近30日迷你趋势(div 条形 + 原生 table,不引入图表库) -->
      <div v-if="flowSel" style="margin-top:12px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <b>{{ flowSel }}</b>
          <span class="muted" style="font-size:12px">近30日净流入(亿,点击上方行切换)</span>
          <span class="grow" />
          <el-button size="small" link @click="flowSel = ''; flowHist = []">收起</el-button>
        </div>
        <div v-loading="histLoading">
          <div class="mini-bars">
            <el-tooltip v-for="h in flowHist" :key="h.snap_date"
                        :content="`${h.snap_date}: ${fmtNum(h.net_inflow)}`" placement="top">
              <div class="mini-bar" :class="Number(h.net_inflow) >= 0 ? 'pos' : 'neg'"
                   :style="{ height: barH(h.net_inflow) + 'px' }" />
            </el-tooltip>
          </div>
          <div class="table-scroll" style="margin-top:8px">
            <table class="native-mini">
              <tr><th>日期</th><th style="text-align:right">净流入(亿)</th></tr>
              <tr v-for="h in flowHist" :key="h.snap_date">
                <td>{{ h.snap_date }}</td>
                <td style="text-align:right" :class="upDownClass(h.net_inflow)">{{ fmtNum(h.net_inflow) }}</td>
              </tr>
            </table>
          </div>
        </div>
      </div>
    </el-card>

    <el-card class="page-card">
      <template #header>商品周期</template>
      <div class="table-scroll">
      <el-table :data="commodities" size="small" v-loading="cmdLoading" stripe border>
        <el-table-column prop="series_name" label="品种" min-width="110" />
        <el-table-column label="信号" width="90">
          <template #default="{ row }">
            <el-tag :type="row.signal === '启动' ? 'danger' : row.signal === '持续' ? 'warning' : 'info'"
                    size="small">{{ row.signal || '-' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="20日动量%" width="120" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.mom_20)">{{ fmtPct(row.mom_20) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="related_track" label="关联赛道" min-width="120" />
        <el-table-column prop="trade_date" label="日期" width="110" />
      </el-table>
      </div>
      <el-empty v-if="!cmdLoading && !commodities.length" description="暂无商品数据" :image-size="60">
        <template #description>
          <p>暂无商品数据,请先在任务中心运行 commodities 后可见</p>
          <p class="muted" style="font-size:12px">商品周期 16:20 落库;仅作赛道确认参考,不作入场依据</p>
        </template>
      </el-empty>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import api from '../api'
import { fmtNum, fmtPct, upDownClass } from '../utils/fmt'

const indexes = ref([])
const commodities = ref([])
const ixLoading = ref(false)
const cmdLoading = ref(false)

// 市场总览
const stat = ref({})
const hasStat = computed(() => stat.value && Object.keys(stat.value).length > 0
  && (stat.value.advance != null || stat.value.amount_total != null))
const limitText = computed(() => `${stat.value.limit_up ?? '-'} / ${stat.value.limit_down ?? '-'}`)
const amountText = computed(() => stat.value.amount_total != null
  ? fmtNum(Number(stat.value.amount_total) / 1e8) : '-')

// 板块资金
const boardType = ref('industry')
const flows = ref([])
const flowLoading = ref(false)
const flowSel = ref('')
const flowHist = ref([])
const histLoading = ref(false)
let seq = 0
let flowSeq = 0
let histSeq = 0

function barH(v) {
  const xs = flowHist.value.map((h) => Math.abs(Number(h.net_inflow) || 0))
  const mx = Math.max(...xs, 1)
  return 4 + Math.round((Math.abs(Number(v) || 0) / mx) * 56)
}

async function loadFlow() {
  const cur = (flowSeq += 1)
  flowLoading.value = true
  try {
    const data = await api.get('/api/screener/fund-flow',
      { params: { board_type: boardType.value, limit: 20 }, silent: true }).catch(() => [])
    if (cur !== flowSeq) return
    flows.value = Array.isArray(data) ? data : []
  } finally {
    if (cur === flowSeq) flowLoading.value = false
  }
}

async function onFlowRow(row) {
  flowSel.value = row.board_name
  const cur = (histSeq += 1)
  histLoading.value = true
  try {
    const data = await api.get('/api/screener/fund-flow/history',
      { params: { board_name: row.board_name, board_type: boardType.value, days: 30 }, silent: true })
      .catch(() => [])
    if (cur !== histSeq) return
    flowHist.value = Array.isArray(data) ? data : []
  } finally {
    if (cur === histSeq) histLoading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => { seq += 1; flowSeq += 1; histSeq += 1 })

async function load() {
  const cur = (seq += 1)
  ixLoading.value = cmdLoading.value = true
  try {
    const [ix, cmd, ms] = await Promise.all([
      api.get('/api/screener/indexes', { silent: true }).catch(() => []),
      api.get('/api/screener/commodities', { silent: true }).catch(() => []),
      api.get('/api/screener/market-stat/latest', { silent: true }).catch(() => ({})),
    ])
    if (cur !== seq) return // 竞态:丢弃过期响应
    indexes.value = Array.isArray(ix) ? ix : []
    commodities.value = Array.isArray(cmd) ? cmd : []
    stat.value = (ms && typeof ms === 'object' && !Array.isArray(ms)) ? ms : {}
  } finally {
    if (cur === seq) ixLoading.value = cmdLoading.value = false
  }
  await loadFlow()
}
</script>

<style scoped>
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.native-mini { border-collapse: collapse; font-size: 12px; min-width: 280px; }
.native-mini th, .native-mini td { border: 1px solid #ebeef5; padding: 4px 8px; }
.native-mini th { background: #f5f7fa; font-weight: 600; }
.mini-bars { display: flex; align-items: flex-end; gap: 3px; min-height: 64px;
  padding: 8px 4px 2px; border: 1px solid #ebeef5; border-radius: 4px; background: #fafbfc; }
.mini-bar { width: 10px; border-radius: 2px 2px 0 0; cursor: default; }
.mini-bar.pos { background: #f56c6c; }
.mini-bar.neg { background: #67c23a; }
</style>
