<template>
  <el-card class="page-card" style="max-width:min(640px, 100%)">
    <template #header>我的大模型设置(OpenAI 兼容协议;密钥加密存储)</template>
    <el-alert type="info" :closable="false" style="margin-bottom:14px"
              title="未配置时 AI 功能不可用,规则功能不受影响。支持 DeepSeek / 通义 / Kimi / OpenAI / 本地 vLLM 等" />
    <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
      <el-form-item label="Base URL" prop="base_url">
        <el-input v-model="form.base_url" placeholder="https://api.deepseek.com/v1" />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input v-model="form.api_key" type="password" show-password
                  :placeholder="me?.llm?.masked_key || 'sk-...'" />
      </el-form-item>
      <el-form-item label="模型" prop="model">
        <el-input v-model="form.model" placeholder="deepseek-chat" />
      </el-form-item>
      <el-form-item label="温度">
        <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" />
      </el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      <el-alert v-if="localHttpHint" type="warning" :closable="false" style="margin-bottom:12px"
                title="检测到本机 HTTP 地址(vLLM/Ollama 本地调试用):仅本机环回地址允许,远程 HTTP 会被服务端拒绝" />
      <el-form-item>
        <el-button type="warning" :loading="testing" @click="test">测试连通</el-button>
        <el-button v-if="!form.enabled" type="info" :loading="saving" @click="saveDisabled">仅保存停用状态</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </el-form-item>
    </el-form>
    <el-alert v-if="testResult" :type="testResult.ok ? 'success' : 'error'" :title="testResult.message" :closable="false" />
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const me = ref(null)
const formRef = ref()
const form = ref({ base_url: '', api_key: '', model: '', temperature: 0.3, enabled: true })
const testing = ref(false)
const saving = ref(false)
const testResult = ref(null)

// 本机环回:允许 http(本地 vLLM 调试);远程 http 由服务端拒绝,这里只做提示不拦截保存
// 统一解析一次 URL,host 判定只写一份(避免环回/远程两套逻辑漂移)
function parseHttpUrl(url) {
  try {
    const u = new URL(String(url || '').trim())
    if (u.protocol !== 'http:') return null
    return { loopback: ['localhost', '127.0.0.1', '::1'].includes(u.hostname) }
  } catch { return null }
}
const localHttpHint = computed(() => !!parseHttpUrl(form.value.base_url)?.loopback)
function isRemoteHttp(url) {
  const parsed = parseHttpUrl(url)
  return !!parsed && !parsed.loopback
}

const rules = {
  base_url: [
    { required: true, message: '请输入 Base URL', trigger: 'blur' },
    { pattern: /^https?:\/\/.+/, message: '需以 http(s):// 开头', trigger: 'blur' },
    {
      validator: (_r, v, cb) => {
        if (isRemoteHttp(v)) cb(new Error('远程地址必须使用 HTTPS;仅本机环回(localhost/127.0.0.1)允许 HTTP'))
        else cb()
      },
      trigger: 'blur',
    },
  ],
  model: [{ required: true, message: '请输入模型名', trigger: 'blur' }],
}

onMounted(async () => {
  me.value = await api.get('/api/auth/me')
  const cfg = await api.get('/api/settings/llm')
  if (cfg) form.value = { ...form.value, ...cfg, api_key: '' }
})

async function save() {
  await formRef.value.validate()
  saving.value = true
  try {
    await api.put('/api/settings/llm', form.value)
    ElMessage.success('已保存')
  } finally {
    saving.value = false
  }
}

// 停用状态单保存:后端 enabled=false 时不要求 URL/模型必填,留空则保留原配置
async function saveDisabled() {
  saving.value = true
  try {
    await api.put('/api/settings/llm', { ...form.value, enabled: false })
    form.value.enabled = false
    ElMessage.success('已保存为停用状态(启用时才要求 URL/模型)')
  } finally {
    saving.value = false
  }
}

async function test() {
  await formRef.value.validate()
  testing.value = true
  try {
    testResult.value = await api.post('/api/settings/llm/test', form.value)
  } finally {
    testing.value = false
  }
}
</script>
