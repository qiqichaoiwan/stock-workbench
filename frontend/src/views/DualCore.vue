<template>
  <main class="dual-core-v2">
    <header class="dc-hero">
      <div><small>双核共振 / v2.0 · unverified</small><h1>14:45 终判执行卡</h1><p>只生成建议与记录，绝不自动下单。14:30 仅预警，收盘历史不是实时证据。</p></div>
      <div class="dc-tools no-print"><label>交易日期<input v-model="tradeDate" type="date" :disabled="busy" @change="switchCard" /></label><label>卡片阶段<select v-model="phase" :disabled="busy" @change="switchCard"><option v-for="p in phases" :key="p[0]" :value="p[0]">{{ p[1] }}</option></select></label><button :disabled="busy" @click="refresh">刷新自动数据</button><button :disabled="busy" @click="evaluateManual">校验手工证据（非实时）</button><button :disabled="busy" @click="save">保存执行卡</button><button @click="printCard">打印 A4</button><button @click="exportDraft">导出当前草稿</button><button :disabled="!card.revision" @click="loadAudit">修订历史</button><button :disabled="!card.revision" @click="exportCard">导出留档</button></div>
    </header>
    <p v-if="error" class="dc-error" role="alert">{{ error }}</p><p class="dc-save" aria-live="polite">{{ saveHint }} · 修订 {{ card.revision }} · {{ phaseLabel }} · 日历状态未核验</p>
    <ul v-if="audit.length" class="no-print"><li v-for="item in audit" :key="item.revision">修订 {{ item.revision }} · {{ item.action }} · {{ item.recorded_at }}</li></ul>
    <section class="dc-dashboard" aria-label="终判状态">
      <article><small>终判状态</small><h2>{{ summary.state }}</h2><p>{{ result?.decision?.reason || '自动证据不完整，不能由手工勾选补成通过。' }}</p></article>
      <article><small>风险与仓位</small><h2>{{ summary.cap }}% 上限</h2><p>策略未经回测，总仓永久防线 ≤50%；不是目标仓位。</p></article>
      <article><small>数据新鲜度</small><h2>{{ summary.missing.length ? '未满足 ≤120秒' : '参见逐项时间戳' }}</h2><p>快照 {{ result?.as_of || '未收到' }}<br>历史数据日 {{ historical?.history_as_of || '未知' }}</p></article>
    </section>
    <div class="dc-alert"><strong>缺失 / 阻断原因</strong><ul><li v-for="(m,i) in blockers" :key="i">{{ m }}</li></ul><p>空仓为风险建议，不代表已卖出；T+1、停牌、跌停无法成交时保留实际占用仓位并制定次日预案。数据恢复不得追补当日信号。</p></div>
    <section class="dc-section" aria-label="手工数字证据">
      <h2>手工数字证据 · unverified · 仅规则参考</h2><p>以下字段与引擎同名；金额须使用一致单位。unknown 不等于否。时间为用户记录，不证明实时来源；不填写则保持缺失。全部输入永不授权开仓。</p>
      <label>用户记录时间（含时区 ISO）<input v-model="card.evidence.captured_at" placeholder="2026-10-28T14:45:00+08:00" /></label>
      <details v-for="code in ['883957','883994']" :key="code"><summary>{{ code }} 核心数字（手工）</summary><DualCoreEvidenceFields :model="card.evidence.indices[code]" kind="core" :prefix="`core.${code}`" /><div v-if="code==='883994'"><label v-for="(_,i) in card.evidence.indices[code].macd_hist" :key="i">周MACD柱 {{ i+1 }}（从旧到新）<input v-model.number="card.evidence.indices[code].macd_hist[i]" type="number" step="any" /></label></div></details>
      <details><summary>账户风险（手工）</summary><DualCoreEvidenceFields :model="card.account" kind="account" prefix="account" /></details>
      <details><summary>行业数字与风险（手工）</summary><DualCoreEvidenceFields :model="card.evidence.industry" kind="industry" prefix="industry" /></details>
    </section>
    <section v-if="reference" class="dc-section" aria-label="手工计算参考"><h2>手工计算参考 · unverified · 不可授权开仓</h2><p>顶层仍按实时缺失阻断。下列是用户输入的规则推导，不是已核验行情，不是买卖指令。</p><p>{{ reference.status }} · 参考上限 {{ reference.verdict?.cap }}% · {{ reference.decision?.reason }} · can_open=false</p><table><thead><tr><th>规则</th><th>输入</th><th>阈值</th><th>参考结果</th></tr></thead><tbody><tr v-for="(c,i) in reference.checks" :key="i"><td>{{ c.key }}</td><td>{{ display(c.actual) }}</td><td>{{ display(c.threshold) }}</td><td>{{ passText(c.pass) }} · {{ c.reason }}</td></tr></tbody></table><p>持仓风险建议（均未执行）：{{ display(reference.decision?.orders) }}</p><article v-for="(candidate,i) in result?.candidate_results || []" :key="i">{{ candidate.code }} · {{ display(candidate.evidence_analysis?.decision) }}</article></section>
    <nav class="dc-step-nav no-print"><a v-for="(title,i) in stepTitles" :key="i" :href="`#dc-step-${i}`">{{ i }} {{ title }}</a></nav>
    <section id="dc-step-0" class="dc-section"><h2>Step 0 · 数据完整性</h2><p>09:30 / 14:45 各查一次。双指数、情绪五项、行业和个股缺一或延迟＞2分钟，不通过，停止后续开仓判断。</p><div class="dc-scroll"><table><thead><tr><th>自动检查</th><th>实际值</th><th>阈值</th><th>来源 / 时间</th><th>状态</th></tr></thead><tbody><tr v-for="(c,i) in autoChecks" :key="i"><td>{{ c.key }}</td><td>{{ display(c.actual) }}</td><td>{{ display(c.threshold) }}</td><td>{{ c.source || '自动接口 · 来源未提供' }}<br>{{ c.timestamp || '时间缺失' }}</td><td :class="c.pass === true ? 'dc-pass':'dc-fail'">{{ passText(c.pass) }}<br>{{ c.reason }}</td></tr><tr v-if="!autoChecks.length"><td colspan="5">unknown · 尚无自动检查，按不通过处理</td></tr></tbody></table></div></section>
    <section id="dc-step-1" class="dc-section"><h2>Step 1 · 周线一票否决</h2><p>883957 破5周线（无缓冲）/ 周跌幅≥8% / 跌停≥50家，任一成立 → 空仓。883994 破5周线或高位巨阴 → 降两级，禁止≥3连板。</p><div class="dc-grid"><article v-for="code in ['883957','883994']" :key="code"><h3>{{ code }} · 历史参考，非14:45现价</h3><dl><template v-for="(label,key) in coreFields" :key="key"><dt>{{ label }}</dt><dd>{{ display(historical?.data?.[code]?.[key]) }}</dd></template></dl></article></div><p class="dc-muted">周中允许未完成周K；周五15:00后复核，不一致自下一交易日起生效。手工周线记录见下方证据区，不得替代自动输入。</p></section>
    <section id="dc-step-2" class="dc-section"><h2>Step 2 · 情绪面板</h2><table><thead><tr><th>指标</th><th>达标阈值</th><th>手工实测</th><th>自动状态</th></tr></thead><tbody><tr v-for="e in emotions" :key="e.key"><td>{{ e.label }}</td><td>{{ e.rule }}</td><td><input v-model.number="card.evidence.emotion[e.key]" type="number" placeholder="未填" aria-label="手工情绪实测" /><small>手工 · 不参与实时授权</small></td><td>{{ emotionCheck(e.key) }}</td></tr></tbody></table><p>4–5分正常；3分降一级；≤2分降两级（最低试错）；≤1分只减不加。缺失不是0分，是数据阻断。</p></section>
    <section id="dc-step-3" class="dc-section"><h2>Step 3 · 日线发令枪</h2><div class="dc-grid"><article><h3>A · 883957 ≥ 昨高 ×1.003</h3><p>自动实际 {{ checkValue('gun_a','actual') }} / 阈值 {{ checkValue('gun_a','threshold') }}</p></article><article><h3>B · 883994 ≥ 近60交易日最高 ×1.005</h3><p>自动实际 {{ checkValue('gun_b','actual') }} / 阈值 {{ checkValue('gun_b','threshold') }}</p></article></div><p>必须 A 且 B；60日最高使用盘中最高价口径，不是全历史最高。14:30曾达标但14:45回落视为未触发。</p></section>
    <section id="dc-step-4" class="dc-section"><h2>Step 4 · 均线阶梯，累计叠加</h2><ol><li>883957 &lt; MA5×0.997 → 降一级</li><li>883957 &lt; MA10×0.997 → 在降级基础上仓位减半</li><li>883957 &lt; MA20×0.997 → 只做中军，同时禁止扫板和≥3连板</li><li>883994 &lt; MA5×0.997 → 只能试错≤10%（仅开仓豁免）</li><li>双指数破MA5且连续2日反抽未收回 → 空仓</li></ol><p>阶梯结论：{{ result?.position?.only_core ? '只做中军' : '见自动检查（缺失不推断正常）' }}；持仓管理不享有5日线豁免。</p></section>
    <section id="dc-step-5" class="dc-section"><h2>Step 5 · 顶背离</h2><p>883994 创60日新高，同时周MACD红柱连续2周缩短或DIF下穿DEA：只做首板/中军，禁止≥3连板，总仓≤50%、单票≤30%。优先于“重仓出击”。</p><p>自动结论：{{ checkValue('top_divergence','actual') }}</p></section>
    <section id="dc-step-6" class="dc-section"><h2>Step 6 · 账户与仓位预算</h2><div class="dc-form-grid"><label v-for="(label,key) in accountFields" :key="key">{{ label }}<input v-model.number="card.account[key]" type="number" placeholder="未知，不能视为0" /><small>手工台账</small></label><label class="dc-inline"><input v-model="card.checklist.has_new_entry" type="checkbox" />今日计划新开仓（未执行）</label></div><p class="dc-budget">自动档位上限 {{ summary.cap }}% → {{ card.checklist.has_new_entry ? '新仓减20个百分点' : '不扣新仓缓冲' }} → 最终预算 {{ budget }}%。{{ budget === 0 ? '预算为0，禁止新开仓。' : '仍须满足所有过滤及风险约束。' }}</p><p>试错10%−20个百分点＝0，不是8%。连亏2笔下一笔再五折；连亏3笔停2交易日；当日亏损≥1.5%禁开仓、≥2%清仓建议；周亏≥5%下周限震荡；连续2月胜率＜35%停实盘回测。</p><details><summary>单票 / 持仓数联动表</summary><p>进攻2只35–40% / 3只25–33% / 4只20–25% / 5只14–20%；震荡1只25–30% / 2只15–25% / 3只10–17%；试错1只5–10%。常规单票≤35%，仅龙一三周期全共振进攻可到40%；同时不得超过总仓。最多5只。</p><p>加仓必须三周期共振、浮盈≥3%、未触单票上限；亏损股不得摊平。unverified 上限50%仍约束进攻表。</p></details></section>
    <section id="dc-step-7" class="dc-section"><h2>Step 7 · 行业与个股过滤</h2><p>unknown / 失败均不准入。手工备注不是自动通过。行业候选参考：{{ (historical?.industry_top || []).map(x=>x.name).join('、') || '无数据' }}</p><div class="dc-grid"><article v-for="group in filterGroups" :key="group.title"><h3>{{ group.title }}</h3><ul><li v-for="text in group.items" :key="text">{{ text }}</li></ul></article></div><button class="no-print" @click="addCandidate">添加手工候选</button><article v-for="(candidate,i) in card.candidates" :key="i" class="dc-row"><input v-model="candidate.code" placeholder="代码" aria-label="候选代码" /><input v-model="candidate.industry" placeholder="二级行业" aria-label="候选行业" /><input v-model="candidate.note" placeholder="过滤证据 / 未确认项" aria-label="候选备注" /><DualCoreEvidenceFields :model="candidate" kind="stock" :prefix="`candidate.${i}`" /><strong>unknown · 不准入（手工）</strong><button class="no-print" @click="card.candidates.splice(i,1)">移除</button></article></section>
    <section id="dc-step-8" class="dc-section"><h2>Step 8 · 风控与持仓建议（未执行）</h2><p class="dc-alert">文档冲突裁决：按策略§3.2编号树，Step5止盈先于Step6均线减仓，不按末尾宣誓顺序重排。Step6与Step7取较大减幅、不叠加；清仓永远覆盖减仓。</p><ol start="0"><li v-for="text in decisionTree" :key="text">{{ text }}</li></ol><p>当前建议：{{ result?.decision?.action || 'block' }} · {{ result?.decision?.reason || '缺失阻断，仅做风险核对' }} · 未执行</p><button class="no-print" @click="addHolding">添加手工持仓</button><article v-for="(holding,i) in card.holdings" :key="i" class="dc-holding"><div class="dc-row"><input v-model="holding.code" placeholder="持仓代码" aria-label="持仓代码" /><input v-model.number="holding.weight" type="number" placeholder="仓位%" aria-label="持仓仓位" /><DualCoreEvidenceFields :model="holding" kind="holding" :prefix="`holding.${i}`" /><button class="no-print" @click="card.holdings.splice(i,1)">移除</button></div><p>{{ holding.bought_today ? 'T+1受限，当日不可卖，最早次日执行止损。' : '可卖数量与成交能力尚未核验。' }} {{ holding.limit_down_locked ? '卖出受限，仓位仍计占用，禁止新增。' : '' }} 风险处置建议未执行，不更新实际账户。</p><input v-model="holding.plan" placeholder="风险处理 / 次日跳空预案（手工）" aria-label="持仓风险预案" /></article></section>
    <section id="dc-step-9" class="dc-section"><h2>Step 9 · 交易记录</h2><p>本页不提交订单。仅用户自行录入成交事实，保存记录不代表成交；默认“未执行”。</p><button class="no-print" @click="addTrade">添加手工记录</button><div class="dc-scroll"><table><thead><tr><th>时间</th><th>代码</th><th>方向</th><th>价格</th><th>股数</th><th>账户%</th><th>状态 / 理由</th></tr></thead><tbody><tr v-for="(trade,i) in card.trades" :key="i"><td><input v-model="trade.time" type="time" aria-label="交易时间" /></td><td><input v-model="trade.code" aria-label="交易代码" /></td><td><select v-model="trade.direction" aria-label="交易方向"><option>买入</option><option>卖出</option></select></td><td><input v-model.number="trade.price" type="number" aria-label="成交价格" /></td><td><input v-model.number="trade.shares" type="number" aria-label="成交股数" /></td><td><input v-model.number="trade.weight" type="number" aria-label="成交仓位" /></td><td><select v-model="trade.status" aria-label="成交状态"><option>未执行</option><option>用户记录已成交</option><option>委托未成交</option><option>撤单</option></select><input v-model="trade.reason" placeholder="手工事实 / 理由" aria-label="交易理由" /><button class="no-print" @click="card.trades.splice(i,1)">删除</button></td></tr></tbody></table></div></section>
    <section id="dc-step-10" class="dc-section"><h2>Step 10 · 次日预案</h2><p>跳空≤2%且未破止损按14:45常规判断；＞2%且破止损考虑9:25集合竞价卖出；≥5%开盘处置，跌停无法成交则持续挂单，不摊平。均为预案，未执行。</p><div class="dc-form-grid"><label v-for="(label,key) in planFields" :key="key">{{ label }}<textarea v-model="card.next_day_plan[key]" rows="2" /></label></div></section>
    <section id="dc-step-11" class="dc-section"><h2>Step 11 · 盘后复盘</h2><div class="dc-form-grid"><label v-for="(label,key) in reviewFields" :key="key">{{ label }}<textarea v-model="card.review[key]" rows="3" /></label></div><details class="no-print"><summary>补充手工证据（永不伪装实时）</summary><label>指数 / 周线核对笔记<textarea v-model="card.evidence.indices.notes" rows="3" /></label><p>所有浏览器输入存为 manual。自动项缺失仍为不通过。保存按登录用户、交易日、阶段隔离；版本冲突不会静默覆盖。</p></details></section>
    <footer><p>策略v2.0 初始假设，未经回测。先保护账户，再谈信号。已填写 {{ card.trades.length }} 条手工交易记录；当前页面无自动交易能力。</p></footer>
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api'
import DualCoreEvidenceFields from './DualCoreEvidenceFields.vue'
import { createCard, emptyEvidence, buildManualPayload, newEntryCap, safeSummary, toCardBody } from '../utils/dualCoreCard.mjs'
const phases = [['data_check','09:30 数据检查'],['preview','14:30 预警（非执行）'],['final','14:45 终判'],['weekly_review','周五收盘复核']]
const tradeDate = ref(new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit' }).format(new Date()))
const phase = ref('final'), card = ref(createCard()), audit = ref([]), historical = ref(null), result = ref(null), busy = ref(false), error = ref(''), saveHint = ref('尚未保存')
const summary = computed(() => safeSummary(result.value))
const reference = computed(() => result.value?.evidence_analysis || card.value.calculation?.evidence_analysis)
const budget = computed(() => newEntryCap(summary.value.cap, card.value.checklist.has_new_entry))
const phaseLabel = computed(() => phases.find(p => p[0] === phase.value)?.[1])
const autoChecks = computed(() => result.value?.checks || [])
const blockers = computed(() => [...summary.value.missing, ...(result.value?.verdict?.reasons || []), ...(card.value.day_guard?.reasons || [])])
const stepTitles = ['完整性','周线','情绪','发令枪','阶梯','背离','仓位','选股','风控','交易','预案','复盘']
const coreFields = { close:'历史收盘', wma5:'5周均线', wma10:'10周均线', wma20:'20周均线', w_pct:'周涨跌%', ma5:'日MA5', ma10:'日MA10', ma20:'日MA20' }
const emotions = [{key:'limit_up',label:'涨停家数（剔除ST/新股首日）',rule:'≥50'}, {key:'broken',label:'炸板家数',rule:'炸板/(涨停+炸板)≤25%'},{key:'height',label:'连板高度',rule:'≥4板'}, {key:'limit_down',label:'跌停家数（含ST）',rule:'≤10，≥50一票否决'}, {key:'amount',label:'两市成交额',rule:'≥5日均额'}, {key:'amount_ma5',label:'两市5日平均成交额',rule:'与成交额单位一致'}]
const accountFields = { daily_pnl:'当日盈亏（%）', weekly_pnl:'本周盈亏（%）', consecutive_losses:'连续亏损笔数', total_weight:'实际总仓位（%）' }
const planFields = { stop:'个股止损价 / 触发条件', reduce:'减仓线（MA5×0.997）', profit:'止盈线', gap:'跳空 / T+1 / 跌停预案', missing:'次日数据缺失处理（空仓建议）' }
const reviewFields = { gun_a:'883957 实测 / 昨高 / 发令枪A', gun_b:'883994 实测 / 60日最高 / 发令枪B', position:'档位 vs 实际仓位 / 不一致原因', skipped:'是否跳过编号决策树', risk:'连亏 / 熔断 / 周累计亏损', reflection:'纪律、情绪与周期反思' }
const filterGroups = [{title:'行业准入',items:['周线上升通道；MA20连续4周向上、近20周新高超过前20周','同花顺二级行业当日涨幅前3，成交额≥20日均额×1.3','5日涨幅≥3%，5日内≥4日收在MA5上','所属行业当日排名前10；左侧下跌或5日涨幅负数剔除']},{title:'个股准入',items:['周收盘≥MA30周（不放宽）；近8周MACD金叉，DIF<0仅试错','一阳穿多线 / 箱体突破 / N字反包之一','回踩不破MA5或放量突破；走坏仅试错','排除ST/退市、近3月减持/大宗折价>5%、近1月预亏/减值/立案/非标','排除停牌/复牌首日、市值<30亿或日均成交<3亿','排除近5日≥2跌停、20日跌幅>30%、长上影/放量滞涨、一字涨停']}]
const decisionTree = ['数据不完整 → 空仓建议，停止开仓判定','持仓黑天鹅 → 该股清仓建议，禁开仓','账户亏损≥2% → 全清建议，停手','883957 < 昨低×0.997 → 全清建议，禁开仓','个股亏损≤−5% → 清仓建议，不买回','高位放量阴线/长上影 → 分批止盈建议','持仓 < MA5×0.997 → 减半建议','883994冲高回落且≥3连板 → 减半建议，与上项取大','当日已清仓/止损 → 不再开仓','发令枪A且B未同时触发 → 不开仓','选股过滤未全通过（含unknown）→ 不开仓','按档位及单票/总仓硬约束给建议；不下单']
function display(v) { return v === undefined || v === null || v === '' ? 'unknown / 缺失' : typeof v === 'object' ? JSON.stringify(v) : String(v) }
function passText(pass) { return pass === true ? '通过' : pass === false ? '不通过' : 'unknown · 不通过' }
function checkValue(key, field) { return display(autoChecks.value.find(c => c.key === key)?.[field]) }
function emotionCheck(key) { return passText(autoChecks.value.find(c => c.key === `emotion_${key}`)?.pass) }
function addCandidate() { card.value.candidates.push({...emptyEvidence('stock'),code:'',industry:'',note:'',source:'manual'}) }
function addHolding() { card.value.holdings.push({...emptyEvidence('holding'),code:'',plan:'',source:'manual'}) }
function addTrade() { card.value.trades.push({time:'',code:'',direction:'买入',price:null,shares:null,weight:null,status:'未执行',reason:'',source:'manual'}) }
const base = '/api/portfolio/dual-core/cards'
const target = () => `${base}/${tradeDate.value}/${phase.value}`
let contextRevision = 0
function hydrateCard(saved) {
  const empty = createCard()
  const indices = Object.fromEntries(['883957','883994'].map(code=>[code,{...empty.evidence.indices[code],...saved.evidence?.indices?.[code]}]))
  return { ...empty, ...saved, account:{...empty.account,...saved.account}, evidence: { ...empty.evidence, ...saved.evidence, industry:{...empty.evidence.industry,...saved.evidence?.industry}, emotion: { ...saved.evidence?.emotion }, indices } }
}
async function switchCard() {
  const context = ++contextRevision, path = target()
  busy.value = true; error.value = ''; result.value = null; historical.value = null; audit.value = []; card.value = createCard(); saveHint.value = '读取中…'
  try {
    const saved = await api.get(path, {silent:true})
    if (context !== contextRevision) return
    card.value = hydrateCard(saved); saveHint.value = `已读取 ${saved.updated_at}`
  } catch(e) {
    if (context !== contextRevision) return
    if (e.response?.status === 404) saveHint.value = '新卡片，尚未保存'
    else { error.value = '卡片读取失败；禁止覆盖未知版本，请重试。'; saveHint.value = '读取失败' }
  } finally { if(context === contextRevision) busy.value = false }
}
async function refresh() {
  const context = contextRevision
  busy.value = true; error.value = ''
  try { const response = await api.get('/api/screener/dual-core'); if(context !== contextRevision) return; historical.value = response; if (historical.value?.version !== '2.0' || !Array.isArray(historical.value?.checks) || !Array.isArray(historical.value?.source_missing)) throw new Error('v2 schema mismatch'); result.value = historical.value }
  catch { historical.value = null; result.value = null; error.value = '自动数据读取失败，维持缺失阻断。' }
  finally { busy.value = false }
}
async function evaluateManual() {
  const context = contextRevision
  busy.value = true; error.value = ''
  try {
    const response = await api.post('/api/screener/dual-core/evaluate-v2', buildManualPayload(card.value,tradeDate.value,phase.value))
    if(context !== contextRevision) return
    if (response.version !== '2.0' || !Array.isArray(response.source_missing)) throw new Error('schema mismatch')
    result.value = response
  } catch { result.value = null; error.value = '手工校验失败，维持缺失阻断。' }
  finally { busy.value = false }
}
async function save() {
  if (saveHint.value === '读取失败') return
  busy.value = true; error.value = ''
  try {
    await api.put(target(), toCardBody(card.value,tradeDate.value,phase.value))
    const saved = await api.get(target())
    card.value = hydrateCard(saved); saveHint.value = `已保存并回读 ${saved.updated_at}`
  } catch(e) { error.value = e.response?.status === 409 ? '修订冲突：未覆盖服务器。请先导出当前草稿，再重新读取卡片。' : '保存或回读失败，不能确认已保存。'; saveHint.value = '尚未确认保存' }
  finally { busy.value = false }
}
function downloadJson(value, suffix) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(value,null,2)], {type:'application/json'})); a.download = `dual-core-${tradeDate.value}-${phase.value}-${suffix}.json`; a.click(); URL.revokeObjectURL(a.href)
}
function exportDraft() { downloadJson(toCardBody(card.value,tradeDate.value,phase.value),'manual-draft') }
async function loadAudit() {
  const context = contextRevision, path = target()
  try { const response = await api.get(`${path}/audit`); if(context === contextRevision) audit.value = response.items }
  catch { if(context === contextRevision) error.value = '修订历史读取失败。' }
}
async function exportCard() {
  try { const saved = await api.get(`${target()}/export`); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(saved,null,2)], {type:'application/json'})); a.download = `dual-core-${tradeDate.value}-${phase.value}.json`; a.click(); URL.revokeObjectURL(a.href) }
  catch { error.value = '导出失败，未生成留档。' }
}
function printCard() { window.print() }
onMounted(async () => { await switchCard(); await refresh() })
</script>

