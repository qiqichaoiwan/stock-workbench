<template>
  <div>
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>策略参数(全部阈值后台可调)</span>
          <span class="grow" />
          <el-button v-if="canOperate" type="primary" size="small" :loading="saving" @click="save">保存修改</el-button>
        </div>
      </template>
      <el-table :data="params" size="small" stripe>
        <el-table-column prop="key" label="参数" width="200" />
        <el-table-column label="值" width="200">
          <template #default="{ row }">
            <el-input v-model="row.value" size="small" :disabled="!canOperate" @blur="validateRow(row)" />
          </template>
        </el-table-column>
        <el-table-column prop="note" label="说明" />
      </el-table>
      <el-alert v-if="!canOperate" type="info" :closable="false" title="当前角色只读;修改需策略员或管理员权限" style="margin-top:10px" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../store'

const store = useUserStore()
const canOperate = computed(() => store.isAnalyst)
const params = ref([])
const saving = ref(false)

onMounted(async () => {
  params.value = await api.get('/api/screener/strategy-params')
})

// 数值型参数就地校验(非法输入标红提示,保存前拦截)
function validateRow(row) {
  const v = String(row.value).trim()
  if (v === '') return true
  const ok = /^-?\d+(\.\d+)?$/.test(v)
  if (!ok) {
    ElMessage.warning(`参数 ${row.key} 需为数值`)
    return false
  }
  row.value = v
  return true
}

async function save() {
  if (!params.value.every(validateRow)) return
  saving.value = true
  try {
    const payload = Object.fromEntries(params.value.map((p) => [p.key, p.value]))
    await api.put('/api/screener/strategy-params', { params: payload })
    ElMessage.success('参数已保存,下一轮筛选生效')
  } finally {
    saving.value = false
  }
}
</script>
