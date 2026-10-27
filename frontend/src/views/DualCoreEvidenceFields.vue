<template>
  <div class="dc-form-grid">
    <label v-for="key in fields(kind)" :key="key">{{ key }}<input v-model.number="model[key]" type="number" step="any" :data-manual-number="`${prefix}.${key}`" placeholder="unknown" /></label>
    <label v-for="key in fields(kind,'boolean')" :key="key">{{ labels[key] || key }}<select v-model="model[key]" :data-manual-bool="`${prefix}.${key}`"><option :value="null">unknown / 未核验</option><option :value="true">是 / true</option><option :value="false">否 / false</option></select></label>
  </div>
</template>
<script setup>
import { fields } from '../utils/dualCoreCard.mjs'
defineProps({ model:{type:Object,required:true},kind:{type:String,required:true},prefix:{type:String,required:true} })
const labels = {bought_today:'今日买入',limit_down_locked:'停牌/跌停无法卖出'}
</script>
