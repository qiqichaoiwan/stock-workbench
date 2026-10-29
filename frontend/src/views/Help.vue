<template>
  <el-card class="page-card help-page">
    <template #header>
      <div class="card-head">
        <span>帮助中心</span>
        <span class="muted hide-mobile" style="font-size:12px">先看「每日操作流程」,再按模块查细节</span>
        <span class="grow" />
        <el-input v-model="query" placeholder="搜索功能 / 关键词,如 止损、17:05" clearable
                  style="width:250px" />
      </div>
    </template>

    <!-- 重点摘要:一眼看清用哪个模块、三条硬约束 -->
    <section class="hero">
      <div class="hero-main">
        <h2>买股票先看这里</h2>
        <p class="hero-lead">
          从 <router-link to="/pool" class="hero-link">选股池</router-link> 出票,用
          <router-link to="/dual-core" class="hero-link">双核共振</router-link> 决定当天开不开仓、开多少;
          其余模块只做辅助确认和风控。选股池是候选名单,<b>不是买入指令</b>。
        </p>
        <div class="hero-rules">
          <span class="rule rule-danger">数据缺失 / 阻断 → 当日空仓,不手工补通过</span>
          <span class="rule rule-warn">策略 unverified · 总仓 ≤50%(上限,不是目标)</span>
          <span class="rule rule-info">仅参考、不构成信号:龙虎榜 · 新闻甄别 · 策略卡</span>
        </div>
        <p class="hero-foot">
          本系统只给建议与留档,<b>不会自动下单</b>;页面上的「空仓建议」不代表已卖出。本系统不构成投资建议,盈亏自负。
        </p>
      </div>
    </section>

    <!-- 每日流程:7 步时间线 -->
    <section class="block">
      <div class="block-head">
        <span class="block-title">每日操作流程</span>
        <span class="block-sub">按顺序走完,基本不会漏掉关键动作</span>
      </div>
      <ol class="flow">
        <li v-for="step in workflow" :key="step.no" class="flow-step">
          <span class="flow-no">{{ step.no }}</span>
          <div class="flow-body">
            <div class="flow-top">
              <span class="flow-time">{{ step.time }}</span>
              <router-link class="flow-module" :to="step.to">{{ step.module }}</router-link>
              <span class="flow-act">{{ step.act }}</span>
            </div>
            <p class="flow-detail">{{ step.detail }}</p>
          </div>
        </li>
      </ol>
    </section>

    <!-- 模块速查:按工作台分组,点标题展开 -->
    <div class="block">
      <div class="block-head">
        <span class="block-title">模块速查</span>
        <span class="block-sub">
          每条都按「看什么 / 怎么用 / 更新 / 空数据 / 权限」拆开;点标题展开
          <template v-if="query.trim()"> · 搜索到 {{ matchedCount }} 项</template>
        </span>
        <span class="grow" />
        <el-button v-if="matchedCount" link type="primary" size="small" @click="toggleAll">
          {{ allOpen ? '全部收起' : '全部展开' }}
        </el-button>
      </div>

      <div v-for="group in filteredGroups" :key="group.name" class="panel-group">
        <div class="panel-group-head">
          <span class="panel-group-name">{{ group.name }}</span>
          <span class="muted" style="font-size:12px">{{ group.items.length }} 项</span>
        </div>
        <el-collapse v-model="openIds">
          <el-collapse-item v-for="s in group.items" :key="s.id" :name="s.id">
            <template #title>
              <div class="sec-title">
                <span class="sec-name">{{ s.title }}</span>
                <el-tag v-if="s.badge" size="small" :type="s.badgeType || 'info'" effect="plain">
                  {{ s.badge }}
                </el-tag>
                <span class="sec-hint">{{ s.hint }}</span>
              </div>
            </template>
            <div class="sec-body">
              <div v-for="row in s.rows" :key="row.k" class="row">
                <span class="row-label" :class="'tone-' + row.tone">{{ row.k }}</span>
                <p class="row-text">{{ row.t }}</p>
              </div>
              <div class="sec-foot">
                <router-link :to="s.to" class="sec-jump">前往「{{ s.title }}」→</router-link>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
      <el-empty v-if="!filteredGroups.length" description="没有匹配的帮助条目,换个关键词试试" />
    </div>
  </el-card>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { INIT_ORDER_TEXT, taskName } from '../utils/tasks'

const query = ref('')
const openIds = ref([])

