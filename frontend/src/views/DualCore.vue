<template>
  <div class="dual-core-v2">
    <header class="dc-head">
      <div class="dc-head-main">
        <h1>{{ phaseTitle }}</h1>
        <p class="muted">v2.0 · 未回测 / 未核验（unverified）· 只给建议与记录，不会自动下单；14:30 仅预警。</p>
      </div>
      <span class="dc-tag" :class="summary.canOpen ? 'is-success' : 'is-danger'">
        {{ summary.canOpen ? '可开仓（仅建议）' : '不开仓 · 仅建议' }}
      </span>
    </header>

    <div class="dc-tools no-print">
      <div class="dc-tools-row">
        <label class="dc-field">交易日期
          <input v-model="tradeDate" type="date" :disabled="busy" @change="onDateChange" />
        </label>
        <label class="dc-field">卡片阶段
          <select v-model="phase" :disabled="busy" @change="onPhaseChange">
            <option v-for="p in phases" :key="p[0]" :value="p[0]">{{ p[1] }}</option>
          </select>
        </label>
        <span class="grow" />
        <button class="el-button el-button--small el-button--primary" :disabled="busy" @click="refresh">刷新自动数据</button>
        <button class="el-button el-button--small" :disabled="busy" @click="evaluateManual">校验手工证据（非实时）</button>
        <button class="el-button el-button--small el-button--primary dc-save-btn" :disabled="busy" @click="save">保存执行卡</button>
      </div>
      <div class="dc-tools-row is-sub">
        <span class="dc-tools-label">留档 / 打印</span>
        <button class="el-button el-button--small" @click="printCard">打印 A4</button>
        <button class="el-button el-button--small" @click="exportDraft">导出当前草稿</button>
        <button class="el-button el-button--small" :disabled="!card.revision" @click="loadAudit">修订历史</button>
        <button class="el-button el-button--small" :disabled="!card.revision" @click="exportCard">导出留档</button>
      </div>
    </div>

    <p class="dc-save" aria-live="polite">{{ saveHint }} · 修订 {{ card.revision }} · {{ phaseLabel }} · 日历状态未核验</p>
    <p v-if="!isToday" class="dc-alert is-info">
      <strong>所选日期不是今天。</strong>自动检查只代表当前时点，不能作为该历史日期的实时证据；手工校验仍只做记录，不授权开仓。
    </p>
    <p v-if="isToday && autoResult?.feed_status === 'closed'" class="dc-alert is-info">
      <strong>非交易时段。</strong>当前显示 {{ autoResult.feed_trade_date || '最近交易日' }} 收盘快照（非实时），仅供复核，不授权开仓；下一交易时段 09:30 后恢复实时采集。
    </p>
    <p v-if="error" class="dc-alert is-error" role="alert">{{ error }}</p>

    <p class="dc-guide-now no-print">
      <strong>现在该做什么：</strong>{{ nextStep }}
      <a class="dc-guide-link" href="#dc-blockers" @click.prevent="jump('dc-blockers')">查看缺失项</a>
      <a class="dc-guide-link" href="#dc-step-6" @click.prevent="jump('dc-step-6')">填手工台账</a>
    </p>

    <section class="dc-dashboard" aria-label="终判状态">
      <article>
        <small>终判状态</small>
        <h2>{{ summary.state }}</h2>
        <p>{{ reasonText(result?.decision?.reason) || '自动证据不完整，不能由手工勾选补成通过。' }}</p>
      </article>
      <article>
        <small>风险与仓位</small>
        <h2>{{ summary.cap }}% 上限</h2>
        <p>策略未经回测，总仓永久防线 ≤50%；不是目标仓位。</p>
      </article>
      <article>
        <small>数据新鲜度</small>
        <h2>{{ freshnessText }}</h2>
        <p>{{ resultIsManual ? '校验时间' : '计算时间' }} {{ fmtTime(result?.as_of) }}<br>历史数据日 {{ historical?.history_as_of || '未知' }}<template v-if="result?.feed_trade_date"><br>收盘快照日 {{ result.feed_trade_date }}（非实时）</template></p>
      </article>
    </section>

    <section id="dc-blockers" class="dc-gap" role="status" aria-label="缺失与阻断原因">
      <div class="dc-gap-head">
        <strong>缺失 / 阻断原因</strong>
        <span class="dc-tag is-warning">{{ blockerRows.length }} 类 · {{ rawMissing.length }} 项</span>
        <span class="dc-tag" :class="hasServerGap ? 'is-danger' : 'is-info'">{{ hasServerGap ? '服务端存在缺口' : '仅手工台账缺口' }}</span>
      </div>
      <p class="dc-gap-lead">{{ blockerLead }}</p>
      <div class="dc-scroll">
        <table class="dc-gap-table">
          <thead><tr><th>数据域</th><th>缺什么</th><th>为什么缺</th><th>影响</th><th>怎么处理</th></tr></thead>
          <tbody>
            <tr v-for="row in blockerRows" :key="row.key" :class="row.manual ? 'is-manual' : 'is-server'">
              <td data-label="数据域">
                <b>{{ row.domain }}</b>
                <span class="dc-gap-owner" :class="row.manual ? 'is-manual' : 'is-server'">{{ row.manual ? '手工可填 · 不授权' : '服务端数据链' }}</span>
              </td>
              <td data-label="缺什么">{{ row.missing }}</td>
              <td data-label="为什么缺">{{ row.cause }}</td>
              <td data-label="影响">{{ row.impact }}</td>
              <td data-label="怎么处理">
                <a v-if="row.link" class="dc-inline-link" :href="`#${row.link}`" @click.prevent="jump(row.link)">{{ row.action }}</a>
                <span v-else>{{ row.action }}</span>
              </td>
            </tr>
            <tr v-if="!blockerRows.length"><td colspan="5" class="muted">未发现数据缺口；仍以实际检查结果为准。</td></tr>
          </tbody>
        </table>
      </div>
      <p class="dc-gap-note"><strong>判定结论：</strong>{{ blockerConclusion }}</p>
      <details class="dc-gap-raw no-print">
        <summary>查看原始缺失键（排查用）</summary>
        <p>{{ rawMissing.join('、') || '无' }}</p>
      </details>
      <p class="dc-gap-risk">空仓为风险建议，不代表已卖出；T+1、停牌、跌停无法成交时保留实际占用仓位并制定次日预案。数据恢复不得追补当日信号。</p>
    </section>

    <section class="dc-guide no-print" aria-label="使用说明">
      <div class="dc-guide-head">
        <h2>怎么用这张卡</h2>
        <span class="dc-tag is-info">先看结论，再填台账</span>
      </div>
      <div class="dc-guide-flow">
        <div class="dc-guide-step">
          <span>1</span>
          <div><b>刷新自动数据</b><p>点顶部“刷新自动数据”；服务端在交易时段采集双核实时快照、情绪池和交易日历，采集失败或证据超过 120 秒会按缺失阻断。非交易时段显示最近收盘快照（仅复核，不授权开仓）。核心 / 情绪缺口不能靠手填补成通过；历史日线可在 <a class="dc-inline-link" href="#/tasks">任务中心</a> 查看。</p></div>
        </div>
        <div class="dc-guide-step">
          <span>2</span>
          <div><b>看终判和缺口</b><p>先看上面三块状态和“缺失 / 阻断原因”；缺失时一律按空仓，不要手工补通过。</p></div>
        </div>
        <div class="dc-guide-step">
          <span>3</span>
          <div><b>需要留档再填</b><p>Step 6–11 和“手工证据（进阶）”都是台账；unknown 不等于否，不填也能看自动结论。</p></div>
        </div>
        <div class="dc-guide-step">
          <span>4</span>
          <div><b>保存或打印</b><p>“保存执行卡”只保存留档，不会下单；需要时打印 A4 或导出 JSON。</p></div>
        </div>
      </div>
    </section>

    <ul v-if="audit.length" class="dc-audit no-print">
      <li v-for="item in audit" :key="item.revision">
        <span class="dc-tag is-info">修订 {{ item.revision }}</span>
        {{ item.action }} · {{ item.recorded_at }}
      </li>
    </ul>

    <section id="dc-manual" class="dc-section dc-advanced" aria-label="手工数字证据">
      <details class="dc-advanced-box">
        <summary>
          <span class="dc-advanced-title">手工证据（进阶，可选）</span>
          <span class="dc-tag is-warning">unverified · 不授权</span>
          <span class="dc-advanced-hint">只有做手工复核或完整留档时才需要展开</span>
        </summary>
        <p class="dc-help">以下字段与引擎同名；金额须使用一致单位。unknown 不等于否。时间为用户记录，不证明实时来源；不填写则保持缺失。全部输入永不授权开仓，也不会把自动缺失补成通过。</p>
        <label class="dc-field dc-field-wide">用户记录时间（含时区 ISO）
          <input v-model="card.evidence.captured_at" placeholder="2026-10-28T14:45:00+08:00" />
        </label>
        <details v-for="code in ['883957','883994']" :key="code" class="dc-details">
          <summary>{{ code }} 核心数字（手工）</summary>
          <DualCoreEvidenceFields :model="card.evidence.indices[code]" kind="core" :prefix="`core.${code}`" />
          <div v-if="code==='883994'" class="dc-macd">
            <p class="dc-help">周 MACD 柱（从旧到新，至少 3 个；任一项 unknown 视为缺失）</p>
            <div class="dc-macd-grid">
              <label v-for="(_,i) in card.evidence.indices[code].macd_hist" :key="i" class="dc-field">
                第 {{ i + 1 }} 根{{ i === 0 ? '（旧）' : i === card.evidence.indices[code].macd_hist.length - 1 ? '（新）' : '' }}
                <input v-model.number="card.evidence.indices[code].macd_hist[i]" type="number" step="any" />
              </label>
            </div>
          </div>
        </details>
        <details class="dc-details">
          <summary>行业数字与风险（手工）</summary>
          <DualCoreEvidenceFields :model="card.evidence.industry" kind="industry" prefix="industry" />
        </details>
      </details>
    </section>

    <section v-if="reference" class="dc-section" aria-label="手工计算参考">
      <div class="dc-section-head">
        <h2>手工计算参考</h2>
        <span class="dc-tag is-warning">unverified · 不可授权开仓</span>
      </div>
      <p class="dc-help">来源：{{ referenceSource }}。顶层仍按实时缺失阻断；下列是规则推导，不是已核验行情，不是买卖指令。</p>
      <p class="dc-budget">{{ reference.status }} · 参考上限 {{ reference.verdict?.cap }}% · {{ reference.decision?.reason }} · can_open=false</p>
      <div class="dc-scroll">
        <table>
          <thead><tr><th>规则</th><th>输入</th><th>阈值</th><th>参考结果</th></tr></thead>
          <tbody>
            <tr v-for="(c,i) in reference.checks" :key="i">
              <td>{{ checkKeyText(c.key) }}</td>
              <td>{{ display(c.actual) }}</td>
              <td>{{ checkThresholdText(c.threshold) }}</td>
              <td>{{ passText(c.pass) }} · {{ c.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>持仓风险建议（均未执行）：{{ display(reference.decision?.orders) }}</p>
    </section>

    <section v-if="candidateResults.length" class="dc-section" aria-label="候选逐只校验">
      <div class="dc-section-head">
        <h2>候选逐只校验</h2>
        <span class="dc-tag is-info">{{ candidateResults.length }} 只 · 未执行</span>
      </div>
      <p class="dc-help">手工候选会逐只套用同一规则；任何 unknown 或失败都不准入。</p>
      <div class="dc-scroll">
        <table>
          <thead><tr><th>代码</th><th>参考上限</th><th>决策</th><th>原因</th><th>缺失项</th></tr></thead>
          <tbody>
            <tr v-for="(item,i) in candidateResults" :key="i">
              <td>{{ item.code || '—' }}</td>
              <td>{{ item.evidence_analysis?.verdict?.cap ?? item.verdict?.cap ?? 'unknown' }}%</td>
              <td>Step {{ item.decision?.step }} · {{ item.decision?.action || 'block' }}</td>
              <td>{{ item.decision?.reason || '缺失阻断' }}</td>
              <td>{{ (item.source_missing || []).length }} 项</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <nav class="dc-step-nav no-print" aria-label="步骤导航">
      <span class="dc-nav-group">自动判定</span>
      <a v-for="(title,i) in stepTitles.slice(0,6)" :key="i" :href="`#dc-step-${i}`" @click.prevent="jump(`dc-step-${i}`)">{{ i }} {{ title }}</a>
      <span class="dc-nav-group">手工台账</span>
      <a v-for="(title,i) in stepTitles.slice(6)" :key="i+6" :href="`#dc-step-${i+6}`" @click.prevent="jump(`dc-step-${i+6}`)">{{ i+6 }} {{ title }}</a>
    </nav>

    <section id="dc-step-0" class="dc-section">
      <div class="dc-section-head">
        <h2>Step 0 · 数据完整性</h2>
        <span class="dc-tag" :class="resultIsManual ? 'is-warning' : 'is-info'">{{ resultIsManual ? '手工校验参考' : '自动检查' }}</span>
      </div>
      <p class="dc-help">09:30 / 14:45 各查一次。双指数、情绪五项、行业和个股缺一或延迟＞2分钟，不通过，停止后续开仓判断。</p>
      <div class="dc-scroll">
        <table class="dc-check-table">
          <thead><tr><th>检查</th><th>实际值</th><th>阈值</th><th>来源 / 时间</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="(c,i) in autoChecks" :key="i">
              <td>{{ checkKeyText(c.key) }}</td>
              <td>
                <template v-if="isStringList(c.actual)">
                  <span v-for="x in c.actual" :key="x" class="dc-miss">{{ missingText(x) }}</span>
                </template>
                <template v-else>{{ display(c.actual) }}</template>
              </td>
              <td>{{ checkThresholdText(c.threshold) }}</td>
              <td>{{ c.source || '自动接口 · 来源未提供' }}<br>{{ fmtTime(c.timestamp) }}</td>
              <td :class="c.pass === true ? 'dc-pass':'dc-fail'">{{ passText(c.pass) }}<br>{{ reasonText(c.reason) }}</td>
            </tr>
            <tr v-if="!autoChecks.length"><td colspan="5">unknown · 尚无检查结果，按不通过处理</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section id="dc-step-1" class="dc-section">
      <h2>Step 1 · 周线一票否决 <span class="dc-kind">自动判定 · 只读</span></h2>
      <p class="dc-help">883957 破5周线（无缓冲）/ 周跌幅≥8% / 跌停≥50家，任一成立 → 空仓。883994 破5周线或高位巨阴 → 降两级，禁止≥3连板。</p>
      <div class="dc-grid">
        <article v-for="code in ['883957','883994']" :key="code">
          <h3>{{ code }} · 历史参考，非14:45现价</h3>
          <dl>
            <template v-for="(label,key) in coreFields" :key="key">
              <dt>{{ label }}</dt><dd class="num">{{ display(historical?.data?.[code]?.[key]) }}</dd>
            </template>
          </dl>
        </article>
      </div>
      <p class="dc-help">周中允许未完成周K；周五15:00后复核，不一致自下一交易日起生效。手工周线记录见下方证据区，不得替代自动输入。</p>
    </section>

    <section id="dc-step-2" class="dc-section">
      <h2>Step 2 · 情绪面板 <span class="dc-kind">自动判定 + 手工对照</span></h2>
      <p class="dc-help">4–5分正常；3分降一级；≤2分降两级（最低试错）；≤1分只减不加。缺失不是0分，是数据阻断。</p>
      <p class="dc-budget">
        手工口径评分：{{ emotionScore === null ? '未填齐（unknown）' : `${emotionScore} / 5` }}
        <span class="muted">· 仅用于对照，不参与实时授权</span>
      </p>
      <div class="dc-scroll">
        <table>
          <thead><tr><th>指标</th><th>达标阈值</th><th>手工实测</th></tr></thead>
          <tbody>
            <tr v-for="e in emotions" :key="e.key">
              <td>{{ e.label }}</td>
              <td>{{ e.rule }}</td>
              <td>
                <input v-model.number="card.evidence.emotion[e.key]" type="number" placeholder="未填" aria-label="手工情绪实测" />
                <small class="muted">手工 · 不参与实时授权</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section id="dc-step-3" class="dc-section">
      <h2>Step 3 · 日线发令枪 <span class="dc-kind">自动判定 · 只读</span></h2>
      <div class="dc-grid">
        <article><h3>A · 883957 ≥ 昨高 ×1.003</h3><p>自动实际 {{ checkValue('gun_a','actual') }} / 阈值 {{ checkValue('gun_a','threshold') }}</p></article>
        <article><h3>B · 883994 ≥ 近60交易日最高 ×1.005</h3><p>自动实际 {{ checkValue('gun_b','actual') }} / 阈值 {{ checkValue('gun_b','threshold') }}</p></article>
      </div>
      <p class="dc-help">必须 A 且 B；60日最高使用盘中最高价口径，不是全历史最高。14:30曾达标但14:45回落视为未触发。</p>
    </section>

    <section id="dc-step-4" class="dc-section">
      <h2>Step 4 · 均线阶梯，累计叠加 <span class="dc-kind">自动判定 · 只读</span></h2>
      <ol>
        <li>883957 &lt; MA5×0.997 → 降一级</li>
        <li>883957 &lt; MA10×0.997 → 在降级基础上仓位减半</li>
        <li>883957 &lt; MA20×0.997 → 只做中军，同时禁止扫板和≥3连板</li>
        <li>883994 &lt; MA5×0.997 → 只能试错≤10%（仅开仓豁免）</li>
        <li>双指数破MA5且连续2日反抽未收回 → 空仓</li>
      </ol>
      <p class="dc-budget">阶梯结论：{{ result?.position?.only_core ? '只做中军' : '见自动检查（缺失不推断正常）' }}；持仓管理不享有5日线豁免。</p>
    </section>

    <section id="dc-step-5" class="dc-section">
      <h2>Step 5 · 顶背离 <span class="dc-kind">自动判定 · 只读</span></h2>
      <p class="dc-help">883994 创60日新高，同时周MACD红柱连续2周缩短或DIF下穿DEA：只做首板/中军，禁止≥3连板，总仓≤50%、单票≤30%。优先于“重仓出击”。</p>
      <p class="dc-budget" :class="divergence === true ? 'is-fail' : divergence === false ? 'is-pass' : ''">背离结论（{{ divergenceSource }}）：{{ divergenceText }}</p>
    </section>

    <section id="dc-step-6" class="dc-section">
      <h2>Step 6 · 账户与仓位预算 <span class="dc-kind is-manual">手工输入 · 不授权</span></h2>
      <p class="dc-help">只在你需要保存完整执行卡时填写；不填不影响上面的自动结论。账户风险字段全部为手工台账，unknown 不能视为 0；全部填写后手工参考才可计算，仍不授权开仓。</p>
      <DualCoreEvidenceFields :model="card.account" kind="account" prefix="account" />
      <label class="dc-check">
        <input v-model="card.checklist.has_new_entry" type="checkbox" />今日计划新开仓（未执行）
      </label>
      <p class="dc-budget">
        自动档位上限 {{ summary.cap }}% → {{ card.checklist.has_new_entry ? '新仓减20个百分点' : '不扣新仓缓冲' }} → 最终预算 {{ budget }}%。
        {{ budget === 0 ? '预算为0，禁止新开仓。' : '仍须满足所有过滤及风险约束。' }}
      </p>
      <p class="dc-help">试错10%−20个百分点＝0，不是8%。连亏2笔下一笔再五折；连亏3笔停2交易日；当日亏损≥1.5%禁开仓、≥2%清仓建议；周亏≥5%下周限震荡；连续2月胜率＜35%停实盘回测。</p>
      <details class="dc-details">
        <summary>单票 / 持仓数联动表</summary>
        <p>进攻2只35–40% / 3只25–33% / 4只20–25% / 5只14–20%；震荡1只25–30% / 2只15–25% / 3只10–17%；试错1只5–10%。常规单票≤35%，仅龙一三周期全共振进攻可到40%；同时不得超过总仓。最多5只。</p>
        <p>加仓必须三周期共振、浮盈≥3%、未触单票上限；亏损股不得摊平。unverified 上限50%仍约束进攻表。</p>
      </details>
    </section>

    <section id="dc-step-7" class="dc-section">
      <h2>Step 7 · 行业与个股过滤 <span class="dc-kind is-manual">手工候选 · 不授权</span></h2>
      <p class="dc-help">没有候选可跳过本节；有候选时点“添加手工候选”逐只录入，unknown / 失败均不准入。手工备注不是自动通过。行业候选参考：{{ (historical?.industry_top || []).map(x=>x.name).join('、') || '无数据' }}</p>
      <div class="dc-grid">
        <article v-for="group in filterGroups" :key="group.title">
          <h3>{{ group.title }}</h3>
          <ul><li v-for="text in group.items" :key="text">{{ text }}</li></ul>
        </article>
      </div>
      <div class="dc-actions no-print">
        <button class="el-button el-button--small" @click="addCandidate">添加手工候选</button>
      </div>
      <article v-for="(candidate,i) in card.candidates" :key="i" class="dc-edit-card">
        <div class="dc-edit-head">
          <input v-model="candidate.code" placeholder="代码" aria-label="候选代码" />
          <input v-model="candidate.industry" placeholder="二级行业" aria-label="候选行业" />
          <input v-model="candidate.note" placeholder="过滤证据 / 未确认项" aria-label="候选备注" />
          <button class="el-button el-button--small el-button--danger is-plain no-print" @click="card.candidates.splice(i,1)">移除</button>
        </div>
        <p class="dc-help">unknown · 不准入（手工记录，不形成买入指令）</p>
        <details class="dc-details">
          <summary>完整证据字段（{{ fields('stock').length + fields('stock','boolean').length }} 项）</summary>
          <DualCoreEvidenceFields :model="candidate" kind="stock" :prefix="`candidate.${i}`" />
        </details>
      </article>
    </section>

    <section id="dc-step-8" class="dc-section">
      <h2>Step 8 · 风控与持仓建议（未执行） <span class="dc-kind is-manual">手工持仓 · 不执行</span></h2>
      <p class="dc-help">没有持仓可跳过本节；有持仓时录入代码、仓位和风险字段，仅生成风险处置建议，不更新实际账户。</p>
      <p class="dc-alert is-info">文档冲突裁决：按策略§3.2编号树，Step5止盈先于Step6均线减仓，不按末尾宣誓顺序重排。Step6与Step7取较大减幅、不叠加；清仓永远覆盖减仓。</p>
      <ol start="0"><li v-for="text in decisionTree" :key="text">{{ text }}</li></ol>
      <p class="dc-budget">当前建议：{{ result?.decision?.action || 'block' }} · {{ result?.decision?.reason || '缺失阻断，仅做风险核对' }} · 未执行</p>
      <div class="dc-actions no-print">
        <button class="el-button el-button--small" @click="addHolding">添加手工持仓</button>
      </div>
      <article v-for="(holding,i) in card.holdings" :key="i" class="dc-edit-card">
        <div class="dc-edit-head">
          <input v-model="holding.code" placeholder="持仓代码" aria-label="持仓代码" />
          <input v-model.number="holding.weight" type="number" placeholder="仓位%" aria-label="持仓仓位" />
          <button class="el-button el-button--small el-button--danger is-plain no-print" @click="card.holdings.splice(i,1)">移除</button>
        </div>
        <p class="dc-help">
          {{ holding.bought_today ? 'T+1受限，当日不可卖，最早次日执行止损。' : '可卖数量与成交能力尚未核验。' }}
          {{ holding.limit_down_locked ? '卖出受限，仓位仍计占用，禁止新增。' : '' }}
          风险处置建议未执行，不更新实际账户。
        </p>
        <input v-model="holding.plan" placeholder="风险处理 / 次日跳空预案（手工）" aria-label="持仓风险预案" />
        <details class="dc-details">
          <summary>完整证据字段（{{ fields('holding').length + fields('holding','boolean').length }} 项）</summary>
          <DualCoreEvidenceFields :model="holding" kind="holding" :prefix="`holding.${i}`" />
        </details>
      </article>
    </section>

    <section id="dc-step-9" class="dc-section">
      <h2>Step 9 · 交易记录 <span class="dc-kind is-record">手工事实 · 不下单</span></h2>
      <p class="dc-help">本页不提交订单。仅用户自行录入成交事实，保存记录不代表成交；默认“未执行”。</p>
      <div class="dc-actions no-print">
        <button class="el-button el-button--small" @click="addTrade">添加手工记录</button>
      </div>
      <div class="dc-scroll">
        <table>
          <thead><tr><th>时间</th><th>代码</th><th>方向</th><th>价格</th><th>股数</th><th>账户%</th><th>状态 / 理由</th></tr></thead>
          <tbody>
            <tr v-for="(trade,i) in card.trades" :key="i">
              <td><input v-model="trade.time" type="time" aria-label="交易时间" /></td>
              <td><input v-model="trade.code" aria-label="交易代码" /></td>
              <td>
                <select v-model="trade.direction" aria-label="交易方向">
                  <option>买入</option><option>卖出</option>
                </select>
              </td>
              <td><input v-model.number="trade.price" type="number" aria-label="成交价格" /></td>
              <td><input v-model.number="trade.shares" type="number" aria-label="成交股数" /></td>
              <td><input v-model.number="trade.weight" type="number" aria-label="成交仓位" /></td>
              <td>
                <select v-model="trade.status" aria-label="成交状态">
                  <option>未执行</option><option>用户记录已成交</option><option>委托未成交</option><option>撤单</option>
                </select>
                <input v-model="trade.reason" placeholder="手工事实 / 理由" aria-label="交易理由" />
                <button class="el-button el-button--small el-button--danger is-plain no-print" @click="card.trades.splice(i,1)">删除</button>
              </td>
            </tr>
            <tr v-if="!card.trades.length"><td colspan="7" class="muted">尚未录入手工交易记录</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section id="dc-step-10" class="dc-section">
      <h2>Step 10 · 次日预案 <span class="dc-kind is-record">盘后留档</span></h2>
      <p class="dc-help">跳空≤2%且未破止损按14:45常规判断；＞2%且破止损考虑9:25集合竞价卖出；≥5%开盘处置，跌停无法成交则持续挂单，不摊平。均为预案，未执行。</p>
      <div class="dc-form-grid">
        <label v-for="(label,key) in planFields" :key="key" class="dc-field-block">{{ label }}<textarea v-model="card.next_day_plan[key]" rows="2" /></label>
      </div>
    </section>

    <section id="dc-step-11" class="dc-section">
      <h2>Step 11 · 盘后复盘 <span class="dc-kind is-record">盘后留档</span></h2>
      <div class="dc-form-grid">
        <label v-for="(label,key) in reviewFields" :key="key" class="dc-field-block">{{ label }}<textarea v-model="card.review[key]" rows="3" /></label>
      </div>
      <details class="dc-details no-print">
        <summary>补充手工证据（永不伪装实时）</summary>
        <label class="dc-field-block">指数 / 周线核对笔记<textarea v-model="card.evidence.indices.notes" rows="3" /></label>
        <p class="dc-help">所有浏览器输入存为 manual。自动项缺失仍为不通过。保存按登录用户、交易日、阶段隔离；版本冲突不会静默覆盖。</p>
      </details>
    </section>

    <footer>
      <p>策略v2.0 初始假设，未经回测。先保护账户，再谈信号。已填写 {{ card.trades.length }} 条手工交易记录；当前页面无自动交易能力。</p>
    </footer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import api from '../api'
import DualCoreEvidenceFields from './DualCoreEvidenceFields.vue'
import { createCard, emptyEvidence, buildManualPayload, newEntryCap, safeSummary, toCardBody, fields } from '../utils/dualCoreCard.mjs'

const phases = [['data_check','09:30 数据检查'],['preview','14:30 预警（非执行）'],['final','14:45 终判'],['weekly_review','周五收盘复核']]
const phaseTitles = { data_check:'09:30 数据检查卡', preview:'14:30 预警卡（非执行）', final:'14:45 终判执行卡', weekly_review:'周五收盘复核卡' }
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit' }).format(new Date())
const tradeDate = ref(today())
const phase = ref('final')
const card = ref(createCard())
const audit = ref([])
const historical = ref(null)
const autoResult = ref(null)
const manualResult = ref(null)
const busy = ref(false)
const error = ref('')
const saveHint = ref('尚未保存')

const result = computed(() => manualResult.value || autoResult.value)
const resultIsManual = computed(() => Boolean(manualResult.value))
const isToday = computed(() => tradeDate.value === today())
const phaseLabel = computed(() => phases.find(p => p[0] === phase.value)?.[1])
const phaseTitle = computed(() => phaseTitles[phase.value] || '双核共振执行卡')
const summary = computed(() => safeSummary(result.value))
const rawMissing = computed(() => [...new Set(summary.value.missing || [])])
const hasServerGap = computed(() => rawMissing.value.some(key => {
  const text = String(key)
  return text.startsWith('cores.') || text === 'emotion' || text.startsWith('emotion.')
}))
const reference = computed(() => manualResult.value?.evidence_analysis || card.value.calculation?.evidence_analysis)
const referenceSource = computed(() => manualResult.value?.evidence_analysis
  ? '本次手工校验（未保存、未授权）'
  : card.value.calculation?.evidence_analysis ? `已保存修订 ${card.value.revision} 的计算快照` : '')
const candidateResults = computed(() => result.value?.candidate_results || [])
const budget = computed(() => newEntryCap(summary.value.cap, card.value.checklist.has_new_entry))
const autoChecks = computed(() => result.value?.checks || [])
function asDivergence(value) {
  if (typeof value === 'boolean') return value
  // 自动接口沿用历史参考口径：{top,bottom,dimension_down,notes}，只取顶背离。
  if (value && typeof value === 'object' && typeof value.top === 'boolean') return value.top
  return undefined
}
const divergenceManual = computed(() => asDivergence(manualResult.value?.evidence_analysis?.divergence))
const divergenceAuto = computed(() => asDivergence(autoResult.value?.divergence))
const divergenceSaved = computed(() => asDivergence(card.value.calculation?.evidence_analysis?.divergence))
const divergence = computed(() => divergenceManual.value ?? divergenceAuto.value ?? divergenceSaved.value)
const divergenceSource = computed(() => {
  if (divergenceManual.value !== undefined) return '手工校验参考 · 未授权'
  if (divergenceAuto.value !== undefined) {
    if (autoResult.value?.feed_status === 'closed') return '自动接口 · 周线 MACD（收盘快照）'
    if (autoResult.value?.feed_status === 'unavailable') return '自动接口 · 日线历史参考'
    return '自动接口 · 周线 MACD（实时快照）'
  }
  if (autoResult.value) return '自动接口 · 结论缺失'
  if (divergenceSaved.value !== undefined) return '已保存修订快照'
  return '未收到可验证输入'
})
const divergenceText = computed(() => divergence.value === true
  ? '已触发 · 只做首板/中军，禁止≥3连板，单票≤30%'
  : divergence.value === false ? '未触发 · 仍受 Step 4 均线阶梯约束'
  : 'unknown · 缺失不推断正常')
const freshnessText = computed(() => {
  if (!result.value) return '未收到可验证快照'
  if (result.value.feed_status === 'verified') return '服务端快照已核验（≤120秒）'
  if (result.value.feed_status === 'closed') return '最近收盘快照 · 非实时'
  if (result.value.feed_status === 'partial') return '服务端快照有缺口'
  if (result.value.feed_status === 'unavailable') return '服务端实时采集不可用'
  return (result.value.source_missing || []).length ? `有 ${result.value.source_missing.length} 项缺失` : '来源与时间已满足'
})
const nextStep = computed(() => {
  if (!isToday.value) return '所选日期不是今天：只能查看和留档；切回今天才能刷新自动数据。'
  if (!autoResult.value) return '先点顶部「刷新自动数据」；没有结果就按缺失阻断处理。'
  if (autoResult.value.feed_status === 'closed') {
    if (hasServerGap.value)
      return '当前非交易时段：最近收盘快照仍缺核心 / 情绪证据，不能开仓；下一交易时段 09:30 后恢复采集。'
    return `当前非交易时段：显示 ${autoResult.value.feed_trade_date || '最近交易日'} 收盘快照供复核，不授权开仓；下一交易时段 09:30 后恢复实时采集。`
  }
  if (hasServerGap.value) {
    if (autoResult.value.feed_status === 'unavailable')
      return '服务端实时采集当前不可用（非交易时段 / 未启用 / 源超时）：不要开仓；可查看缺口原因，不能用手工输入补成通过。'
    return '服务端实时快照仍有缺口（源未更新或采集失败）：不要开仓，也不能用手工输入补成通过；行业 / 个股可在 Step 2、Step 7 手工对照留档。'
  }
  if (rawMissing.value.length) return '自动数据仍有缺口：按空仓处理；可到对应步骤手工对照，但手工不授权开仓。'
  if (phase.value === 'data_check') return '09:30 数据检查：核对 Step 0–1 后保存检查卡。'
  if (phase.value === 'preview') return '14:30 只做预警：记录观察，等 14:45 再终判。'
  if (phase.value === 'weekly_review') return '周五收盘复核：核对 Step 1 周线与 Step 11 复盘，保存复核卡。'
  if (summary.value.canOpen) return '14:45 终判可开仓（仅建议）：核对 Step 6–9 后保存执行卡。'
  return '14:45 终判不开仓：按阻断原因处理，保存执行卡留档。'
})
const reasonLabels = {
  'source_missing':'数据来源缺失',
  'source_missing: flatten advisory only, no assumed fills':'数据缺失：仅给出空仓建议，不假设成交',
  'missing critical values':'关键数值缺失',
  'weekly hard veto':'周线一票否决',
  'weekly veto or daily gun not satisfied':'周线否决或发令枪未同时满足',
  'selection filter failed or unknown':'选股过滤未通过或存在 unknown',
  'holding black swan; no new entries today':'持仓黑天鹅：今日禁止新开仓',
  'daily loss circuit breaker':'当日亏损熔断',
  '退军线; no entries today':'跌破退军线：今日禁止开仓',
  'stock -5% stop; no new entries today':'个股 -5% 止损：今日禁止新开仓',
  'numbered Step 5 precedes MA5 reduction; batch size requires manual execution':'编号 Step 5 止盈优先，批量执行需人工确认',
  'holding MA5 exception never applies':'持仓不适用 MA5 豁免',
  'core B pullback; do not compound half reductions':'核心B冲高回落：减半不叠加',
  'loss freeze / stop-day / data failure latch / locked limit-down':'亏损冻结 / 停止日 / 数据故障 / 跌停锁定',
  'MA20 broken: core only':'跌破 MA20：只做中军',
  'divergence/weekly weak forbids high boards':'顶背离或周线走弱：禁止高位连板',
  'hard account cap; recommendation only':'账户总仓超上限：仅建议减仓',
  'add requires full resonance and >=3% profit; no limit-up-open adding':'加仓需三周期共振且浮盈 ≥3%',
  'holding count cap':'持仓数量已达上限',
  'position capacity exhausted; new-entry 20 point reserve conflict':'仓位额度耗尽（新仓预留 20 个百分点）',
  'calendar_unverified':'交易日历未核验',
  'source_missing':'来源缺失',
  'flatten advisory only, no assumed fills':'仅对已有证据给扁平化建议，不假定缺失数据',
  'flatten advisory only; no assumed files':'仅对已有证据给扁平化建议，不假定缺失数据',
  'only 14:45 snapshot may authorize advisory entry':'仅 14:45 快照可给出开仓建议',
  'advisory only; no execution':'仅建议，不执行',
}
function reasonText(reason) {
  if (!reason) return ''
  const text = String(reason)
  if (text.startsWith('source_missing:')) {
    const rest = text.slice('source_missing:'.length).trim()
    return '来源缺失：' + (reasonLabels[rest] || rest)
  }
  return reasonLabels[text] || text
}
function missingText(code) {
  const parts = String(code).split('.')
  const bare = { emotion:'情绪', industry:'行业', stock:'个股', account:'账户', holdings:'持仓' }[parts[0]]
  if (bare && parts.length === 1) return `${bare} · 缺失`
  const suffix = { source:'来源缺失', timestamp:'时间戳缺失', final_snapshot:'14:45快照缺失' }[parts[parts.length - 1]]
  const group = { cores:'核心', emotion:'情绪', industry:'行业', stock:'个股', account:'账户', holdings:'持仓' }[parts[0]]
  if (!group || !suffix) return String(code)
  return `${group}${parts.length > 2 ? ' ' + parts[1] : ''} · ${suffix}`
}
function fmtTime(value) {
  if (!value) return '时间缺失'
  const text = String(value)
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', { timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit',
    hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false }).format(date).replace(/\//g, '-')
}
// 缺失项来自后端原始键，这里按数据域翻译并说明“为什么缺 / 影响 / 怎么处理”，
// 避免用户把「数据未接入」误当成页面故障，也避免同一核心的来源+时间戳拆成多行。
const coreFieldLabels = {
  source:'来源未核验（daily 不算可信源）', timestamp:'时间戳缺失 / 超 120 秒', final_snapshot:'缺 14:45 快照',
  price:'现价', prev_high:'昨高', prev_low:'昨低', ma5:'日MA5', ma10:'日MA10', ma20:'日MA20', ma5_prev:'昨日MA5',
  wma5:'5周线', wma10:'10周线', wma20:'20周线', w_pct:'周涨跌%', below_ma5_days:'连续低于MA5天数',
  wma20_up4:'20周线连续4周向上', high60:'60日最高', macd_hist:'周MACD柱', macd_dead_cross:'周MACD死叉',
}
const emotionFieldLabels = { limit_up:'涨停家数', broken:'炸板家数', height:'连板高度', limit_down:'跌停家数', amount:'成交额', amount_ma5:'5日均额' }
const industryFieldLabels = { price:'现价', wma5:'5周线', wma10:'10周线', wma20:'20周线', wma20_up4:'20周线走平/向上', high20w:'20周最高', previous_high20w:'前20周最高', rank:'行业排名', amount:'成交额', amount_ma20:'20日均额', gain5:'5日涨幅', above_ma5_days:'连续站上MA5天数' }
const stockFieldLabels = { price:'现价', open:'开盘价', high:'最高价', low:'最低价', prev_close:'昨收', ma5:'MA5', ma10:'MA10', ma20:'MA20', prev_price:'上一日价', prev_ma5:'上一日MA5', volume_ratio:'量比', w_close:'周收盘', wma30:'30周线', macd_cross_weeks:'MACD金叉周数', dif:'DIF', float_cap:'流通市值', amount_avg:'平均成交额', limit_down5:'5日跌停数', return20:'20日涨幅', boards:'连板数', box_range20:'20日箱体', prior_high20:'前20日最高', n_bear_age:'N型天数', n_bear_high:'N型高点' }
const riskFieldLabels = { daily_pnl:'当日盈亏%', weekly_pnl:'周盈亏%', consecutive_losses:'连续亏损笔数', total_weight:'总仓位%', frozen_sessions:'冻结交易日', stopped_today:'今日停止交易', data_failure_today:'今日数据故障', review_complete:'复盘完成', two_month_low_winrate:'两月低胜率', weight:'仓位%', pnl:'盈亏%', price:'现价', ma5:'MA5', boards:'连板数', bought_today:'当日买入', limit_down_locked:'跌停锁定', high_volume_bear:'高位放量阴线', high_upper_shadow:'长上影', black_swan:'黑天鹅', limit_up_open:'一字涨停开板' }
function filledCount(keys, group) {
  return keys.filter(k => group && group[k] !== null && group[k] !== undefined && group[k] !== '').length
}
const stockKeys = [...fields('stock'), ...fields('stock', 'boolean')]
const industryKeys = [...fields('industry'), ...fields('industry', 'boolean')]
const accountKeys = [...fields('account'), ...fields('account', 'boolean')]
const holdingKeys = [...fields('holding'), ...fields('holding', 'boolean')]
function joinFields(keys, labels) {
  const names = keys.map(k => labels[k] || k)
  return names.length ? names.join('、') : '整组未提供'
}
const blockerRows = computed(() => {
  const coreSeen = new Map()
  const byDomain = new Map()
  const other = []
  for (const key of rawMissing.value) {
    const parts = String(key).split('.')
    const head = parts[0]
    if (head === 'cores' && parts[1]) {
      const code = parts[1]
      const field = parts[parts.length - 1]
      if (!coreSeen.has(code)) coreSeen.set(code, [])
      if (!coreSeen.get(code).includes(field)) coreSeen.get(code).push(field)
      continue
    }
    if (['emotion', 'industry', 'stock', 'account', 'holdings'].includes(head)) {
      const field = parts.slice(1).join('.')
      if (!byDomain.has(head)) byDomain.set(head, [])
      if (field && !byDomain.get(head).includes(field)) byDomain.get(head).push(field)
      continue
    }
    if (!other.includes(String(key))) other.push(String(key))
  }
  const rows = []
  const feedStatus = autoResult.value?.feed_status
  const feedOffline = feedStatus === 'unavailable'
  const reviewMode = feedStatus === 'closed'
  for (const [code, list] of coreSeen) {
    rows.push({ key:`cores.${code}`, domain:`核心 ${code} 实时快照`, manual:false, link:'',
      missing: joinFields(list, coreFieldLabels),
      cause: feedOffline
        ? '服务端实时采集不可用（未启用 / 源超时 / 非交易时段），当前只读已落库日线'
        : reviewMode
          ? '收盘快照未包含该核心的完整当日证据（日线未更新或字段缺失）'
          : '服务端未返回该核心的当日完整快照：日线未更新、实时报价缺失或字段不全',
      impact:'Step 0 完整性不通过，Step 1–5 停止判定',
      action: feedOffline
        ? '检查服务端采集开关与数据源状态；仍不可用时按缺失阻断，不能靠手工补成通过'
        : reviewMode
          ? '等待下一交易时段重新采集；仍缺失则按缺失阻断，不能靠手工补成通过'
          : '等待服务端重新采集；仍缺失则按缺失阻断，不能靠手工补成通过' })
  }
  if (byDomain.has('emotion')) {
    const filled = filledCount(Object.keys(emotionFieldLabels), card.value.evidence.emotion)
    rows.push({ key:'emotion', domain:'情绪五项', manual:false, link:'dc-step-2',
      missing: joinFields(byDomain.get('emotion'), emotionFieldLabels),
      cause: feedOffline
        ? '服务端实时采集不可用，情绪池本次未采集'
        : reviewMode
          ? '收盘快照未返回完整情绪池：涨停池未发布或部分池采集失败'
          : '服务端未返回完整情绪池：涨停池未发布或部分池采集失败',
      impact:'Step 1 周线否决（跌停家数）与 Step 2 情绪评分无法计算',
      action:`等待服务端采集恢复；或去 Step 2 手工对照（已填 ${filled}/6，仅留档）` })
  }
  if (byDomain.has('industry')) {
    const filled = filledCount(industryKeys, card.value.evidence.industry)
    rows.push({ key:'industry', domain:'行业证据', manual:true, link:'dc-step-7',
      missing: joinFields(byDomain.get('industry'), industryFieldLabels),
      cause:'未选择行业，或行业实时证据未接入',
      impact:'Step 7 行业过滤 unknown，候选不准入',
      action:`去 Step 7 录入行业（已填 ${filled}/${industryKeys.length}）` })
  }
  if (byDomain.has('stock')) {
    const candidates = card.value.candidates || []
    const filled = candidates[0] ? filledCount(stockKeys, candidates[0]) : 0
    rows.push({ key:'stock', domain:'个股证据', manual:true, link:'dc-step-7',
      missing: joinFields(byDomain.get('stock'), stockFieldLabels),
      cause: candidates.length ? '候选股证据未填齐' : '未添加候选股，自动接口也未提供个股证据',
      impact:'Step 7 个股过滤 unknown，不能准入',
      action: candidates.length ? `去 Step 7 补齐候选（已填 ${filled}/${stockKeys.length}）` : '去 Step 7 添加手工候选' })
  }
  if (byDomain.has('account')) {
    const filled = filledCount(accountKeys, card.value.account)
    rows.push({ key:'account', domain:'账户风险', manual:true, link:'dc-step-6',
      missing: joinFields(byDomain.get('account'), riskFieldLabels),
      cause:'账户风险字段为手工台账，自动接口不提供',
      impact:'Step 8 风控与仓位预算 unknown，禁止新开仓',
      action:`去 Step 6 填写账户（已填 ${filled}/${accountKeys.length}）` })
  }
  if (byDomain.has('holdings')) {
    const holdings = card.value.holdings || []
    const filled = holdings[0] ? filledCount(holdingKeys, holdings[0]) : 0
    rows.push({ key:'holdings', domain:'持仓风险', manual:true, link:'dc-step-8',
      missing: joinFields(byDomain.get('holdings'), riskFieldLabels),
      cause:'持仓风险字段为手工台账，自动接口不提供',
      impact:'Step 8 风险处置建议 unknown，不推断可卖',
      action: holdings.length ? `去 Step 8 补齐持仓（${holdings.length} 只，已填 ${filled}/${holdingKeys.length}）` : '去 Step 8 添加手工持仓' })
  }
  for (const text of other) {
    rows.push({ key:`other.${text}`, domain:'其他缺口', manual:false, link:'',
      missing:text, cause:'接口返回的原始缺失标记',
      impact:'按缺失阻断处理', action:'见下方原始缺失键' })
  }
  return rows
})
const blockerLead = computed(() => {
  const closed = autoResult.value?.feed_status === 'closed'
  if (!rawMissing.value.length) {
    return closed
      ? '未发现数据缺口；当前为非交易时段，自动数据为最近收盘快照，仅供复核，不授权开仓。'
      : '未发现数据缺口；仍以实际检查结果与交易日历核验为准。'
  }
  if (hasServerGap.value) {
    return closed
      ? '当前为非交易时段；最近收盘快照仍有服务端缺口（源未更新或字段缺失），不能靠手工填空解决。行业 / 个股可手工录入对照留档，但手工永不授权开仓。'
      : '以下为数据缺口，不是页面故障。核心 / 情绪由服务端在交易时段实时采集，但本次快照未通过核验（采集不可用、源未更新或字段缺失），不能靠手工填空解决；行业 / 个股可手工录入对照留档，但手工永不授权开仓。'
  }
  return closed
    ? '以下为手工台账缺口；当前为非交易时段，自动数据为最近收盘快照，仅供复核；手工录入也不授权开仓。'
    : '以下为手工台账缺口；可录入对照，但手工永不授权开仓。'
})
const blockerConclusion = computed(() => {
  const reasons = [...new Set([...(result.value?.verdict?.reasons || []).map(reasonText).filter(Boolean),
    ...(card.value.day_guard?.reasons || []).map(reasonText).filter(Boolean)])]
  return reasons.length ? reasons.join('；') : '按缺失阻断处理：不推断正常，不给出开仓建议。'
})
const emotionScore = computed(() => {
  const e = card.value.evidence.emotion || {}
  const need = ['limit_up','broken','height','limit_down','amount','amount_ma5']
  if (need.some(k => e[k] === null || e[k] === undefined || e[k] === '' || !Number.isFinite(Number(e[k])))) return null
  let score = 0
  if (e.limit_up >= 50) score++
  if (e.limit_up + e.broken > 0 && e.broken / (e.limit_up + e.broken) <= 0.25) score++
  if (e.height >= 4) score++
  if (e.limit_down <= 10) score++
  if (e.amount >= e.amount_ma5) score++
  return score
})
const stepTitles = ['完整性','周线','情绪','发令枪','阶梯','背离','仓位','选股','风控','交易','预案','复盘']
const coreFields = { close:'历史收盘', wma5:'5周均线', wma10:'10周均线', wma20:'20周均线', w_pct:'周涨跌%', ma5:'日MA5', ma10:'日MA10', ma20:'日MA20' }
const emotions = [
  {key:'limit_up',label:'涨停家数（剔除ST/新股首日）',rule:'≥50'},
  {key:'broken',label:'炸板家数',rule:'炸板/(涨停+炸板)≤25%'},
  {key:'height',label:'连板高度',rule:'≥4板'},
  {key:'limit_down',label:'跌停家数（含ST）',rule:'≤10，≥50一票否决'},
  {key:'amount',label:'两市成交额',rule:'≥5日均额'},
  {key:'amount_ma5',label:'两市5日平均成交额',rule:'与成交额单位一致'},
]
const planFields = { stop:'个股止损价 / 触发条件', reduce:'减仓线（MA5×0.997）', profit:'止盈线', gap:'跳空 / T+1 / 跌停预案', missing:'次日数据缺失处理（空仓建议）' }
const reviewFields = { gun_a:'883957 实测 / 昨高 / 发令枪A', gun_b:'883994 实测 / 60日最高 / 发令枪B', position:'档位 vs 实际仓位 / 不一致原因', skipped:'是否跳过编号决策树', risk:'连亏 / 熔断 / 周累计亏损', reflection:'纪律、情绪与周期反思' }
const filterGroups = [
  {title:'行业准入',items:['周线上升通道；MA20连续4周向上、近20周新高超过前20周','同花顺二级行业当日涨幅前3，成交额≥20日均额×1.3','5日涨幅≥3%，5日内≥4日收在MA5上','所属行业当日排名前10；左侧下跌或5日涨幅负数剔除']},
  {title:'个股准入',items:['周收盘≥MA30周（不放宽）；近8周MACD金叉，DIF<0仅试错','一阳穿多线 / 箱体突破 / N字反包之一','回踩不破MA5或放量突破；走坏仅试错','排除ST/退市、近3月减持/大宗折价>5%、近1月预亏/减值/立案/非标','排除停牌/复牌首日、市值<30亿或日均成交<3亿','排除近5日≥2跌停、20日跌幅>30%、长上影/放量滞涨、一字涨停']},
]
const decisionTree = ['数据不完整 → 空仓建议，停止开仓判定','持仓黑天鹅 → 该股清仓建议，禁开仓','账户亏损≥2% → 全清建议，停手','883957 < 昨低×0.997 → 全清建议，禁开仓','个股亏损≤−5% → 清仓建议，不买回','高位放量阴线/长上影 → 分批止盈建议','持仓 < MA5×0.997 → 减半建议','883994冲高回落且≥3连板 → 减半建议，与上项取大','当日已清仓/止损 → 不再开仓','发令枪A且B未同时触发 → 不开仓','选股过滤未全通过（含unknown）→ 不开仓','按档位及单票/总仓硬约束给建议；不下单']

function display(v) { return v === undefined || v === null || v === '' ? 'unknown / 缺失' : typeof v === 'object' ? JSON.stringify(v) : String(v) }
function jump(id) {
  const el = document.getElementById(id)
  if (!el) return
  // 吸顶导航在平板宽度会占 1~2 行，按实际高度留出偏移，避免标题被盖住
  const nav = document.querySelector('.dc-step-nav')
  const navH = nav ? Math.ceil(nav.getBoundingClientRect().height) : 0
  el.style.scrollMarginTop = `${navH + 12}px`
  if (typeof el.scrollIntoView === 'function') el.scrollIntoView({ behavior:'smooth', block:'start' })
}
function isStringList(v) { return Array.isArray(v) && v.every(x => typeof x === 'string') }
const thresholdLabels = {
  'complete; age <=120s': '完整，且延迟 ≤120 秒',
  'price >= wma5; weekly > -8%; limit_down <50': '现价 ≥5周线；周跌幅 > -8%；跌停 <50 家',
  'current price, never intraday high': '使用现价，不用盘中最高价',
  'prior 60 sessions excluding current session': '前60个交易日（不含当日）',
  '<2 for at least one core': '至少一个核心连续低于MA5不足2日',
  '>=4 normal; 3 downgrade; <=2 two levels; <=1 no buys': '≥4 正常；3 降一级；≤2 降两级；≤1 只减不加',
  'all required': '全部必填',
}
function checkThresholdText(v) { return thresholdLabels[String(v)] || display(v) }
const checkKeyLabels = {
  data:'数据完整性', values:'关键数值', weekly_veto:'周线一票否决',
  gun_a:'发令枪A（883957 现价 > 昨高×1.003）', gun_b:'发令枪B（883994 现价 ≥ 60日高×1.005）',
  failed_rebound:'双核反抽失败', emotion:'情绪五项', risk_evidence:'账户 / 持仓风险证据',
  pullback_inputs:'冲高回落输入', ma5:'883957 日线 MA5', ma10:'883957 日线 MA10', ma20:'883957 日线 MA20',
}
function checkKeyText(key) {
  const text = String(key ?? '').trim()
  if (!text) return 'unknown'
  if (checkKeyLabels[text]) return checkKeyLabels[text]
  const step = text.match(/^decision_step_(\d+)$/)
  if (step) return `决策树 Step ${step[1]}`
  return text
}
function passText(pass) { return pass === true ? '通过' : pass === false ? '不通过' : 'unknown · 不通过' }
function checkValue(key, field) { return display(autoChecks.value.find(c => c.key === key)?.[field]) }
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
  busy.value = true; error.value = ''; manualResult.value = null; audit.value = []
  card.value = createCard(); saveHint.value = '读取中…'
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
async function onDateChange(event) {
  if (event?.target?.value) tradeDate.value = event.target.value
  autoResult.value = null; historical.value = null
  await switchCard()
  if (isToday.value) await refresh()
}
async function onPhaseChange() { await switchCard() }
async function refresh() {
  if (!isToday.value) return
  const context = contextRevision
  busy.value = true; error.value = ''
  try {
    const response = await api.get('/api/screener/dual-core')
    if(context !== contextRevision) return
    if (response?.version !== '2.0' || !Array.isArray(response?.checks) || !Array.isArray(response?.source_missing)) throw new Error('v2 schema mismatch')
    historical.value = response
    autoResult.value = response
  } catch {
    historical.value = null; autoResult.value = null
    error.value = '自动数据读取失败，维持缺失阻断。'
  } finally { busy.value = false }
}
async function evaluateManual() {
  const context = contextRevision
  busy.value = true; error.value = ''
  try {
    const response = await api.post('/api/screener/dual-core/evaluate-v2', buildManualPayload(card.value,tradeDate.value,phase.value))
    if(context !== contextRevision) return
    if (response.version !== '2.0' || !Array.isArray(response.source_missing)) throw new Error('schema mismatch')
    manualResult.value = response
  } catch { manualResult.value = null; error.value = '手工校验失败，维持缺失阻断。' }
  finally { busy.value = false }
}
async function save() {
  if (saveHint.value === '读取失败') return
  busy.value = true; error.value = ''
  try {
    await api.put(target(), toCardBody(card.value,tradeDate.value,phase.value))
    const saved = await api.get(target())
    card.value = hydrateCard(saved); manualResult.value = null; saveHint.value = `已保存并回读 ${saved.updated_at}`
  } catch(e) { error.value = e.response?.status === 409 ? '修订冲突：未覆盖服务器。请先导出当前草稿，再重新读取卡片。' : '保存或回读失败，不能确认已保存。'; saveHint.value = '尚未确认保存' }
  finally { busy.value = false }
}
function downloadJson(value, suffix) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(value,null,2)], {type:'application/json' })); a.download = `dual-core-${tradeDate.value}-${phase.value}-${suffix}.json`; a.click(); URL.revokeObjectURL(a.href)
}
function exportDraft() { downloadJson(toCardBody(card.value,tradeDate.value,phase.value),'manual-draft') }
async function loadAudit() {
  const context = contextRevision, path = target()
  try { const response = await api.get(`${path}/audit`); if(context === contextRevision) audit.value = response.items }
  catch { if(context === contextRevision) error.value = '修订历史读取失败。' }
}
async function exportCard() {
  try { const saved = await api.get(`${target()}/export`); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(saved,null,2)], {type:'application/json' })); a.download = `dual-core-${tradeDate.value}-${phase.value}.json`; a.click(); URL.revokeObjectURL(a.href) }
  catch { error.value = '导出失败，未生成留档。' }
}
function printCard() { window.print() }
watch(card, () => { if (manualResult.value) manualResult.value = null }, { deep:true })
onMounted(async () => { await switchCard(); if (isToday.value) await refresh() })
</script>

