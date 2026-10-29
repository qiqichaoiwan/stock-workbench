<template>
  <div class="dc-field-grid">
    <label v-for="key in fields(kind)" :key="key" class="dc-field-item">
      <span class="dc-field-label">{{ labelFor(kind, key) }}</span>
      <input v-model.number="model[key]" type="number" step="any" :data-manual-number="`${prefix}.${key}`" placeholder="unknown" />
    </label>
    <label v-for="key in fields(kind,'boolean')" :key="key" class="dc-field-item">
      <span class="dc-field-label">{{ labelFor(kind, key) }}</span>
      <select v-model="model[key]" :data-manual-bool="`${prefix}.${key}`"><option :value="null">unknown / 未核验</option><option :value="true">是 / true</option><option :value="false">否 / false</option></select>
    </label>
  </div>
</template>
<script setup>
import { fields } from '../utils/dualCoreCard.mjs'
defineProps({ model:{type:Object,required:true},kind:{type:String,required:true},prefix:{type:String,required:true} })
// 中文标签与 v2 引擎字段一一对应；同一字段名在不同分组含义不同的，按 kind 覆盖。
const common = {
  price:'现价', open:'开盘价', high:'最高价', low:'最低价', prev_close:'昨收', prev_price:'前一日价格',
  prev_high:'昨日最高', prev_low:'昨日最低', ma5:'日MA5', ma10:'日MA10', ma20:'日MA20', ma5_prev:'前一日MA5', prev_ma5:'前一日MA5',
  wma5:'5周均线', wma10:'10周均线', wma20:'20周均线', wma30:'30周均线',
  w_pct:'周涨跌幅 %', below_ma5_days:'连续低于MA5天数', above_ma5_days:'5日内站上MA5天数',
  high60:'近60日最高', pct_chg:'当日涨跌幅 %', pct_chg_high:'创60日新高日涨幅 %',
  high20w:'近20周最高', previous_high20w:'前20周最高', rank:'当日行业排名',
  amount:'当日成交额', amount_ma20:'20日均成交额', amount_avg:'20日日均成交额（元）', gain5:'5日涨幅 %',
  volume_ratio:'量比', w_close:'周收盘价', macd_cross_weeks:'MACD金叉距今周数', dif:'MACD DIF',
  float_cap:'流通市值（元）', limit_down5:'近5日跌停次数', return20:'近20日涨跌幅 %',
  boards:'连板数', box_range20:'20日箱体振幅 %', prior_high20:'前20日箱体高点',
  n_bear_age:'N字反包距今天数', n_bear_high:'N字前高',
  weight:'仓位 %', pnl:'浮盈亏 %',
  daily_pnl:'当日盈亏 %', weekly_pnl:'本周累计盈亏 %', consecutive_losses:'连续亏损笔数',
  total_weight:'账户总仓位 %', frozen_sessions:'冻结交易日数',
  wma20_up4:'20周均线连续4周向上', macd_dead_cross:'周MACD死叉',
  black_swan:'黑天鹅 / 重大利空', is_core:'中军核心', leader1:'龙一', full_resonance:'三周期全共振',
  high_upper_shadow:'长上影', high_volume_bear:'高位放量阴线', stagnation:'放量滞涨',
  one_price_limit_up:'一字涨停', ma5_bad:'跌破MA5（仅试错）',
  bought_today:'今日买入', limit_down_locked:'停牌/跌停无法卖出', limit_up_open:'涨停开盘（禁止加仓）',
  stopped_today:'今日停止交易', data_failure_today:'今日数据故障',
  review_complete:'两周复盘已完成', two_month_low_winrate:'近两月胜率<35%',
}
const overrides = { core:{ high:'当日最高' }, industry:{ price:'行业指数点位' } }
function labelFor(kind, key) { return overrides[kind]?.[key] || common[key] || key }
</script>
<style scoped>
.dc-field-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px 12px}
.dc-field-item{display:grid;gap:4px;min-width:0;font-size:12px;color:var(--el-text-color-secondary,#909399)}
.dc-field-label{overflow-wrap:anywhere;line-height:1.4}
.dc-field-item input,.dc-field-item select{width:100%;height:30px;padding:0 8px;border:1px solid var(--el-border-color,#dcdfe6);border-radius:4px;background:var(--el-bg-color,#fff);color:var(--el-text-color-regular,#606266);font:inherit;font-size:13px;box-sizing:border-box}
.dc-field-item input:focus,.dc-field-item select:focus{outline:none;border-color:var(--el-color-primary,#409eff)}
@media (max-width: 760px) {
  .dc-field-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 10px}
}
@media (max-width: 360px) {
  .dc-field-grid{grid-template-columns:1fr}
}
</style>