const workflow = [
  {
    no: '①', time: '开盘前', module: '仪表盘 · 大盘指数', to: '/dashboard', act: '定总仓位',
    detail: '看市场状态、建议仓位与拥挤度回避名单(SC-U08);回避名单里的行业在选股池/赛道页会被自动规避。',
  },
  {
    no: '②', time: '09:30', module: '双核共振', to: '/dual-core', act: '数据检查',
    detail: '出现「缺失 / 阻断原因」就按空仓处理,不要手工补通过;14:30 的预警只是观察,不是执行信号。',
  },
  {
    no: '③', time: '盘前 / 盘中', module: '选股池', to: '/pool', act: '选标的',
    detail: '优先看评级「买入」且评分 ≥65;结合现价/今日涨跌幅判断是否已追高(交易时段实时、收盘后为最近收盘快照);弱市守门触发时主池暂停新增,不要手动加仓。',
  },
  {
    no: '④', time: '盘中', module: '技术信号', to: '/signals', act: '定买点',
    detail: '按 buy_setup(入场组合)过滤,确认池内个股已有入场结构;信号来自上一交易日 16:40,只作确认,不当追涨依据。',
  },
  {
    no: '⑤', time: '14:45', module: '双核共振', to: '/dual-core', act: '终判',
    detail: '只认「终判可开仓(仅建议)」;总仓永久防线 ≤50%,新开仓再减 20 个百分点,试错 10% 减完为 0 即禁开仓;883957 < 昨低×0.997(退军线)当日禁止开仓。',
  },
  {
    no: '⑥', time: '买入后', module: '我的持仓', to: '/portfolio', act: '上止损',
    detail: '录入买入价 / 数量 / 买入日期,系统算保本价与止损线;「已破止损」标红的优先处理;T+1/停牌/跌停无法成交时保留实际占用仓位并制定次日预案。',
  },
  {
    no: '⑦', time: '收盘后', module: '任务中心', to: '/tasks', act: '等更新',
    detail: '16:15 行业 → 16:30 市场状态 → 16:40 技术信号 → 16:50 双核指数 → 17:05 策略与选股池;盘中看到的是上一交易日结果,当日买点放到次日结合盘面确认。',
  },
]

