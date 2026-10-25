<template>
  <el-row :gutter="16">
    <el-col :xs="24" :sm="24" :md="8">
      <el-card class="page-card">
        <template #header>
          <div class="card-head">
            <span>报告列表</span>
            <span class="grow" />
            <el-button type="primary" size="small" :loading="generating" @click="generate">生成本周报告</el-button>
          </div>
        </template>
        <el-menu @select="view" v-loading="loading">
          <el-menu-item v-for="r in list" :key="r.id" :index="String(r.id)">{{ r.title }}</el-menu-item>
        </el-menu>
        <el-empty v-if="!loading && !list.length" description="暂无周报" :image-size="60">
          <template #description>
            <p>暂无周报,点上方「生成本周报告」手动生成</p>
            <p class="muted" style="font-size:12px">周报每周五 17:45 自动生成;周五收盘前为空属正常</p>
          </template>
        </el-empty>
        <el-divider>日报(即时生成)</el-divider>
        <el-button style="width:100%" @click="viewDaily">查看今日持仓日报</el-button>
      </el-card>
    </el-col>
    <el-col :xs="24" :sm="24" :md="16">
      <el-card class="page-card" v-loading="viewing">
        <template #header>{{ currentTitle || '报告内容' }}</template>
        <div v-if="content" class="markdown-body" v-html="rendered"></div>
        <el-empty v-else description="选择左侧报告查看">
          <template #description>
            <p>选择左侧报告查看</p>
            <p class="muted" style="font-size:12px">点左侧周报标题看周报,或点「查看今日持仓日报」即时生成日报(无持仓时先去录入持仓)</p>
          </template>
        </el-empty>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import api from '../api'

const list = ref([])
const content = ref('')
const currentTitle = ref('')
const generating = ref(false)
const loading = ref(false)
const viewing = ref(false)

// 周报内容含用户可控文本,渲染前消毒防存储型 XSS
const rendered = computed(() => DOMPurify.sanitize(marked.parse(content.value || '')))

onMounted(load)

async function load() {
  loading.value = true
  try {
    list.value = await api.get('/api/reports/weekly')
  } finally {
    loading.value = false
  }
}

async function view(id) {
  viewing.value = true
  try {
    const r = await api.get(`/api/reports/weekly/${id}`)
    content.value = r.content
    currentTitle.value = r.title
  } finally {
    viewing.value = false
  }
}

async function viewDaily() {
  viewing.value = true
  try {
    const r = await api.get('/api/reports/daily')
    content.value = r.content
    currentTitle.value = '今日持仓日报'
  } finally {
    viewing.value = false
  }
}

async function generate() {
  generating.value = true
  try {
    const r = await api.post('/api/reports/weekly/generate')
    await load()
    await view(String(r.id))
  } finally {
    generating.value = false
  }
}
</script>

<style scoped>
.markdown-body :deep(h1) { font-size: 18px; margin: 8px 0 12px; }
.markdown-body :deep(h2) { font-size: 15px; margin: 14px 0 8px; }
.markdown-body :deep(p) { margin: 6px 0; line-height: 1.6; }
.markdown-body :deep(table) { border-collapse: collapse; margin: 8px 0; }
.markdown-body :deep(th), .markdown-body :deep(td) {
  border: 1px solid #ebeef5; padding: 4px 10px; font-size: 13px;
}
.markdown-body :deep(blockquote) {
  margin: 10px 0; padding: 6px 12px; background: #fdf6ec;
  border-left: 3px solid #e6a23c; color: #909399; font-size: 12px;
}
</style>
