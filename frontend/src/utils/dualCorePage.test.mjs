import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, writeFile, unlink } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { Window } from 'happy-dom'

test('real Vue page fails closed, edits records and saves exact card with readback', async () => {
  const win = new Window({ url:'http://localhost/' })
  for (const key of ['window','document','Document','ShadowRoot','HTMLElement','SVGElement','Element','Node']) globalThis[key] = key === 'window' ? win : key === 'document' ? win.document : win[key]
  const calls = []
  let saved
  let releaseAudit
  globalThis.__dualApi = {
    async get(url) {
      calls.push(['GET',url])
      if(url.endsWith('/audit')) return new Promise(resolve=>{releaseAudit=resolve})
      if (url === '/api/screener/dual-core') return { as_of:'2026-10-27', history_as_of:'2026-10-27', data:{}, version:'2.0',phase:'closed',source_missing:['emotion-feed-missing'],data_quality:{complete:false},checks:[],verdict:{cap:0},decision:{can_open:false}}
      if (saved) return saved
      throw {response:{status:404}}
    },
    async post(url,body) { calls.push(['POST',url,body]); return {version:'2.0',phase:'warning',source_missing:['manual-evidence'],data_quality:{complete:false},checks:[],verdict:{cap:0},decision:{can_open:false}, evidence_analysis:{status:'unverified_manual',checks:[{key:'gun_a',actual:110,threshold:100.3,pass:true}],verdict:{cap:50},decision:{can_open:false,reason:'reference-fixture'}}} },
    async put(url,body) { calls.push(['PUT',url,body]); saved = {...body, revision:1, updated_at:'saved'}; return saved }
  }
  const source = await readFile(new URL('../views/DualCore.vue', import.meta.url),'utf8')
  const { descriptor } = parse(source)
  let code = compileScript(descriptor, { id:'behavior-test',inlineTemplate:true }).content
  code = code.replace("import api from '../api'",'const api = globalThis.__dualApi').replace("'../utils/dualCoreCard.mjs'",JSON.stringify(new URL('./dualCoreCard.mjs',import.meta.url).href))
  const childPath = new URL('./.dualCore.fields.runtime.mjs',import.meta.url)
  const child = parse(await readFile(new URL('../views/DualCoreEvidenceFields.vue',import.meta.url),'utf8')).descriptor
  await writeFile(childPath,compileScript(child,{id:'fields-test',inlineTemplate:true}).content.replace("'../utils/dualCoreCard.mjs'",JSON.stringify(new URL('./dualCoreCard.mjs',import.meta.url).href)))
  code = code.replace("'./DualCoreEvidenceFields.vue'",JSON.stringify(childPath.href))
  const path = new URL('./.dualCore.runtime.mjs',import.meta.url)
  await writeFile(path,code)
  try {
    const { createApp, nextTick } = await import('vue')
    const { default: Component } = await import(`${path.href}?t=${Date.now()}`)
    const host = document.createElement('div'); document.body.append(host)
    const app = createApp(Component); app.mount(host)
    const flush = async () => { await new Promise(r=>setTimeout(r,10)); await nextTick() }
    await flush()
    assert.match(host.textContent,/缺失阻断/)
    assert.match(host.textContent,/emotion-feed-missing/)
    assert.equal(host.querySelectorAll('section[id^="dc-step-"]').length,12)
    const click = async text => { const b=[...host.querySelectorAll('button')].find(b=>b.textContent===text); assert.ok(b,text); b.click(); await flush() }
    await click('添加手工记录')
    assert.equal(host.querySelector('[aria-label="成交状态"]').value,'未执行')
    const codeInput=host.querySelector('[aria-label="交易代码"]'); codeInput.value='600000'; codeInput.dispatchEvent(new win.Event('input',{bubbles:true}))
    await click('添加手工持仓')
    const t1=host.querySelector('[data-manual-bool="holding.0.bought_today"]'); t1.value='true'; t1.dispatchEvent(new win.Event('change',{bubbles:true})); await flush()
    assert.match(host.textContent,/T\+1受限/)
    await click('保存执行卡')
    const write=calls.find(c=>c[0]==='PUT')
    assert.equal(write[2].trades[0].code,'600000')
    assert.equal(write[2].trades[0].status,'未执行')
    assert.equal(write[2].evidence.source,'manual')
    assert.equal(write[2].revision,0)
    assert.equal(calls.at(-1)[0],'GET')
    assert.match(host.textContent,/已保存并回读/)
    host.querySelectorAll('button').forEach(b=>{if(b.textContent==='修订历史')b.click()})
    assert.equal(calls.some(c=>c[0]==='POST'),false)
    const emotion = host.querySelector('[aria-label="手工情绪实测"]'); emotion.value='999'; emotion.dispatchEvent(new win.Event('input',{bubbles:true})); await flush()
    assert.match(host.textContent,/缺失阻断/)
    let prints=0; win.print=()=>{prints++}; await click('打印 A4'); assert.equal(prints,1)
    const stage = host.querySelector('.dc-tools select'); stage.value='preview'; stage.dispatchEvent(new win.Event('change',{bubbles:true})); await flush()
    assert.match(calls.at(-1)[1],/\/preview$/)
    assert.match(host.textContent,/14:30 预警（非执行）/)
    releaseAudit({items:[{revision:99,action:'STALE-AUDIT',recorded_at:'old'}]}); await flush()
    assert.doesNotMatch(host.textContent,/STALE-AUDIT/)
    const corePrice=host.querySelector('[data-manual-number="core.883957.price"]'); corePrice.value='110';corePrice.dispatchEvent(new win.Event('input',{bubbles:true}))
    const weight=host.querySelector('[data-manual-number="account.total_weight"]');weight.value='20';weight.dispatchEvent(new win.Event('input',{bubbles:true}))
    await click('添加手工候选')
    const blackSwan=host.querySelector('[data-manual-bool="candidate.0.black_swan"]');assert.match(blackSwan.value,/unknown/);blackSwan.value='false';blackSwan.dispatchEvent(new win.Event('change',{bubbles:true}))
    await click('校验手工证据（非实时）')
    assert.equal(calls.at(-1)[1],'/api/screener/dual-core/evaluate-v2')
    assert.equal(calls.at(-1)[2].evidence.emotion.source,'manual')
    assert.equal(calls.at(-1)[2].evidence.cores['883957'].price,110)
    assert.equal(calls.at(-1)[2].account.total_weight,20)
    assert.equal(calls.at(-1)[2].candidates[0].black_swan,false)
    assert.equal(calls.at(-1)[2].holdings[0].limit_down_locked,null)
    assert.match(host.textContent,/manual-evidence/)
    assert.match(host.textContent,/缺失阻断/)
    assert.match(host.textContent,/reference-fixture/)
    const date = host.querySelector('input[type="date"]'); date.value='2026-10-29'; date.dispatchEvent(new win.Event('change',{bubbles:true})); await flush()
    assert.doesNotMatch(host.textContent,/reference-fixture|manual-evidence|emotion-feed-missing/)
    assert.match(host.querySelector('[data-manual-bool="account.review_complete"]').value,/unknown/)
    assert.ok(host.querySelector('[data-manual-number="core.883957.price"]'))
    assert.ok(host.querySelector('[data-manual-number="industry.amount_ma20"]'))
    app.unmount()
  } finally { await unlink(path); await unlink(childPath); win.happyDOM.abort() }
})