const SECTIONS = [
  {
    id: 'dashboard', title: '仪表盘', group: '选股工作台', to: '/dashboard',
    hint: '开盘前定总仓位', badge: '第①步', badgeType: 'primary',
    rows: [
      { k: '看什么', tone: 'what', t: '市场状态(趋势/周期/建议仓位)、拥挤度回避名单(SC-U08)、商品涨价信号、选股池表现、今日技术信号的汇总快照,右侧指数条为辅助参考。' },
      { k: '怎么用', tone: 'how', t: '每天开盘前扫一眼市场状态与回避名单决定总体仓位;拥挤度名单中的行业在选股池与赛道页会被自动回避;信号表按类型标签(入场组合/弱于行业等)快速定位。' },
      { k: '数据何时更新', tone: 'update', t: '交易日收盘后流水线依次落库——行业监测 16:15、商品 16:20、市场状态 16:30、技术信号 16:40、指数 16:45、双核指数 16:50、策略与池表现 17:05;盘中看到的是上一个交易日结果。' },
      { k: '空数据怎么办', tone: 'empty', t: `页面会提示去任务中心运行对应任务(${taskName('market_state')} / ${taskName('industry_daily')} / ${taskName('signals')} / ${taskName('strategy')});若加载失败会显示重试按钮,点重试即可。` },
      { k: '权限', tone: 'perm', t: '所有登录用户可见。' },
    ],
  },
  {
    id: 'market', title: '大盘指数', group: '选股工作台', to: '/market',
    hint: '判断大盘环境与资金', badge: '第①步', badgeType: 'primary',
    rows: [
      { k: '看什么', tone: 'what', t: '主要指数点位涨跌、市场总览(上涨/下跌家数、涨跌停、成交额)、板块资金(行业/概念净流入)、商品周期信号。' },
      { k: '怎么用', tone: 'how', t: '指数表判断大盘环境;点击某板块行可展开其近30日净流入迷你趋势,确认资金是持续流入还是单日脉冲。' },
      { k: '口径', tone: 'metric', t: '成交额以东财日线为准;当日走腾讯/新浪备源时,用新浪指数快照的「成交额」补当日值(仍缺失显示 -);「点位源」列只表示点位与涨跌幅来源。指数行标注「数据日 + 收盘 15:00」,市场总览/板块资金标注「数据日 + 采集时点」,采集发生在交易时段外会标「盘前采集/盘后采集」。' },
      { k: '数据何时更新', tone: 'update', t: `指数表每交易日 16:45 定时 ${taskName('sync_indexes')} 落库(手动重跑会晚于该时刻,以表内数据日为准);市场总览与板块资金为盘中快照(9:35 起每30分钟,9:35/10:05/10:35/11:05/11:35/13:05/13:35/14:05),商品周期 16:20 落库。` },
      { k: '空数据怎么办', tone: 'empty', t: `空状态会写明去任务中心运行哪个任务(${taskName('sync_indexes')} / ${taskName('fund_flow')} / ${taskName('market_stat')} / ${taskName('commodities')});收盘前指数行为空属正常,等收盘流水线跑完再看。` },
      { k: '权限', tone: 'perm', t: '所有登录用户可见。' },
    ],
  },
  {
    id: 'dual-core', title: '双核共振 v2.0', group: '选股工作台', to: '/dual-core',
    hint: '开不开仓的总开关', badge: '第②⑤步', badgeType: 'danger',
    rows: [
      { k: '看什么', tone: 'what', t: '14:45 终判、缺失阻断、数据新鲜度、风险仓位,以及 Step0–11 每日执行卡。14:30 只预警,不是执行信号。自动接口保留的日线历史明确标为参考,不能冒充 ≤2 分钟的实时数据。' },
      { k: '怎么用', tone: 'how', t: '先做数据完整性检查,再看周线否决、情绪五项、A且B发令枪、累进均线阶梯、顶背离及账户约束。A 为 883957 ≥ 昨高×1.003;B 为 883994 ≥ 近60交易日最高×1.005;退军线为 883957 < 昨低×0.997。行业和个股 unknown 一律不准入,手工输入仅留档,不会把自动缺失变成通过。' },
      { k: '仓位与冲突裁决', tone: 'danger', t: '策略 unverified 总仓 ≤50%;新开仓在上限再减 20 个百分点,试错 10% 减后为 0,禁止开仓。破 MA5 降级、破 MA10 减半、破 MA20 只做中军累计生效。按编号决策树,Step5 止盈先于 Step6 均线减仓;清仓优先,减半信号不叠加。T+1/停牌/跌停受限会明确提示,清仓建议不等于实际卖出。' },
      { k: '留档', tone: 'what', t: '按当前登录用户、交易日和数据检查/预警/终判/周复核阶段保存独立卡片;保存后回读确认,版本冲突不会静默覆盖。交易默认未执行,用户手工记录成交不代表本系统发出订单。交易记录、次日预案、复盘支持保存、JSON 留档与响应式 A4 打印。' },
      { k: '数据与权限', tone: 'perm', t: `历史指数仍由 16:50「${taskName('dual_core')}」落库。刷新不代表接入实时;缺失或延迟 >2 分钟维持空仓建议,数据恢复不追补当日信号。登录用户只保存自己的卡片;页面无自动下单、无自动持仓变更。` },
    ],
  },
  {
    id: 'pool', title: '选股池', group: '选股工作台', to: '/pool',
    hint: '买哪只从这里选', badge: '第③步', badgeType: 'success',
    rows: [
      { k: '看什么', tone: 'what', t: '总选股池 Top30(评级/综合评分/入选价/命中理由/现价/今日涨跌幅)与赛道池、各股出池留档(含出池原因,可回溯)。' },
      { k: '怎么用', tone: 'how', t: '默认按评分排序,评分 ≥65 加粗提示;优先看评级「买入」且评分高的行,结合现价/涨跌幅判断是否已追高;命中理由标签超过 3 个悬停查看全部;弱市守门触发时主池暂停新增入选(存量继续按评级跟踪),横幅会明确提示,此时不要手动加仓。' },
      { k: '行情口径', tone: 'metric', t: '现价/今日涨跌幅优先取服务端实时行情(新浪批量报价);失败时降级为全市场快照缓存或已落库日线,并在页头标注来源。交易时段每 20 秒自动刷新;非交易时段显示最近收盘快照;源异常时明确显示「已落库收盘数据」。' },
      { k: '数据何时更新', tone: 'update', t: `交易日 17:05 ${taskName('strategy')} 任务筛选并入池出池;赛道池依赖赛道成分同步完成后才有数据。` },
      { k: '空数据怎么办', tone: 'empty', t: `总池为空先去任务中心运行 ${taskName('strategy')};赛道池若显示“赛道成分尚未同步”,点引导卡按钮跳到行业页赛道管理处同步成分;本地网络受限时同步可能降级(503),云端部署后自动恢复。` },
      { k: '权限', tone: 'perm', t: '所有登录用户可见;手动出池需策略员或管理员。' },
    ],
  },
  {
    id: 'industries', title: '行业与赛道', group: '选股工作台', to: '/industries',
    hint: '选赛道、供赛道池成分',
    rows: [
      { k: '看什么', tone: 'what', t: '顶部数据日与指标覆盖度;新兴赛道雷达(阶段/动量/拥挤度/新闻突增/雷达分)、行业景气榜(景气评分热力着色,回避名单标红)、赛道管理(成分/来源/同步时间/启用)。' },
      { k: '怎么用', tone: 'how', t: '雷达优先看“低位转强 > 强动量 > 观察”;SC-U08 拥挤行业自动垫底回避;发现好赛道点“加入赛道跟踪”,再到赛道管理里同步成分或点“一键回填”批量回填全部赛道成分,成分进赛道池后参与策略筛选。' },
      { k: '口径', tone: 'metric', t: '顶部状态条显示「行业数据日」与动量/拥挤度/资金/估值四项覆盖度;东财行业日线不可达时会降级为新浪行业快照,只有点位、涨跌与成交额,动量(需 22/62 个交易日)、拥挤度(需 60 日)、资金三类指标逐日积累,未就绪时表中显示「积累中 / -」而非 0。新闻按赛道/概念标签匹配,与行业分类名不同体系,无匹配即 0 条。' },
      { k: '数据何时更新', tone: 'update', t: `每日 16:15 ${taskName('industry_daily')} 定时任务生成行业截面(数据日早于最新交易日时页面会提示);赛道成分每周一 8:40 自动同步,也可随时手动点「同步成分」或「一键回填」。` },
      { k: '空数据怎么办', tone: 'empty', t: `雷达空先运行 ${taskName('industry_daily')};本地东财被墙时自动走同花顺名单核验→本地代表股/行业/关键词展开四级降级,赛道池不断流;来源列含义:东财/新浪/关键词/同花顺核验/本地。` },
      { k: '权限', tone: 'perm', t: '所有登录用户可见;采纳赛道/同步/新增需策略员或管理员,普通用户只读。' },
    ],
  },
  {
    id: 'signals', title: '技术信号', group: '选股工作台', to: '/signals',
    hint: '定买点,来自上一交易日', badge: '第④步', badgeType: 'success',
    rows: [
      { k: '看什么', tone: 'what', t: '底分型/连红/量能偏离/相对强弱等相对口径信号流(日期/代码/信号类型/强度/明细)。' },
      { k: '怎么用', tone: 'how', t: '顶部下拉按信号类型过滤(如只看 buy_setup 入场组合);强度列可排序,明细列写清连红天数、量能偏离、所属行业动量等依据。' },
      { k: '数据何时更新', tone: 'update', t: `交易日 16:40 ${taskName('signals')} 任务扫描生成;盘中看到的是上一交易日信号,切换类型即重新请求(服务端 limit=300)。` },
      { k: '空数据怎么办', tone: 'empty', t: `空表先去任务中心运行 ${taskName('signals')};周末与非交易日无新增信号属预期,看历史信号直接翻页即可。` },
      { k: '权限', tone: 'perm', t: '所有登录用户可见。' },
    ],
  },
  {
    id: 'news', title: '新闻甄别', group: '选股工作台', to: '/news',
    hint: '题材确认,不作入场依据',
    rows: [
      { k: '看什么', tone: 'what', t: '低频采集的新闻流(时间/标题/主题/关联赛道/情绪/热度/甄别),服务赛道确认,不作入场依据。' },
      { k: '怎么用', tone: 'how', t: '“仅甄别通过”开关过滤掉日内噪声,默认开启;关联赛道列把新闻挂到赛道,配合行业页雷达的“新闻近3日突增”一起看。' },
      { k: '数据何时更新', tone: 'update', t: `交易日 11:40 与 14:40 两次低频采集(${taskName('news')} 任务);切换开关即重新请求(服务端 limit=200)。` },
      { k: '空数据怎么办', tone: 'empty', t: `空表先去任务中心运行 ${taskName('news')};若某天确实无通过甄别的新闻,关掉开关确认是否连噪声也没有,再决定是否手动触发。` },
      { k: '权限', tone: 'perm', t: '所有登录用户可见。' },
    ],
  },
  {
    id: 'lhb', title: '龙虎榜', group: '选股工作台', to: '/lhb',
    hint: '游资情绪,仅参考',
    rows: [
      { k: '看什么', tone: 'what', t: '上榜个股(收盘/涨跌幅/龙虎净额/命中游资/交易日)与游资席位表(席位/游资/风格/风险)。「命中游资」仅展示真正的游资席位,机构专用与沪深股通(北向)已在接口层过滤,不计入标签。' },
      { k: '怎么用', tone: 'how', t: '先看龙虎净额与命中游资标签判断游资合力方向(↑=游资买入实心标签,↓=游资卖出描边标签;红/黄/灰=高/中/低风险),再对照席位表风格决定跟随意愿;仅作情绪参考,不直接构成买卖信号。' },
      { k: '数据何时更新', tone: 'update', t: `收盘后 17:10 ${taskName('lhb_sync')} 落库;席位表为静态配置,随版本更新。` },
      { k: '空数据怎么办', tone: 'empty', t: `17:10 前为空属正常,等流水线跑完;周末与非交易日无数据属预期;若交易日晚间仍空,去任务中心手动运行 ${taskName('lhb_sync')} 或看任务日志。` },
      { k: '权限', tone: 'perm', t: '所有登录用户可见。' },
    ],
  },
  {
    id: 'strategy', title: '策略配置', group: '选股工作台', to: '/strategy',
    hint: '阈值参数,改动影响下一轮筛选',
    rows: [
      { k: '看什么', tone: 'what', t: '全部策略阈值参数表(参数名/值/说明),后台可调,下一轮筛选生效。' },
      { k: '怎么用', tone: 'how', t: '只改你理解含义的参数,输入框失焦即做数值校验(非法输入会警告并拦截保存);改完点右上保存,不确定先截图记下原值。' },
      { k: '数据何时更新', tone: 'update', t: `保存即时写入,下一轮 17:05 ${taskName('strategy')} 任务按新阈值筛选;页面本身无定时刷新。` },
      { k: '空数据怎么办', tone: 'empty', t: '参数表正常不会为空;若加载失败刷新页面重试,仍失败检查网络或联系管理员看任务日志。' },
      { k: '权限', tone: 'perm', t: '策略员与管理员可改;普通用户只读(页面有只读提示)。' },
    ],
  },
  {
    id: 'cards', title: '策略卡', group: '选股工作台', to: '/cards',
    hint: '策略生命周期与证据链',
    rows: [
      { k: '看什么', tone: 'what', t: '策略生命周期卡(unverified → testing → verified / deprecated):假设、证据链、回测备注,证据链必填,风控与 alpha 分离,回测需随机对照组。' },
      { k: '怎么用', tone: 'how', t: '新想法先建卡写假设与证据再进 testing;验证通过才标 verified 进入生产筛选;废弃的标 deprecated 留档不删除,供回溯。' },
      { k: '数据何时更新', tone: 'update', t: '人工维护,无定时任务;状态变更即时保存。' },
      { k: '空数据怎么办', tone: 'empty', t: '卡为空说明尚未沉淀任何策略,找策略员按模板补卡;普通用户看不到新建入口属正常。' },
      { k: '权限', tone: 'perm', t: '所有登录用户可见;状态变更需策略员或管理员。' },
    ],
  },
  {
    id: 'tasks', title: '任务中心', group: '选股工作台', to: '/tasks',
    hint: '后台任务与日志',
    rows: [
      { k: '看什么', tone: 'what', t: '全部后台任务的运行日志(任务名/时间/状态/耗时/结果),日志保留最近200条,前端分页查看。' },
      { k: '怎么用', tone: 'how', t: `先看状态列定位失败或跳过,再点结果列悬停看详情;手动触发选任务后执行,后台可能耗时数分钟,页面会自动轮询(最长约3分钟)并提示最终结果;首次初始化建议顺序:${INIT_ORDER_TEXT}。` },
      { k: '数据何时更新', tone: 'update', t: '定时调度(盘中快照每30分钟;收盘流水线 15:35 起;龙虎榜 17:10;周报周五 17:45),非交易日自动跳过(状态记为「跳过」)。' },
      { k: '空数据怎么办', tone: 'empty', t: '日志为空说明后端尚未跑过任何任务,按初始化顺序手动触发;「跳过」属非交易日预期行为,不是故障。' },
      { k: '权限', tone: 'perm', t: '策略员与管理员可见可操作;普通用户无入口。' },
    ],
  },
  {
    id: 'portfolio', title: '我的持仓', group: '持仓工作台', to: '/portfolio',
    hint: '止损与仓位硬规则', badge: '第⑥步', badgeType: 'warning',
    rows: [
      { k: '看什么', tone: 'what', t: '个人持仓(买入价/现价/浮盈/保本价/止损价/阶段/期间最高/池评级/风险),G03 三阶段止损由代码硬规则强制执行。' },
      { k: '怎么用', tone: 'how', t: '点“录入持仓”填代码/买入价/数量/买入日期,系统自动算保本价与止损线(初始止损默认10%,会议口径最高点回撤10-15%必须卖出);阶段 A=初始止损禁上移、B=已上移至保本、C=创新高后动态跟踪,悬停阶段标签看解释;“已破止损”标红的优先处理;AI分析仅参考建议,不改变止损线。' },
      { k: '数据何时更新', tone: 'update', t: `现价与阶段由交易日 15:50 ${taskName('holding_patrol')} 巡检逐用户推进;录入/卖出即时写库。` },
      { k: '空数据怎么办', tone: 'empty', t: '空即无持仓,点右上“录入持仓”开始;若有持仓但现价为空,等收盘巡检或检查行情同步任务是否成功。' },
      { k: '权限', tone: 'perm', t: '所有登录用户可见,只能看到自己的持仓。' },
    ],
  },
  {
    id: 'watchlist', title: '自选预警', group: '持仓工作台', to: '/watchlist',
    hint: '价格提醒,不改你的止损',
    rows: [
      { k: '看什么', tone: 'what', t: '命中提醒(价格触及三价或涨跌幅阈值)与自选表(成本/止盈/止损/预警%/备注)。' },
      { k: '怎么用', tone: 'how', t: `点“新增自选”填 6 位代码与三价:成本价用于算盈亏参考,止盈/止损价是触发提醒的线,预警%是单日涨跌幅阈值;三者任一为 0 表示不设该项;命中提醒由盘中 ${taskName('watch_alert')} 任务产生,只读不改你的止损,仅提示。` },
      { k: '数据何时更新', tone: 'update', t: `命中提醒由盘中 ${taskName('watch_alert')} 快照任务(9:35 起每30分钟)扫描产生;自选表即时增删改。` },
      { k: '空数据怎么办', tone: 'empty', t: '自选空点右上新增;有自选但无提醒说明尚未触发阈值,或等下一个盘中扫描周期。' },
      { k: '权限', tone: 'perm', t: '所有登录用户可见,只能看到自己的自选。' },
    ],
  },
  {
    id: 'trades', title: '交易历史', group: '持仓工作台', to: '/trades',
    hint: '卖出后复盘',
    rows: [
      { k: '看什么', tone: 'what', t: '卖出后的复盘统计(交易数/胜率/平均盈亏/盈亏比/总盈亏,绝对收益口径)与卖出历史(含费盈亏、离场触发原因,可审计)。' },
      { k: '怎么用', tone: 'how', t: '风控框架只负责“不死”,alpha 需另行验证——先看胜率与盈亏比是否匹配你的策略假设,再逐笔核对触发原因(初始止损/保本离场/移动止损/手动/AI参考)。' },
      { k: '数据何时更新', tone: 'update', t: '持仓卖出登记后自动生成,无定时任务;统计口径为绝对收益。' },
      { k: '空数据怎么办', tone: 'empty', t: '空即尚未卖出过持仓,去持仓页卖出登记后自动出现;不是故障。' },
      { k: '权限', tone: 'perm', t: '所有登录用户可见,只能看到自己的交易。' },
    ],
  },
  {
    id: 'reports', title: '报告中心', group: '持仓工作台', to: '/reports',
    hint: '周报与持仓日报',
    rows: [
      { k: '看什么', tone: 'what', t: '左侧周报列表与日报入口,右侧报告正文(Markdown 渲染,内容已做 XSS 消毒)。' },
      { k: '怎么用', tone: 'how', t: '点左侧标题查看对应周报;“查看今日持仓日报”即时按当前持仓生成,不入库;周报右侧正文可直接复制分享。' },
      { k: '数据何时更新', tone: 'update', t: `周报每周五 17:45 ${taskName('weekly_reports')} 任务逐用户生成;日报每次点击实时生成。` },
      { k: '空数据怎么办', tone: 'empty', t: '无周报先点“生成本周报告”手动生成一张;周五收盘前为空属正常;日报无持仓时会提示先录入持仓。' },
      { k: '权限', tone: 'perm', t: '所有登录用户可见,只能看到自己的报告。' },
    ],
  },
  {
    id: 'settings', title: 'LLM 设置', group: '持仓工作台', to: '/settings',
    hint: '个人大模型配置',
    rows: [
      { k: '看什么', tone: 'what', t: '个人大模型配置(OpenAI 兼容协议:Base URL / API Key / 模型 / 温度 / 启用开关),密钥加密存储,页面只显示脱敏 key。' },
      { k: '怎么用', tone: 'how', t: '支持 DeepSeek / 通义 / Kimi / OpenAI / 本地 vLLM 等;填完先点“测试连通”再保存;本地 vLLM 调试可用 http://localhost 这类本机环回地址,远程地址必须 HTTPS;暂时不用可点“仅保存停用状态”,不校验 URL 与模型。' },
      { k: '数据何时更新', tone: 'update', t: '保存即时生效,持仓 AI 分析下次调用即用新配置;无定时任务。' },
      { k: '空数据怎么办', tone: 'empty', t: '未配置时 AI 功能不可用但规则功能不受影响;测试连通失败先检查 Base URL 与 key 是否填对、模型名是否存在。' },
      { k: '权限', tone: 'perm', t: '所有登录用户可见,只能改自己的配置。' },
    ],
  },
  {
    id: 'users', title: '用户与权限', group: '系统管理', to: '/users',
    hint: '账号、角色与邀请码',
    rows: [
      { k: '看什么', tone: 'what', t: '用户管理(用户名/昵称/角色/状态/持仓数/注册时间)与邀请码(一次性,凭码注册)。' },
      { k: '怎么用', tone: 'how', t: '角色分三档——admin 全部权限、analyst 可操作选股工作台、user 只读看板加自己持仓;改角色/启停即时生效(不能改自己);邀请码设有效天数(1-90 天)后生成发给新人,用过即失效,不用可作废。' },
      { k: '数据何时更新', tone: 'update', t: '增删改查即时生效,无定时任务。' },
      { k: '空数据怎么办', tone: 'empty', t: '用户列表正常不会为空;邀请码空点“生成邀请码”即可;搜不到人先清空搜索框再查。' },
      { k: '权限', tone: 'perm', t: '仅管理员可见,普通用户与策略员无入口(手输 URL 也会被路由守卫拦回仪表盘)。' },
    ],
  },
]

