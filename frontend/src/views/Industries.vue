<template>
  <div>
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>新兴赛道雷达(启动期优先 · 识别而非预测)</span>
          <span class="muted" style="font-size:12px">低位转强 &gt; 强动量 &gt; 观察;SC-U08 拥挤行业自动垫底回避;新闻近3日突增加权</span>
          <span class="grow" />
          <el-button size="small" link :loading="radarLoading" @click="loadRadar">刷新雷达</el-button>
        </div>
      </template>
      <div class="table-scroll">
      <el-table v-if="radar.length" :data="radar" size="small" v-loading="radarLoading" stripe border>
        <el-table-column prop="name" label="行业/赛道" min-width="130" :fixed="isMobile ? false : 'left'" show-overflow-tooltip />
        <el-table-column label="阶段" width="150">
          <template #default="{ row }">
            <el-tag :type="stageType(row.stage)" size="small">{{ row.stage_text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="20日动量%" width="110" align="right">
          <template #default="{ row }">
            <span v-if="row._firstDay" class="muted">数据积累中</span>
            <span v-else :class="upDownClass(row.mom_20)">{{ fmtPct(row.mom_20) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="60日动量%" width="110" align="right">
          <template #default="{ row }">
            <span v-if="row._firstDay" class="muted">数据积累中</span>
            <span v-else :class="upDownClass(row.mom_60)">{{ fmtPct(row.mom_60) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="动量加速" width="100" align="right">
          <template #default="{ row }">
            <span v-if="row._firstDay" class="muted">数据积累中</span>
            <span v-else :class="upDownClass(row.accel)" style="font-weight:600">{{ fmtPct(row.accel) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="拥挤度" width="100" align="right">
          <template #default="{ row }">
            <span :class="row.ratio_score > 300 ? 'up' : ''">{{ fmtNum(row.ratio_score, 1) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="新闻3日/突增" width="115" align="right">
          <template #default="{ row }">
            {{ row.news_3d }} 条 / <span :class="row.news_surge >= 2 ? 'up' : ''">×{{ fmtNum(row.news_surge, 1) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="雷达分" width="90" sortable prop="score" align="right">
          <template #default="{ row }">{{ fmtNum(row.score, 2) }}</template>
        </el-table-column>
        <el-table-column v-if="canOperate" label="操作" width="130" :fixed="isMobile ? false : 'right'">
          <template #default="{ row }">
            <el-button v-if="!row.tracked" size="small" link type="primary" @click="adopt(row)">加入赛道跟踪</el-button>
            <el-tag v-else size="small" type="success">已跟踪</el-tag>
          </template>
        </el-table-column>
      </el-table>
      </div>
      <el-empty v-if="!radarLoading && !radar.length" description="暂无雷达数据">
        <template #description>
          <p>暂无雷达数据</p>
          <p class="muted" style="font-size:12px">先运行「{{ taskName('industry_daily') }}」任务生成行业截面后可见</p>
        </template>
      </el-empty>
    </el-card>

    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>行业景气热力图(点击列头排序;回避名单标红)</span>
          <span class="grow" />
          <el-button v-if="canOperate" size="small" type="primary" link :loading="syncingIndustry"
                     @click="syncIndustry">立即同步行业数据</el-button>
        </div>
      </template>
      <template v-if="rows.length">
        <div class="table-scroll">
        <el-table :data="rows" size="small" v-loading="loading" max-height="480" stripe border>
          <el-table-column prop="industry_name" label="行业" min-width="120" :fixed="isMobile ? false : 'left'" show-overflow-tooltip />
          <el-table-column label="景气评分" sortable prop="heat_score" width="110" align="right">
            <template #default="{ row }">{{ fmtNum(row.heat_score, 1) }}</template>
          </el-table-column>
          <el-table-column label="20日动量%" sortable prop="mom_20" width="115" align="right">
            <template #default="{ row }">
              <span :class="upDownClass(row.mom_20)">{{ fmtPct(row.mom_20) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="60日动量%" sortable prop="mom_60" width="115" align="right" class-name="hide-mobile">
            <template #default="{ row }">
              <span :class="upDownClass(row.mom_60)">{{ fmtPct(row.mom_60) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="拥挤度偏离" sortable prop="ratio_score" width="115" align="right">
            <template #default="{ row }">
              <span :class="row.ratio_score > 300 ? 'up' : ''" style="font-weight:600">{{ fmtNum(row.ratio_score, 1) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="主力净流入(亿)" sortable prop="fund_flow" width="135" align="right">
            <template #default="{ row }">
              <!-- 0 为真实零值着色显示;null/'' 为缺失显示 '-',与 0 严格区分 -->
              <span v-if="row.fund_flow === null || row.fund_flow === undefined || row.fund_flow === ''" class="muted">-</span>
              <span v-else :class="upDownClass(row.fund_flow)">{{ fmtNum(row.fund_flow) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="连续流入(天)" sortable prop="flow_streak" width="115" align="right" class-name="hide-mobile">
            <template #default="{ row }">{{ fmtInt(row.flow_streak) }}</template>
          </el-table-column>
          <el-table-column label="当日涨跌%" width="105" align="right">
            <template #default="{ row }">
              <span :class="upDownClass(row.pct_chg)">{{ fmtPct(row.pct_chg) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="动量口径" width="110" class-name="hide-mobile">
            <template #default="{ row }">
              <el-tooltip :content="row._firstDay ? '截面首日暂无历史动量,用当日涨跌幅作相对排序快照,次日恢复正常' : '基于库内历史滚动计算'" placement="top">
                <el-tag :type="row._firstDay ? 'warning' : 'success'" size="small">{{ row._firstDay ? '首日快照' : '历史滚动' }}</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="状态" min-width="120">
            <template #default="{ row }">
              <el-tag v-if="row.avoid_until" type="danger" size="small">回避至{{ row.avoid_until }}</el-tag>
              <el-tag v-else type="success" size="small">正常</el-tag>
            </template>
          </el-table-column>
        </el-table>
        </div>
      </template>
      <el-empty v-if="!loading && !rows.length" description="暂无行业景气数据">
        <template #description>
          <p>暂无行业景气数据</p>
          <p class="muted" style="font-size:12px;line-height:1.6">
            行业数据来自每日 16:00「{{ taskName('industry_daily') }}」任务;<br />
            若本地网络无法访问东方财富接口,当前环境暂无数据属预期内的功能降级,不影响已有持仓与选股池展示。
          </p>
        </template>
        <el-button v-if="canOperate" type="primary" size="small" :loading="syncingIndustry"
                   @click="syncIndustry">立即同步</el-button>
      </el-empty>
    </el-card>

    <el-card class="page-card" id="track-mgmt">
      <template #header>
        <div class="card-head">
          <span>赛道管理</span>
          <span class="grow" />
          <el-button v-if="canOperate" size="small" @click="backfill" :loading="backfilling">一键回填</el-button>
          <el-button v-if="canOperate" size="small" @click="showAdd = true">新增赛道</el-button>
        </div>
      </template>
      <div class="table-scroll">
      <el-table :data="tracks" size="small" stripe border>
        <el-table-column prop="name" label="赛道" min-width="110" show-overflow-tooltip />
        <el-table-column prop="concept_board" label="东财概念板块" min-width="130" show-overflow-tooltip />
        <el-table-column label="新闻关键词" show-overflow-tooltip>
          <template #default="{ row }">{{ row.keywords || '-' }}</template>
        </el-table-column>
        <el-table-column prop="member_count" label="成分" width="110" align="right">
          <template #default="{ row }">
            <template v-if="Number(row.member_count) > 0">{{ fmtInt(row.member_count) }}</template>
            <el-tooltip v-else-if="row.sync_error" :content="row.sync_error" placement="top">
              <span style="color:#f56c6c">同步失败</span>
            </el-tooltip>
            <span v-else class="muted">未同步</span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="90">
          <template #default="{ row }">{{ sourceLabel(row.member_source) }}</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="canOperate" label="操作" min-width="130">
          <template #default="{ row }">
            <el-button size="small" link @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
            <el-button size="small" link type="primary" :loading="row._syncing" @click="sync(row)">同步成分</el-button>
          </template>
        </el-table-column>
      </el-table>
      </div>
    </el-card>

    <el-dialog v-model="showAdd" title="新增赛道(如:电机/AI硬件)" width="480">
      <el-form label-width="110px">
        <el-form-item label="名称"><el-input v-model="newTrack.name" placeholder="必填" /></el-form-item>
        <el-form-item label="东财概念板块"><el-input v-model="newTrack.concept_board" placeholder="与东财板块名一致" /></el-form-item>
        <el-form-item label="新闻关键词"><el-input v-model="newTrack.keywords" placeholder="逗号分隔" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAdd = false">取消</el-button>
        <el-button type="primary" @click="add">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { fmtInt, fmtNum, fmtPct, upDownClass } from '../utils/fmt'
import { taskName } from '../utils/tasks'
import { useUserStore } from '../store'

const store = useUserStore()
const canOperate = computed(() => store.isAnalyst)
const isMobile = ref(window.matchMedia('(max-width: 768px)').matches)

const rows = ref([])
const tracks = ref([])
const radar = ref([])
const radarLoading = ref(false)
const loading = ref(false)
const showAdd = ref(false)
const syncingIndustry = ref(false)
const backfilling = ref(false)
const newTrack = ref({ name: '', concept_board: '', keywords: '' })
let loadSeq = 0
let radarSeq = 0
let syncTimers = []

onMounted(() => {
  const mq = window.matchMedia('(max-width: 768px)')
  const onMq = (e) => { isMobile.value = e.matches }
  mq.addEventListener?.('change', onMq)
  load()
  loadRadar()
})
onBeforeUnmount(() => {
  loadSeq += 1
  radarSeq += 1
  syncTimers.forEach(clearTimeout)
  syncTimers = []
})

async function load() {
  const seq = (loadSeq += 1)
  loading.value = true
  try {
    const [ind, trk] = await Promise.all([
      api.get('/api/screener/industries', { silent: true }),
      api.get('/api/screener/tracks', { silent: true }),
    ])
    if (seq !== loadSeq) return // 竞态:丢弃过期响应
    rows.value = Array.isArray(ind) ? ind : []
    tracks.value = Array.isArray(trk) ? trk : []
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function loadRadar() {
  const seq = (radarSeq += 1)
  radarLoading.value = true
  try {
    const data = await api.get('/api/screener/emerging', { params: { top: 12 }, silent: true })
    if (seq !== radarSeq) return
    // 后端首日无历史动量时用当日涨跌幅作截面 proxy(见 stage_text"首日快照"):
    // 前端不展示 proxy 数值,标"数据积累中",次日恢复正常
    radar.value = (Array.isArray(data) ? data : []).map((r) => ({
      ...r, _firstDay: String(r.stage_text || '').includes('首日快照'),
    }))
  } finally {
    if (seq === radarSeq) radarLoading.value = false
  }
}

function stageType(s) {
  return { starting: 'danger', rising: 'warning', watch: 'info', avoid: 'success' }[s] || 'info'
}

// member_source 中文:concept_board 东财 / concept_board_sina 新浪 / keyword 关键词 /
// concept_board_ths 同花顺核验 / concept_board_local 本地 / eastmoney 东财 /
// sina 新浪 / ths 同花顺核验 / local 本地 / local_ths_verified 同花顺核验(- 为空)
function sourceLabel(s) {
  return { concept_board: '东财', concept_board_sina: '新浪', keyword: '关键词',
    concept_board_ths: '同花顺核验', concept_board_local: '本地',
    eastmoney: '东财', sina: '新浪', ths: '同花顺核验', local: '本地',
    local_ths_verified: '同花顺核验' }[s] || '-'
}

// 把雷达发现的行业一键纳入赛道管理(成分股在东财可用环境同步)
// 若雷达行无 concept_board 映射:提示补填后再同步成分
async function adopt(row) {
  if (!row.concept_board) {
    ElMessage.warning(`「${row.name}」暂无东财概念板块映射,已创建赛道记录;请在赛道管理中补填 concept_board 后再同步成分`)
  }
  await api.post('/api/screener/tracks', {
    name: row.name, concept_board: row.concept_board || '', keywords: row.name, enabled: true,
    note: `新兴赛道雷达采纳(${row.stage_text})`,
  })
  ElMessage.success(`已把「${row.name}」加入赛道管理${row.concept_board ? ',可点击同步成分' : ''}`)
  await loadRadar()
}

async function syncIndustry() {
  syncingIndustry.value = true
  try {
    const r = await api.post('/api/screener/tasks/industry_daily/run')
    ElMessage.success(r.message || '已提交后台执行')
    // 后台执行完毕前再拉取两次结果;定时器可取消,卸载时清理
    syncTimers.forEach(clearTimeout)
    syncTimers = [
      setTimeout(async () => { await load(); await loadRadar() }, 8000),
      setTimeout(async () => { await load(); await loadRadar() }, 25000),
    ]
  } finally {
    syncingIndustry.value = false
  }
}

async function toggle(row) {
  const prev = row.enabled
  row.enabled = !prev
  try {
    await api.put(`/api/screener/tracks/${row.id}`, { name: row.name, concept_board: row.concept_board,
      industry_index: row.industry_index, keywords: row.keywords, enabled: row.enabled, note: row.note || '' })
  } catch {
    row.enabled = prev // 失败回滚开关,避免 UI 与服务端不一致(API 层已全局提示错误)
  }
}

async function sync(row) {
  row._syncing = true
  try {
    const r = await api.post(`/api/screener/tracks/${row.id}/sync`)
    const added = r?.added ?? 0
    const src = sourceLabel(r?.source)
    ElMessage.success(`新增成分 ${added} 只（来源${src}）`)
    await load()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '同步失败'
    ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  } finally {
    row._syncing = false
  }
}

async function backfill() {
  backfilling.value = true
  try {
    // 后端已改后台线程执行(7 赛道×多源×重试最坏分钟级):接口立即返回 queued,
    // 结果看任务日志(tracks_backfill)与赛道列表,这里延迟刷新两轮
    const r = await api.post('/api/screener/tracks/backfill')
    if (r?.queued) {
      ElMessage.success(r.message || '已提交后台执行')
      syncTimers.forEach(clearTimeout)
      syncTimers = [
        setTimeout(() => { load() }, 15000),
        setTimeout(() => { load() }, 45000),
      ]
    } else {
      // 兼容旧同步返回结构
      const results = r?.results || {}
      const names = Object.keys(results)
      const total = names.reduce((s, k) => s + Number(results[k]?.added || 0), 0)
      const errs = r?.errors || []
      if (errs.length) ElMessage.warning(`回填完成：${names.length} 赛道新增 ${total} 只，部分失败：${errs.slice(0, 2).join('；')}`)
      else ElMessage.success(`回填完成：${names.length} 赛道新增 ${total} 只`)
      await load()
    }
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '回填失败'
    ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  } finally {
    backfilling.value = false
  }
}

async function add() {
  if (!newTrack.value.name.trim()) return ElMessage.warning('请填写赛道名称')
  await api.post('/api/screener/tracks', newTrack.value)
  showAdd.value = false
  ElMessage.success('已新增,点击「同步成分」拉取成分股')
  await load()
}
</script>

<style scoped>
/* 375px 下表格(约1035px)不溢出:容器横滚 + 优先列保留,其余列省略 tooltip */
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
@media (max-width: 768px) {
  .hide-mobile { display: none !important; }
}
</style>
