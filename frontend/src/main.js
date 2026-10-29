import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import router from './router'
import './style.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')

// hash 路由下，页内锚点（如报告/帮助里的 #section）会被当成路由地址导致白屏。
// 统一拦截为页内滚动；#/xxx 路由链接保持原样。
document.addEventListener('click', (event) => {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const anchor = event.target instanceof Element ? event.target.closest('a[href^="#"]') : null
  if (!anchor) return
  const href = anchor.getAttribute('href') || ''
  if (href.startsWith('#/')) return
  event.preventDefault()
  const id = href.slice(1)
  if (!id) return
  const target = document.getElementById(decodeURIComponent(id))
  if (target && typeof target.scrollIntoView === 'function') target.scrollIntoView({ behavior: 'smooth', block: 'start' })
})