function hit(section, kw) {
  if (!kw) return true
  const text = [section.title, section.hint, section.badge || '',
    ...section.rows.map((r) => `${r.k}${r.t}`)].join(' ').toLowerCase()
  return text.includes(kw)
}

const filteredGroups = computed(() => {
  const kw = query.value.trim().toLowerCase()
  const groups = []
  for (const s of SECTIONS) {
    if (!hit(s, kw)) continue
    let g = groups.find((x) => x.name === s.group)
    if (!g) {
      g = { name: s.group, items: [] }
      groups.push(g)
    }
    g.items.push(s)
  }
  return groups
})

// 搜索时自动展开命中项,清空搜索时收起,避免用户反复点开
watch(query, () => {
  openIds.value = query.value.trim()
    ? filteredGroups.value.flatMap((g) => g.items.map((s) => s.id))
    : []
})

const matchedCount = computed(() => filteredGroups.value.reduce((n, g) => n + g.items.length, 0))
const allOpen = computed(() => matchedCount.value > 0 && openIds.value.length >= matchedCount.value)

function toggleAll() {
  openIds.value = allOpen.value
    ? []
    : filteredGroups.value.flatMap((g) => g.items.map((s) => s.id))
}
</script>

<style scoped>
.help-page :deep(.el-card__body) { padding-top: 14px; }