<style scoped>
.dual-core-v2{max-width:1280px;margin:auto;color:#243248;line-height:1.65}.dc-hero{background:#142638;color:#fff;padding:24px;border-radius:14px}.dc-hero h1{margin:4px 0;font-size:28px}.dc-hero small{color:#9ddad3;letter-spacing:1px}.dc-tools,.dc-row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.dc-tools label{font-size:12px}.dc-tools button{background:#e4f3ed}.dc-dashboard{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px;margin:16px 0}.dc-dashboard article,.dc-section{background:var(--el-bg-color,#fff);border:1px solid #dfe5eb;border-radius:10px;padding:18px}.dc-dashboard h2{margin:8px 0;font-size:21px}.dc-alert{background:#fff4de;border-left:4px solid #bb7a17;padding:14px 18px;border-radius:4px}.dc-error{background:#ffe9e9;color:#a72323;padding:12px}.dc-save,.dc-muted,small{color:#64748b;font-size:12px}.dc-hero p{color:#d6e4ed}.dc-step-nav{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.dc-step-nav a{padding:5px 10px;border:1px solid #dfe5eb;border-radius:20px;color:#315463;text-decoration:none}.dc-section{margin:14px 0;scroll-margin-top:16px}.dc-section h2{font-size:19px;margin:0 0 12px;color:#175d63}.dc-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.dc-grid article{background:#f5f8fa;padding:12px;border-radius:6px}.dc-grid h3{margin:0 0 8px;font-size:15px}dl{display:grid;grid-template-columns:1fr 1fr;gap:5px}dd{margin:0;font-variant-numeric:tabular-nums}.dc-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.dc-form-grid label{display:grid;gap:5px}input,select,textarea{box-sizing:border-box;max-width:100%;min-width:0;border:1px solid #c6d0db;border-radius:5px;padding:8px;background:#fff;color:#243248;font:inherit;font-size:13px}input[type=checkbox]{width:auto}textarea{width:100%;resize:vertical}.dc-inline{display:flex!important;align-items:center}.dc-row input{max-width:210px}.dc-holding{padding:12px;border:1px solid #dfe5eb;border-radius:6px;margin:10px 0}.dc-holding>input{width:100%}button{border:1px solid #b4cbd0;border-radius:5px;padding:7px 12px;background:#edf5f5;color:#164d53;cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:10px;text-align:left;border-bottom:1px solid #dfe5eb;vertical-align:top}th{background:#f2f6f8}td input{width:100%;min-width:75px}td small{display:block}.dc-scroll{overflow-x:auto}.dc-pass{color:#18725b}.dc-fail{color:#a33832}.dc-budget{border:1px solid #b4cbd0;background:#eff8f6;padding:12px}li{margin:5px 0}footer{font-size:12px;color:#64748b}summary{cursor:pointer;color:#22666c}
@media(max-width:760px){.dc-dashboard,.dc-grid,.dc-form-grid{grid-template-columns:1fr}.dc-hero{padding:16px}.dc-hero h1{font-size:23px}.dc-tools{align-items:stretch}.dc-tools label{width:100%}.dc-section{padding:14px}.dc-row input{max-width:100%;flex:1}.dc-scroll table{min-width:580px}}
@page{size:A4 portrait;margin:12mm}
@media print{.no-print{display:none!important}.dual-core-v2{max-width:none;font-size:10pt;line-height:1.4;color:#111}.dc-hero{background:none!important;color:#111;border-bottom:2px solid #111;padding:0 0 8px}.dc-hero small,.dc-hero p{color:#333}.dc-hero h1{font-size:18pt}.dc-dashboard{grid-template-columns:2fr 1fr 1fr;gap:6px}.dc-dashboard article,.dc-section{padding:8px;border:1px solid #aaa;border-radius:0;box-shadow:none;margin:8px 0}.dc-section h2{color:#111;font-size:13pt;break-after:avoid}.dc-grid{grid-template-columns:1fr 1fr;gap:8px}.dc-grid article,.dc-alert,.dc-budget{background:#fff!important;border:1px solid #aaa}.dc-alert{padding:8px}.dc-scroll{overflow:visible}table{font-size:9pt;table-layout:fixed;min-width:0!important}td,th{overflow-wrap:anywhere;padding:5px}thead{display:table-header-group}tr,.dc-holding,.dc-dashboard article{break-inside:avoid}.dc-section{break-inside:auto}input,select,textarea{border:none;border-bottom:1px dotted #888;padding:2px;font-size:9pt;background:transparent;color:#111;appearance:none;resize:none}textarea{field-sizing:content;overflow:visible;min-height:36px}.dc-form-grid{grid-template-columns:1fr 1fr}.dc-row input{max-width:120px}.dc-pass,.dc-fail{color:#111}p,li{orphans:3;widows:3}}
</style>
<style>
@media print{body:has(.dual-core-v2) .el-aside,body:has(.dual-core-v2) .el-header,body:has(.dual-core-v2) .mobile-nav,body:has(.dual-core-v2) .el-backtop{display:none!important}body:has(.dual-core-v2),body:has(.dual-core-v2) #app,body:has(.dual-core-v2) .el-container,body:has(.dual-core-v2) .el-main{height:auto!important;overflow:visible!important;min-width:0!important;margin:0!important;padding:0!important;background:#fff!important}}
</style>