<style scoped>
.dual-core-v2{width:100%;max-width:none;margin:0;color:var(--el-text-color-primary,#303133);line-height:1.6}
.dc-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin:0 0 10px;flex-wrap:wrap}
.dc-head h1{margin:0 0 4px;font-size:18px;font-weight:600;color:var(--el-text-color-primary,#303133)}
.dc-phase{font-size:14px;font-weight:400;color:var(--el-text-color-secondary,#909399)}
.dc-head p{margin:0;font-size:12px}
.dc-head-main{min-width:0;flex:1 1 220px}
.dc-head>.dc-tag{flex:0 0 auto}
.dc-guide{background:var(--el-bg-color,#fff);border:1px solid var(--el-border-color-lighter,#ebeef5);border-radius:6px;padding:14px 16px;margin:12px 0}
.dc-guide-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.dc-guide-head h2{font-size:15px;font-weight:600;margin:0}
.dc-guide-flow{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.dc-guide-step{display:flex;gap:8px;background:var(--el-fill-color-light,#f5f7fa);border-radius:4px;padding:10px}
.dc-guide-step>span{flex:0 0 22px;height:22px;border-radius:50%;background:var(--el-color-primary,#409eff);color:#fff;display:grid;place-items:center;font-size:12px}
.dc-guide-step b{font-size:13px;color:var(--el-text-color-primary,#303133)}
.dc-guide-step p{margin:2px 0 0;font-size:12px;color:var(--el-text-color-secondary,#909399);line-height:1.5}
.dc-guide-now{margin:0 0 10px;padding:8px 10px;border:1px solid var(--el-color-primary-light-7,#b3d8ff);background:var(--el-color-primary-light-9,#ecf5ff);color:#337ecc;border-radius:4px;font-size:13px}
.dc-guide-link{margin-left:10px;color:var(--el-color-primary,#409eff);text-decoration:none;white-space:nowrap}
.dc-guide-link:hover{text-decoration:underline}
.dc-inline-link{color:var(--el-color-primary,#409eff);text-decoration:none}
.dc-inline-link:hover{text-decoration:underline}
.dc-tools{background:var(--el-bg-color,#fff);border:1px solid var(--el-border-color-lighter,#ebeef5);border-radius:6px;padding:10px 14px;margin-bottom:6px}
.dc-tools-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px}
.dc-tools-row .el-button+.el-button{margin-left:0}
.dc-tools-row.is-sub{margin-top:8px;padding-top:8px;border-top:1px dashed var(--el-border-color-lighter,#ebeef5)}
.dc-tools-label{font-size:12px;color:var(--el-text-color-secondary,#909399);white-space:nowrap}
.dc-tools .grow{flex:1 1 auto}
.dc-save-btn{margin-left:auto}
.dc-tools-row .el-button.dc-save-btn{margin-left:auto}
.dc-field{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--el-text-color-secondary,#909399);white-space:nowrap}
.dc-field input,.dc-field select{height:30px;padding:0 10px;border:1px solid var(--el-border-color,#dcdfe6);border-radius:4px;background:var(--el-bg-color,#fff);color:var(--el-text-color-regular,#606266);font:inherit;font-size:13px;box-sizing:border-box}
.dc-field input:focus,.dc-field select:focus,.dc-field-block textarea:focus,input:focus,select:focus,textarea:focus{outline:none;border-color:var(--el-color-primary,#409eff)}
.dc-field-wide{display:flex;align-items:center;gap:8px;margin:8px 0 4px;white-space:normal}
.dc-field-wide input{flex:1;min-width:0}
.dc-save{font-size:12px;color:var(--el-text-color-secondary,#909399);margin:0 0 10px}
.dc-dashboard{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:12px 0}
.dc-dashboard article{background:var(--el-bg-color,#fff);border:1px solid var(--el-border-color-lighter,#ebeef5);border-radius:6px;padding:14px 16px}
.dc-dashboard small{display:block;color:var(--el-text-color-secondary,#909399);font-size:12px}
.dc-dashboard h2{margin:6px 0;font-size:20px;font-weight:600}
.dc-dashboard p{margin:0;font-size:12px;color:var(--el-text-color-regular,#606266)}
.dc-alert{border-radius:4px;padding:12px 16px;margin:12px 0;border:1px solid;font-size:13px}
.dc-alert strong{display:block;margin-bottom:6px}
.dc-alert ul{margin:0 0 8px;padding-left:20px}
.dc-alert p{margin:0;font-size:12px}
.dc-alert.is-warning{background:var(--el-color-warning-light-9,#fdf6ec);border-color:var(--el-color-warning-light-7,#faecd8);color:#8a6d3b}
.dc-alert.is-error{background:var(--el-color-danger-light-9,#fef0f0);border-color:var(--el-color-danger-light-7,#fde2e2);color:#a33832}
.dc-alert.is-info{background:var(--el-color-info-light-9,#f4f4f5);border-color:var(--el-border-color-lighter,#ebeef5);color:var(--el-text-color-regular,#606266)}
/* 缺失/阻断：按数据域说明原因与处置，避免把“数据未接入”当成页面故障 */
.dc-gap{background:var(--el-color-warning-light-9,#fdf6ec);border:1px solid var(--el-color-warning-light-7,#faecd8);border-radius:4px;padding:12px 16px;margin:12px 0;color:#8a6d3b}
.dc-gap-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.dc-gap-head strong{font-size:13px}
.dc-gap-lead{margin:8px 0;font-size:12px;line-height:1.6}
.dc-gap-table{min-width:760px;font-size:12px;background:var(--el-bg-color,#fff)}
.dc-gap-table th{white-space:nowrap}
.dc-gap-table th:nth-child(1){width:132px}
.dc-gap-table th:nth-child(2){width:170px}
.dc-gap-table th:nth-child(5){width:180px}
.dc-gap-table td{color:var(--el-text-color-regular,#606266);overflow-wrap:anywhere}
.dc-gap-table tr.is-server td:first-child{border-left:3px solid #f56c6c}
.dc-gap-table tr.is-manual td:first-child{border-left:3px solid #e6a23c}
.dc-gap-owner{display:block;margin-top:2px;font-size:11px;font-weight:400;color:var(--el-text-color-secondary,#909399)}
.dc-gap-owner.is-server{color:#c45656}
.dc-gap-owner.is-manual{color:#b88230}
.dc-gap-note{margin:10px 0 6px;font-size:12px;line-height:1.6;color:#6b4f1d}
.dc-gap-raw{margin:6px 0;font-size:12px}
.dc-gap-raw summary{cursor:pointer;color:var(--el-text-color-secondary,#909399)}
.dc-gap-raw p{margin:6px 0 0;overflow-wrap:anywhere}
.dc-gap-risk{margin:6px 0 0;font-size:12px;line-height:1.6}
.dc-audit{list-style:none;margin:0 0 12px;padding:0;font-size:12px;color:var(--el-text-color-secondary,#909399)}
.dc-audit li{display:flex;align-items:center;gap:8px;padding:4px 0}
.dc-section{background:var(--el-bg-color,#fff);border:1px solid var(--el-border-color-lighter,#ebeef5);border-radius:6px;padding:16px 18px;margin:12px 0;scroll-margin-top:16px}
.dc-section h2{font-size:15px;font-weight:600;margin:0 0 10px;color:var(--el-text-color-primary,#303133)}
.dc-section-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border-bottom:1px solid var(--el-border-color-lighter,#ebeef5);padding-bottom:8px;margin-bottom:10px}
.dc-section-head h2{margin:0}
.dc-section-head+.dc-help{margin-top:0}
.dc-section h3{font-size:14px;font-weight:600;margin:0 0 8px}
.dc-help{font-size:12px;color:var(--el-text-color-secondary,#909399);margin:8px 0}
.dc-budget{border:1px solid var(--el-border-color-lighter,#ebeef5);background:var(--el-fill-color-light,#f5f7fa);padding:10px 12px;border-radius:4px;font-size:13px;margin:10px 0}
.dc-budget.is-pass{color:#18725b;border-color:#c2e7d9;background:#f0f9eb}
.dc-budget.is-fail{color:#a33832;border-color:#fde2e2;background:#fef0f0}
.dc-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0}
.dc-grid article{background:var(--el-fill-color-light,#f5f7fa);padding:12px;border-radius:4px}
.dc-grid p{margin:0;font-size:13px}
.dc-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.dc-field-block{display:grid;gap:4px;font-size:12px;color:var(--el-text-color-secondary,#909399)}
.dc-field-block textarea{width:100%;box-sizing:border-box;padding:6px 10px;border:1px solid var(--el-border-color,#dcdfe6);border-radius:4px;background:var(--el-bg-color,#fff);color:var(--el-text-color-regular,#606266);font:inherit;font-size:13px;resize:vertical}
.dc-check{display:flex;align-items:center;gap:8px;font-size:13px;margin:12px 0 4px;color:var(--el-text-color-regular,#606266)}
.dc-check input{width:auto}
.dc-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.dc-edit-card{border:1px solid var(--el-border-color-lighter,#ebeef5);border-radius:4px;padding:12px;margin:12px 0;background:var(--el-bg-color,#fff)}
.dc-edit-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.dc-edit-head input{flex:1;min-width:130px;height:30px;padding:0 10px;border:1px solid var(--el-border-color,#dcdfe6);border-radius:4px;font:inherit;font-size:13px;box-sizing:border-box}
.dc-edit-card>input{width:100%;box-sizing:border-box;height:30px;padding:0 10px;border:1px solid var(--el-border-color,#dcdfe6);border-radius:4px;font:inherit;font-size:13px}
.dc-details{margin:10px 0;border:1px solid var(--el-border-color-lighter,#ebeef5);border-radius:4px;padding:8px 12px;background:var(--el-fill-color-lighter,#fafafa)}
.dc-details summary{cursor:pointer;color:var(--el-color-primary,#409eff);font-size:13px}
.dc-macd{margin-top:10px}
.dc-macd-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.dc-macd-grid .dc-field{display:grid;gap:4px;white-space:normal}
.dc-macd-grid input{width:100%}
.dc-tag{display:inline-flex;align-items:center;height:20px;padding:0 7px;border-radius:4px;border:1px solid;font-size:12px;line-height:1;white-space:nowrap}
.dc-tag.is-info{color:var(--el-color-primary,#409eff);border-color:var(--el-color-primary-light-7,#b3d8ff);background:var(--el-color-primary-light-9,#ecf5ff)}
.dc-tag.is-warning{color:#b88230;border-color:var(--el-color-warning-light-7,#faecd8);background:var(--el-color-warning-light-9,#fdf6ec)}
.dc-tag.is-danger{color:#c45656;border-color:var(--el-color-danger-light-7,#fde2e2);background:var(--el-color-danger-light-9,#fef0f0)}
.dc-tag.is-success{color:#529b2e;border-color:var(--el-color-success-light-7,#e1f3d8);background:var(--el-color-success-light-9,#f0f9eb)}
.dc-kind{margin-left:8px;padding:1px 6px;border-radius:3px;background:var(--el-fill-color,#f0f2f5);color:var(--el-text-color-secondary,#909399);font-size:11px;font-weight:400;white-space:nowrap;vertical-align:1px}
.dc-kind.is-manual{background:var(--el-color-warning-light-9,#fdf6ec);color:#b88230}
.dc-kind.is-record{background:var(--el-color-info-light-9,#f4f4f5);color:#73767a}
.dc-advanced{padding:0}
.dc-advanced-box>summary{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:14px 18px;cursor:pointer;list-style:none}
.dc-advanced-box>summary::-webkit-details-marker{display:none}
.dc-advanced-box>summary::before{content:'▸';color:var(--el-text-color-secondary,#909399);font-size:12px}
.dc-advanced-box[open]>summary::before{content:'▾'}
.dc-advanced-title{font-size:15px;font-weight:600}
.dc-advanced-hint{font-size:12px;color:var(--el-text-color-secondary,#909399)}
.dc-advanced-box[open]>summary{border-bottom:1px solid var(--el-border-color-lighter,#ebeef5);margin-bottom:12px}
.dc-advanced-box>*:not(summary){margin-left:18px;margin-right:18px}
.dc-advanced-box>*:last-child{margin-bottom:14px}
.dc-step-nav{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0;position:sticky;top:0;z-index:5;background:var(--el-bg-color,#fff);padding:8px 0;border-bottom:1px solid var(--el-border-color-lighter,#ebeef5)}
.dc-section[id^="dc-step-"],#dc-blockers{scroll-margin-top:56px}
.dc-step-nav a{padding:4px 10px;border:1px solid var(--el-border-color-lighter,#ebeef5);border-radius:14px;color:var(--el-color-primary,#409eff);text-decoration:none;font-size:12px;background:var(--el-bg-color,#fff);white-space:nowrap}
.dc-nav-group{align-self:center;font-size:12px;color:var(--el-text-color-secondary,#909399);white-space:nowrap}
.dc-nav-group:first-child{margin-left:2px}
.dc-scroll{overflow-x:auto;border:1px solid var(--el-border-color-lighter,#ebeef5);border-radius:4px}
.dc-check-table{table-layout:fixed;min-width:720px}
.dc-check-table th:nth-child(1){width:96px}
.dc-check-table th:nth-child(3){width:150px}
.dc-check-table th:nth-child(4){width:190px}
.dc-check-table th:nth-child(5){width:150px}
.dc-check-table td{overflow-wrap:anywhere}
.dc-miss{display:inline-block;margin:0 4px 4px 0;padding:1px 6px;border-radius:3px;background:var(--el-color-danger-light-9,#fef0f0);color:#c45656;font-size:12px;line-height:1.5}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--el-border-color-lighter,#ebeef5);vertical-align:top}
thead th{background:var(--el-fill-color-light,#f5f7fa);color:var(--el-text-color-secondary,#909399);font-weight:600;white-space:nowrap}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:var(--el-fill-color-lighter,#fafafa)}
td input,td select{width:100%;min-width:76px;height:30px;padding:0 8px;border:1px solid var(--el-border-color,#dcdfe6);border-radius:4px;background:var(--el-bg-color,#fff);font:inherit;font-size:13px;box-sizing:border-box;color:var(--el-text-color-regular,#606266)}
td small{display:block;margin-top:2px}
dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:5px 12px;margin:0}
dt{color:var(--el-text-color-secondary,#909399);font-size:12px}
dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
.dc-pass{color:#529b2e}
.dc-fail{color:#f56c6c}
li{margin:4px 0}
footer{font-size:12px;color:var(--el-text-color-secondary,#909399);margin:16px 0 8px}
.muted{color:var(--el-text-color-secondary,#909399)}
</style>

<style>
@media (max-width: 1200px) {
  .dual-core-v2 .dc-guide-flow{grid-template-columns:repeat(2,minmax(0,1fr))}
  .dual-core-v2 .dc-dashboard{grid-template-columns:1fr 1fr}
  .dual-core-v2 .dc-dashboard article:first-child{grid-column:1 / -1}
  /* 平板宽度下不换行堆叠，改为单行横向滚动，减小吸顶高度 */
  .dual-core-v2 .dc-step-nav{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:thin}
}
@media (max-width: 760px) {
  .dual-core-v2 .dc-guide-flow{grid-template-columns:1fr}
  .dual-core-v2 .dc-dashboard,.dual-core-v2 .dc-grid,.dual-core-v2 .dc-form-grid,.dual-core-v2 .dc-macd-grid{grid-template-columns:1fr}
  .dual-core-v2 .dc-dashboard article:first-child{grid-column:auto}
  .dual-core-v2 .dc-step-nav{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}
  .dual-core-v2 .dc-tools-row{align-items:stretch}
  .dual-core-v2 .dc-tools-row .grow{display:none}
  .dual-core-v2 .dc-tools-row .dc-field{width:100%;justify-content:space-between}
  .dual-core-v2 .dc-tools-row .dc-field input,.dual-core-v2 .dc-tools-row .dc-field select{flex:1}
  .dual-core-v2 .dc-tools-row .el-button{flex:1}
  .dual-core-v2 .dc-tools-row .dc-save-btn{margin-left:0}
  .dual-core-v2 .dc-tools-row.is-sub .el-button{flex:1 1 calc(50% - 4px)}
  .dual-core-v2 .dc-tools-label{width:100%}
  .dual-core-v2 .dc-section{padding:14px}
  .dual-core-v2 .dc-advanced-box>summary{padding:12px 14px}
  .dual-core-v2 .dc-advanced-box>*:not(summary){margin-left:14px;margin-right:14px}
  .dual-core-v2 .dc-edit-head input{min-width:100%}
  .dual-core-v2 .dc-scroll table{min-width:640px}
}
/* 缺失表在平板/手机上改为逐条卡片，避免 5 列横向滚动且每行被压得过高 */
@media (max-width: 1200px) {
  .dual-core-v2 .dc-scroll .dc-gap-table{display:block;min-width:0!important;border:none;background:transparent}
  .dual-core-v2 .dc-gap-table thead{display:none}
  .dual-core-v2 .dc-gap-table tbody,.dual-core-v2 .dc-gap-table tr,.dual-core-v2 .dc-gap-table td{display:block;width:auto}
  .dual-core-v2 .dc-gap-table tr{margin:8px 0;padding:8px 10px;border:1px solid var(--el-border-color-lighter,#ebeef5);border-radius:4px;background:var(--el-bg-color,#fff)}
  .dual-core-v2 .dc-gap-table tr.is-server{border-left:3px solid #f56c6c}
  .dual-core-v2 .dc-gap-table tr.is-manual{border-left:3px solid #e6a23c}
  .dual-core-v2 .dc-gap-table td{border:none;padding:3px 0;font-size:12px}
  .dual-core-v2 .dc-gap-table td::before{content:attr(data-label) '：';color:var(--el-text-color-secondary,#909399);font-weight:600}
  .dual-core-v2 .dc-gap-table td:first-child::before{display:none}
  .dual-core-v2 .dc-gap-table tr.is-server td:first-child,.dual-core-v2 .dc-gap-table tr.is-manual td:first-child{border-left:none}
  .dual-core-v2 .dc-gap-table tr:hover td{background:transparent}
}
@media print {
  .no-print{display:none!important}
  .dual-core-v2{max-width:none;font-size:10pt;line-height:1.4;color:#111}
  .dc-head h1{font-size:16pt}
  .dc-head p,.dc-help{color:#333}
  .dc-dashboard{grid-template-columns:2fr 1fr 1fr;gap:6px}
  .dc-dashboard article,.dc-section{padding:8px;border:1px solid #aaa;border-radius:0;box-shadow:none;margin:8px 0}
  .dc-section h2{color:#111;font-size:13pt;break-after:avoid}
  .dc-grid{grid-template-columns:1fr 1fr;gap:8px}
  .dc-grid article,.dc-alert,.dc-budget,.dc-gap{background:#fff!important;border:1px solid #aaa}
  .dc-scroll{overflow:visible}
  table{font-size:9pt;table-layout:fixed;min-width:0!important}
  /* 缺失/阻断表在 A4 上改为自动列宽，避免长文本被压成一字一行 */
  .dual-core-v2 .dc-gap-table{table-layout:auto}
  .dc-gap{padding:8px}
  .dc-gap-table{font-size:9pt}
  /* A4 宽度下压缩固定列，给「实际值」留出缺失标签的空间，避免一字一行 */
  .dual-core-v2 .dc-check-table th:nth-child(1){width:84px}
  .dual-core-v2 .dc-check-table th:nth-child(3){width:104px}
  .dual-core-v2 .dc-check-table th:nth-child(4){width:116px}
  .dual-core-v2 .dc-check-table th:nth-child(5){width:88px}
  .dual-core-v2 .dc-miss{white-space:nowrap;padding:0 4px}
  td,th{overflow-wrap:anywhere;padding:5px}
  thead{display:table-header-group}
  tr,.dc-edit-card,.dc-dashboard article{break-inside:avoid}
  input,select,textarea{border:none;border-bottom:1px dotted #888;padding:2px;font-size:9pt;background:transparent;color:#111;appearance:none;resize:none}
  textarea{field-sizing:content;overflow:visible;min-height:36px}
  .dc-details{border:none;padding:0;background:transparent}
  .dc-pass,.dc-fail{color:#111}
  p,li{orphans:3;widows:3}
}
@page{size:A4 portrait;margin:12mm}
@media print{body:has(.dual-core-v2) .el-aside,body:has(.dual-core-v2) .el-header,body:has(.dual-core-v2) .mobile-nav,body:has(.dual-core-v2) .el-backtop{display:none!important}body:has(.dual-core-v2),body:has(.dual-core-v2) #app,body:has(.dual-core-v2) .el-container,body:has(.dual-core-v2) .el-main{height:auto!important;overflow:visible!important;min-width:0!important;margin:0!important;padding:0!important;background:#fff!important}}
</style>
