import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, writeFile, unlink } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { Window } from 'happy-dom'

test('list pages distinguish unavailable data from empty data and allow retry', async () => {
  const win = new Window({ url: 'http://localhost/#/dashboard' })
  for (const key of ['window', 'document', 'Document', 'ShadowRoot', 'HTMLElement', 'SVGElement', 'Element', 'Node']) {
    globalThis[key] = key === 'window' ? win : key === 'document' ? win.document : win[key]
  }
  const { createApp, h, nextTick } = await import('vue')
  const flush = async () => { await new Promise((resolve) => setTimeout(resolve, 10)); await nextTick() }
  const passThrough = { setup: (_, { slots }) => () => h('div', [slots.header?.(), slots.default?.(), slots.footer?.()]) }
  const button = { setup: (_, { slots, attrs }) => () => h('button', attrs, slots.default?.()) }
  const alert = { props: ['title'], setup: (props) => () => h('div', { class: 'mock-alert' }, props.title) }
  const empty = { props: ['description'], setup: (props, { slots }) => () => h('div', { class: 'mock-empty' }, [props.description, slots.description?.()]) }
  const table = { setup: () => () => h('div') }
  const mountPage = (component, host) => {
    const app = createApp(component)
    for (const name of ['ElCard', 'ElSelect', 'ElOption', 'ElSwitch', 'ElRadioGroup', 'ElRadioButton', 'ElInput', 'ElInputNumber', 'ElForm', 'ElFormItem', 'ElDialog', 'ElPopconfirm', 'ElTag', 'ElTooltip', 'ElPagination']) app.component(name, passThrough)
    app.component('ElTable', table)
    app.component('ElTableColumn', table)
    app.component('ElButton', button)
    app.component('ElAlert', alert)
    app.component('ElEmpty', empty)
    app.directive('loading', {})
    app.mount(host)
    return app
  }
  const fixtures = [
    { page: 'Signals', fail: ['/api/screener/signals'], messages: ['技术信号读取失败'], empty: '暂无技术信号' },
    { page: 'News', fail: ['/api/screener/news'], messages: ['新闻读取失败'], empty: '暂无新闻' },
    { page: 'Lhb', fail: ['/api/screener/lhb', '/api/screener/hot-money-seats'], messages: ['龙虎榜读取失败', '席位读取失败'], empty: '暂无龙虎榜数据' },
    { page: 'Watchlist', fail: ['/api/watchlist', '/api/watchlist/alerts'], messages: ['自选列表读取失败', '预警读取失败'], empty: '暂无自选', retries: ['刷新列表', '刷新'] },
    { page: 'Pool', fail: ['/api/screener/pool', '/api/screener/pool/history'], messages: ['选股池读取失败', '出池留档读取失败'], empty: '暂无选股池行情', retries: ['刷新', '刷新'] },
  ]
  const temporary = []
  let activeApp
  try {
    for (const fixture of fixtures) {
      let failing = true
      let pendingQuote = null
      let pendingTracks = null
      let quoteRequested = false
      const requested = []
      globalThis.__pageFailureApi = {
        async get(url) {
          requested.push(url)
          if (failing && fixture.fail.includes(url)) throw new Error('offline')
          if (fixture.page === 'Pool' && pendingTracks && url === '/api/screener/tracks') return pendingTracks
          if (fixture.page === 'Pool' && pendingQuote && url === '/api/screener/pool') return [{ code: '000001' }]
          if (fixture.page === 'Pool' && pendingQuote && url === '/api/screener/pool/quotes') {
            quoteRequested = true
            return pendingQuote
          }
          if (url === '/api/screener/dashboard') return { risk_guard: false }
          return []
        },
      }
      const source = await readFile(new URL(`../views/${fixture.page}.vue`, import.meta.url), 'utf8')
      let code = compileScript(parse(source).descriptor, { id: `failure-${fixture.page}`, inlineTemplate: true }).content
      code = code.replace("import api from '../api'", 'const api = globalThis.__pageFailureApi')
        .replaceAll("'../utils/fmt'", JSON.stringify(new URL('./fmt.js', import.meta.url).href))
        .replaceAll("'../utils/tasks'", JSON.stringify(new URL('./tasks.js', import.meta.url).href))
        .replaceAll("'../utils/portfolioQuotes'", JSON.stringify(new URL('./portfolioQuotes.js', import.meta.url).href))
        .replaceAll("'../utils/poolQuotes'", JSON.stringify(new URL('./poolQuotes.js', import.meta.url).href))
        .replace("import { useUserStore } from '../store'", 'const useUserStore = () => ({ isAnalyst: true })')
        .replace("import { useRoute, useRouter } from 'vue-router'", "const useRoute = () => ({ query: {} }); const useRouter = () => ({ push: () => Promise.resolve() })")
      const path = new URL(`./.pagefailure-${fixture.page}.runtime.mjs`, import.meta.url)
      temporary.push(path)
      await writeFile(path, code)
      const { default: component } = await import(`${path.href}?t=${Date.now()}`)
      const host = document.createElement('div')
      document.body.append(host)
      const app = mountPage(component, host)
      activeApp = app
      await flush()
      for (const message of fixture.messages) assert.match(host.textContent, new RegExp(message), fixture.page)
      assert.equal(host.querySelectorAll('.mock-empty').length, 0, `${fixture.page}: error must not masquerade as an empty result`)
      failing = false
      const used = {}
      for (const text of fixture.retries || ['刷新']) {
        const index = used[text] || 0
        used[text] = index + 1
        const target = [...host.querySelectorAll('button')].filter((item) => item.textContent.trim() === text)[index]
        assert.ok(target, `${fixture.page}: ${text} retry`)
        target.click()
        await flush()
      }
      for (const message of fixture.messages) assert.doesNotMatch(host.textContent, new RegExp(message), fixture.page)
      assert.match(host.textContent, new RegExp(fixture.empty), fixture.page)
      if (fixture.page === 'Pool') {
        let resolveQuote
        pendingQuote = new Promise((resolve) => { resolveQuote = resolve })
        ;[...host.querySelectorAll('button')].find((item) => item.textContent.trim() === '刷新').click()
        await flush()
        assert.equal(quoteRequested, true, 'Pool: the late quote request is in flight')
        app.unmount()
        activeApp = null
        const originalSetInterval = globalThis.setInterval
        let lateIntervals = 0
        try {
          globalThis.setInterval = (...args) => { lateIntervals += 1; return originalSetInterval(...args) }
          resolveQuote({ items: {} })
          await flush()
          assert.equal(lateIntervals, 0, 'Pool: an unmounted page must not restart polling')
        } finally {
          globalThis.setInterval = originalSetInterval
        }

        let resolveTracks
        pendingTracks = new Promise((resolve) => { resolveTracks = resolve })
        requested.length = 0
        const secondApp = mountPage(component, host)
        activeApp = secondApp
        await flush()
        secondApp.unmount()
        activeApp = null
        resolveTracks([])
        await flush()
        assert.equal(requested.includes('/api/screener/pool'), false, 'Pool: unmounted page must not start a list request')
      } else {
        app.unmount()
        activeApp = null
      }
      host.remove()
    }
  } finally {
    activeApp?.unmount()
    await Promise.all(temporary.map((path) => unlink(path).catch(() => {})))
    win.happyDOM.abort()
    delete globalThis.__pageFailureApi
  }
})
