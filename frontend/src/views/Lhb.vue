<template>
  <div>
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>龙虎榜</span>
          <span class="muted" style="font-size:12px">↑买入(实心) · ↓卖出(描边) · 红/黄/灰=高/中/低风险 · 仅显示游资(机构/北向已过滤)</span>
          <span class="grow" />
          <el-button size="small" @click="load">刷新</el-button>
        </div>
      </template>
      <div class="table-scroll">
      <el-table :data="lhbRows" size="small" v-loading="lhbLoading" stripe border>
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
        <el-table-column label="命中游资" min-width="230">
          <template #default="{ row }">
            <template v-if="row.traders.length">
              <el-tooltip v-for="t in row.traders" :key="t.trader + t.side"
                          :content="traderTip(t)" :disabled="!traderTip(t)" placement="top">
                <el-tag size="small" :type="riskType(t.risk)"
                        :effect="t.side === '买入' ? 'dark' : 'light'" style="margin:2px">
                  {{ sideArrow(t.side) }}{{ t.trader }}
                </el-tag>
              </el-tooltip>
            </template>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="trade_date" label="交易日" width="110" />
      </el-table>
      </div>
      <el-empty v-if="!lhbLoading && !lhb.length" description="暂无龙虎榜数据" :image-size="60">
        <template #description>
          <p>暂无龙虎榜数据,收盘后 17:10「{{ taskName('lhb_sync') }}」落库后可见</p>
          <p class="muted" style="font-size:12px">周末/非交易日无数据属预期;交易日晚间仍空请去任务中心手动运行「{{ taskName('lhb_sync') }}」</p>
        </template>
      </el-empty>
    </el-card>

    <el-card class="page-card">
      <template #header>游资席位</template>
      <div class="table-scroll">
      <el-table :data="seatRows" size="small" v-loading="seatLoading" stripe border>
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
      <el-empty v-if="!seatLoading && !seatRows.length" description="暂无游资席位数据" :image-size="60" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import api from '../api'
import { fmtNum, fmtPct, upDownClass } from '../utils/fmt'
import { taskName } from '../utils/tasks'

const lhb = ref([])
const seats = ref([])
const lhbLoading = ref(false)
const seatLoading = ref(false)
let seq = 0

// 兼容后端历史格式:对象数组 / JSON 字符串 / 字符串数组;同一游资同一方向去重
const lhbRows = computed(() => lhb.value.map((row) => ({
  ...row,
  traders: normalizeTraders(row.matched_traders),
})))

// 席位表只展示游资;接口已过滤,这里对旧后端/旧缓存再兜底
const seatRows = computed(() => seats.value.filter((s) => itemKind(s) === '游资'))

// 类别:后端 kind 优先;缺失时按游资/机构/北向名称推断(兼容旧接口)
function itemKind(item) {
  const kind = String(item.kind || '').trim()
  if (kind) return kind
  const trader = String(item.trader || item.seat || '')
  const seat = String(item.seat || '')
  if (trader.includes('北向') || seat.includes('股通')) return '北向'
  if (trader.includes('机构') || seat === '机构专用') return '机构'
  return '游资'
}

function normalizeTraders(raw) {
  let list = []
  if (Array.isArray(raw)) {
    list = raw
  } else if (typeof raw === 'string' && raw.trim()) {
    try {
      const parsed = JSON.parse(raw)
      list = Array.isArray(parsed) ? parsed : []
    } catch {
      list = raw.split(/[,;，；|]/).map((s) => s.trim()).filter(Boolean)
    }
  }
  const seen = new Set()
  const out = []
  for (const item of list) {
    if (typeof item === 'string') {
      const name = item.trim()
      if (name && itemKind({ trader: name, seat: name }) === '游资' && !seen.has(name)) {
        seen.add(name)
        out.push({ trader: name, seat: name, side: '', style: '', risk: '' })
      }
      continue
    }
    if (!item || typeof item !== 'object') continue
    const trader = String(item.trader || item.seat || '').trim()
    const side = String(item.side || '').trim()
    const key = `${trader}|${side}`
    if (!trader || itemKind(item) !== '游资' || seen.has(key)) continue
    seen.add(key)
    out.push({
      trader,
      seat: String(item.seat || '').trim(),
      side,
      style: String(item.style || '').trim(),
      risk: String(item.risk || '').trim(),
    })
  }
  return out
}

function traderTip(t) {
  return [
    t.side,
    t.seat && t.seat !== t.trader ? t.seat : '',
    t.style,
    t.risk ? `${t.risk}风险` : '',
  ].filter(Boolean).join(' · ')
}

function sideArrow(side) {
  if (side === '买入') return '↑'
  if (side === '卖出') return '↓'
  return ''
}

function riskType(risk) {
  if (risk.includes('高')) return 'danger'
  if (risk.includes('中')) return 'warning'
  return 'info'
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
