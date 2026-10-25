<template>
  <el-card class="page-card">
    <template #header>
      <div class="card-head">
        <span>策略生命周期卡(unverified → testing → verified / deprecated)</span>
        <span class="muted" style="font-size:12px;margin-left:8px">证据链必填 · 风控与 alpha 分离 · 回测需随机对照组</span>
      </div>
    </template>
    <el-collapse v-model="openCards">
      <el-collapse-item v-for="c in cards" :key="c.id" :name="c.card_code">
        <template #title>
          <el-tag :type="statusType(c.status)" size="small" style="margin-right:8px">{{ c.status }}</el-tag>
          <b>{{ c.card_code }}</b>&nbsp;{{ c.title }}
        </template>
        <p><b>假设:</b>{{ c.hypothesis }}</p>
        <p><b>证据链:</b>{{ c.source_note }}</p>
        <pre class="tag-pre">{{ JSON.stringify(c.evidence, null, 2) }}</pre>
        <p class="muted">{{ c.review_note }}</p>
        <template v-if="canOperate">
          <el-select :model-value="c.status" size="small" style="width:140px" @change="(s) => changeStatus(c, s)">
            <el-option v-for="s in ['unverified', 'testing', 'verified', 'deprecated', 'reference']" :key="s" :label="s" :value="s" />
          </el-select>
        </template>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../store'

const store = useUserStore()
const canOperate = computed(() => store.isAnalyst)
const cards = ref([])
const openCards = ref([])

function statusType(s) {
  return { verified: 'success', testing: 'warning', unverified: 'info', deprecated: 'danger', reference: '' }[s] || 'info'
}

onMounted(async () => {
  cards.value = await api.get('/api/screener/strategy-cards')
  openCards.value = cards.value.map((c) => c.card_code)
})

async function changeStatus(card, status) {
  await api.put(`/api/screener/strategy-cards/${card.id}`, { status })
  card.status = status
  ElMessage.success('状态已更新')
}
</script>
