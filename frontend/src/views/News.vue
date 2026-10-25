<template>
  <el-card class="page-card">
    <template #header>
      <div class="card-head">
        <span>新闻甄别流(低频采集 · 规则过滤日内噪声 · 服务赛道确认,不作入场依据)</span>
        <span class="grow" />
        <el-switch v-model="screenedOnly" active-text="仅甄别通过" @change="load" />
      </div>
    </template>
    <el-table :data="pagedRows" size="small" v-loading="loading" max-height="620" stripe border>
      <el-table-column label="时间" width="150">
        <template #default="{ row }">{{ fmtDT(row.publish_time) }}</template>
      </el-table-column>
      <el-table-column label="标题" min-width="320" show-overflow-tooltip>
        <template #default="{ row }">{{ row.title }}</template>
      </el-table-column>
      <el-table-column prop="category" label="主题" width="110" />
      <el-table-column label="关联赛道" width="110">
        <template #default="{ row }">{{ row.related_industry || '-' }}</template>
      </el-table-column>
      <el-table-column label="情绪" width="90">
        <template #default="{ row }">
          <el-tag :type="row.sentiment === '利好' ? 'danger' : row.sentiment === '利空' ? 'success' : 'info'" size="small">
            {{ row.sentiment }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="热度" width="90" sortable prop="heat">
        <template #default="{ row }">{{ fmtInt(row.heat) }}</template>
      </el-table-column>
      <el-table-column label="甄别" width="90">
        <template #default="{ row }">
          <el-tag :type="row.screened ? 'success' : 'info'" size="small">{{ row.screened ? '通过' : '噪声' }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !rows.length" description="暂无新闻" :image-size="60">
      <template #description>
        <p>暂无新闻,请先在任务中心运行 news 后可见</p>
        <p class="muted" style="font-size:12px">交易日 11:40 / 14:40 低频采集;若开启「仅甄别通过」仍为空,可关掉开关确认是否连噪声也没有</p>
      </template>
    </el-empty>
    <div class="pager">
      <el-pagination layout="total, prev, pager, next" :total="rows.length" :page-size="pageSize"
                     v-model:current-page="page" />
    </div>
    <p class="muted" style="font-size:12px;margin:6px 0 0">前端分页 · 共 {{ rows.length }} 条(服务端 limit=200 取回,切换开关即重新请求)</p>
  </el-card>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import api from '../api'
import { fmtDT, fmtInt } from '../utils/fmt'

const rows = ref([])
const screenedOnly = ref(true)
const loading = ref(false)
const page = ref(1)
const pageSize = 20
let loadSeq = 0
const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))

async function load() {
  const seq = (loadSeq += 1)
  loading.value = true
  try {
    const data = await api.get('/api/screener/news',
      { params: { screened_only: screenedOnly.value, limit: 200 }, silent: true })
    if (seq !== loadSeq) return // 竞态:丢弃过期响应
    rows.value = Array.isArray(data) ? data : (data?.items || data?.data || [])
    page.value = 1
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => { loadSeq += 1 })
</script>