/* ---------- 顶部重点摘要 ---------- */
.hero {
  border: 1px solid #e3e8f0;
  border-left: 4px solid #409eff;
  background: #f7faff;
  border-radius: 6px;
  padding: 14px 16px;
  margin-bottom: 18px;
}
.hero-main h2 { margin: 0 0 6px; font-size: 17px; color: #1f2d3d; }
.hero-lead { margin: 0 0 10px; font-size: 14px; line-height: 1.75; color: #303133; }
.hero-link { color: #409eff; font-weight: 600; text-decoration: none; }
.hero-link:hover { text-decoration: underline; }
.hero-rules { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.rule { font-size: 12px; line-height: 1; padding: 6px 10px; border-radius: 4px; }
.rule-danger { color: #c03639; background: #fef0f0; border: 1px solid #fbc4c4; }
.rule-warn { color: #b88230; background: #fdf6ec; border: 1px solid #f5dab1; }
.rule-info { color: #5a6b7c; background: #f4f6f8; border: 1px solid #e0e5ea; }
.hero-foot { margin: 0; font-size: 12px; line-height: 1.7; color: #909399; }

/* ---------- 区块标题 ---------- */
.block { margin-bottom: 20px; }
.block-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
.block-head .grow { flex: 1; }
.block-title { font-size: 15px; font-weight: 700; color: #1f2d3d; }
.block-title::before {
  content: ''; display: inline-block; width: 3px; height: 14px; margin-right: 8px;
  background: #409eff; border-radius: 2px; vertical-align: -2px;
}
.block-sub { font-size: 12px; color: #909399; }

/* ---------- 每日流程时间线 ---------- */
.flow { list-style: none; margin: 0; padding: 0; }
.flow-step { display: flex; gap: 12px; padding: 10px 0; border-bottom: 1px dashed #ebeef5; }
.flow-step:last-child { border-bottom: none; }
.flow-no {
  flex: 0 0 26px; height: 26px; line-height: 26px; text-align: center;
  border-radius: 50%; background: #409eff; color: #fff; font-size: 13px; font-weight: 700;
}
.flow-body { flex: 1; min-width: 0; }
.flow-top { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 2px; }
.flow-time {
  font-size: 12px; font-weight: 700; color: #409eff; background: #ecf5ff;
  border-radius: 3px; padding: 2px 6px; font-variant-numeric: tabular-nums;
}
.flow-module { font-size: 13px; font-weight: 600; color: #303133; text-decoration: none; }
.flow-module:hover { color: #409eff; text-decoration: underline; }
.flow-act { font-size: 12px; color: #909399; }
.flow-act::before { content: '· '; }
.flow-detail { margin: 0; font-size: 13px; line-height: 1.7; color: #606266; }

/* ---------- 模块分组 ---------- */
.panel-group + .panel-group { margin-top: 14px; }
.panel-group-head {
  display: flex; align-items: baseline; gap: 8px;
  padding: 6px 0 4px; border-bottom: 1px solid #ebeef5; margin-bottom: 2px;
}
.panel-group-name { font-size: 13px; font-weight: 700; color: #1f2d3d; }
.panel-group-name::before {
  content: ''; display: inline-block; width: 6px; height: 6px; margin-right: 6px;
  border-radius: 50%; background: #c6d7ea; vertical-align: 2px;
}
.panel-group :deep(.el-collapse) { border-top: none; border-bottom: none; }
.panel-group :deep(.el-collapse-item__header) { padding: 0 6px; height: 44px; }
.panel-group :deep(.el-collapse-item__content) { padding: 4px 6px 14px; }
.sec-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.sec-name { font-size: 13px; font-weight: 600; color: #303133; flex: 0 0 auto; }
.sec-hint { font-size: 12px; color: #909399; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---------- 结构化行:标签 + 正文 ---------- */
.row { display: flex; gap: 10px; padding: 7px 0; border-top: 1px dashed #f0f2f5; }
.row:first-child { border-top: none; padding-top: 2px; }
.row-label {
  flex: 0 0 84px; font-size: 12px; font-weight: 600; line-height: 20px;
  text-align: center; border-radius: 3px; height: 20px;
}
.tone-what { color: #1f6fd0; background: #ecf5ff; }
.tone-how { color: #2f9e63; background: #eefaf3; }
.tone-update { color: #b88230; background: #fdf6ec; }
.tone-empty { color: #c03639; background: #fef0f0; }
.tone-perm { color: #6b7480; background: #f4f6f8; }
.tone-metric { color: #6b4fbb; background: #f3f0fd; }
.tone-danger { color: #c03639; background: #fef0f0; }
.row-text { flex: 1; min-width: 0; margin: 0; font-size: 13px; line-height: 1.75; color: #606266; }
.sec-foot { margin-top: 8px; }
.sec-jump { font-size: 12px; color: #409eff; text-decoration: none; }
.sec-jump:hover { text-decoration: underline; }

@media (max-width: 768px) {
  .hero { padding: 12px; }
  .hero-main h2 { font-size: 16px; }
  .row { flex-direction: column; gap: 4px; }
  .row-label { flex: none; align-self: flex-start; padding: 0 8px; }
  .sec-hint { display: none; }
  .flow-step { gap: 8px; }
  .block-sub { display: none; }
}
</style>
