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
      <el-table v-if="radar.length" :data="radar" size="small" v-loading="radarLoading" stripe>
        <el-table-column prop="name" label="行业/赛道" min-width="130" fixed="left" />
        <el-table-column label="阶段" width="150">
          <template #default="{ row }">
            <el-tag :type="stageType(row.stage)" size="small">{{ row.stage_text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="20日动量%" width="110" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.mom_20)">{{ fmtPct(row.mom_20) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="60日动量%" width="110" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.mom_60)">{{ fmtPct(row.mom_60) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="动量加速" width="100" align="right">
          <template #default="{ row }">
            <span :class="upDownClass(row.accel)" style="font-weight:600">{{ fmtPct(row.accel) }}</span>
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
        <el-table-column v-if="canOperate" label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button v-if="!row.tracked" size="small" link type="primary" @click="adopt(row)">加入赛道跟踪</el-button>
            <el-tag v-else size="small" type="success">已跟踪</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无雷达数据">
        <template #description>
          <p>暂无雷达数据</p>
          <p class="muted" style="font-size:12px">先运行行业监测任务(industry_daily)生成行业截面后可见</p>
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
        <el-table :data="rows" size="small" v-loading="loading" max-height="480" stripe>
          <el-table-column prop="industry_name" label="行业" min-width="120" fixed="left" />
          <el-table-column label="景气评分" sortable prop="heat_score" width="110" align="right">
            <template #default="{ row }">{{ fmtNum(row.heat_score, 1) }}</template>
          </el-table-column>
          <el-table-column label="20日动量%" sortable prop="mom_20" width="115" align="right">
            <template #default="{ row }">
              <span :class="upDownClass(row.mom_20)">{{ fmtPct(row.mom_20) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="60日动量%" sortable prop="mom_60" width="115" align="right">
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
              <span v-if="row.fund_flow" :class="upDownClass(row.fund_flow)">{{ fmtNum(row.fund_flow) }}</span>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="连续流入(天)" sortable prop="flow_streak" width="115" align="right">
            <template #default="{ row }">{{ fmtInt(row.flow_streak) }}</template>
          </el-table-column>
          <el-table-column label="当日涨跌%" width="105" align="right">
            <template #default="{ row }">
              <span :class="upDownClass(row.pct_chg)">{{ fmtPct(row.pct_chg) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" min-width="120">
            <template #default="{ row }">
              <el-tag v-if="row.avoid_until" type="danger" size="small">回避至{{ row.avoid_until }}</el-tag>
              <el-tag v-else type="success" size="small">正常</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <el-empty v-else description="暂无行业景气数据">
        <template #description>
          <p>暂无行业景气数据</p>
          <p class="muted" style="font-size:12px;line-height:1.6">
            行业数据由每日 16:00 定时任务(industry_daily)同步;<br />
            本地网络若无法访问东方财富接口会暂时为空,云端部署后自动恢复。
          </p>
        </template>
        <el-button v-if="canOperate" type="primary" size="small" :loading="syncingIndustry"
                   @click="syncIndustry">立即同步</el-button>
      </el-empty>
    </el-card>

    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>赛道管理</span>
          <span class="grow" />
          <el-button v-if="canOperate" size="small" @click="showAdd = true">新增赛道</el-button>
        </div>
      </template>
      <el-table :data="tracks" size="small" stripe>
        <el-table-column prop="name" label="赛道" width="120" />
        <el-table-column prop="concept_board" label="东财概念板块" width="150" />
        <el-table-column label="新闻关键词" show-overflow-tooltip>
          <template #default="{ row }">{{ row.keywords || '-' }}</template>
        </el-table-column>
        <el-table-column prop="member_count" label="成分数" width="90" align="right">
          <template #default="{ row }">{{ fmtInt(row.member_count) }}</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="canOperate" label="操作" width="160">
          <template #default="{ row }">
            <el-button size="small" link @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
            <el-button size="small" link type="primary" :loading="row._syncing" @click="sync(row)">同步成分</el-button>
          </template>
        </el-table-column>
      </el-table>
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
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { fmtInt, fmtNum, fmtPct, upDownClass } from '../utils/fmt'
import { useUserStore } from '../store'

const store = useUserStore()
const canOperate = computed(() => store.isAnalyst)

const rows = ref([])
const tracks = ref([])
const radar = ref([])
const radarLoading = ref(false)
const loading = ref(false)
const showAdd = ref(false)
const syncingIndustry = ref(false)
const newTrack = ref({ name: '', concept_board: '', keywords: '' })

onMounted(() => { load(); loadRadar() })

async function load() {
  loading.value = true
  try {
    rows.value = await api.get('/api/screener/industries')
    tracks.value = await api.get('/api/screener/tracks')
  } finally {
    loading.value = false
  }
}

async function loadRadar() {
  radarLoading.value = true
  try {
    radar.value = await api.get('/api/screener/emerging', { params: { top: 12 } })
  } finally {
    radarLoading.value = false
  }
}

function stageType(s) {
  return { starting: 'danger', rising: 'warning', watch: 'info', avoid: 'success' }[s] || 'info'
}

// 把雷达发现的行业一键纳入赛道管理(成分股在东财可用环境同步)
async function adopt(row) {
  await api.post('/api/screener/tracks', {
    name: row.name, keywords: row.name, enabled: true,
    note: `新兴赛道雷达采纳(${row.stage_text})`,
  })
  ElMessage.success(`已把「${row.name}」加入赛道管理,可点击同步成分`)
  await loadRadar()
}

async function syncIndustry() {
  syncingIndustry.value = true
  try {
    const r = await api.post('/api/screener/tasks/industry_daily/run')
    ElMessage.success(r.message || '已提交后台执行')
    // 后台执行完毕前先轮询两次结果
    setTimeout(async () => {
      await load()
      await loadRadar()
    }, 8000)
    setTimeout(async () => {
      await load()
      await loadRadar()
    }, 25000)
  } finally {
    syncingIndustry.value = false
  }
}

async function toggle(row) {
  row.enabled = !row.enabled
  await api.put(`/api/screener/tracks/${row.id}`, { name: row.name, concept_board: row.concept_board,
    industry_index: row.industry_index, keywords: row.keywords, enabled: row.enabled, note: row.note || '' })
}

async function sync(row) {
  row._syncing = true
  try {
    const r = await api.post(`/api/screener/tracks/${row.id}/sync`)
    ElMessage.success(`新增成分 ${r.added} 只`)
    await load()
  } finally {
    row._syncing = false
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
