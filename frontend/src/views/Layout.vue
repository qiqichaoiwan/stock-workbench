<template>
  <el-container class="workbench-shell">
    <el-aside v-if="!isMobile" width="184px" class="aside">
      <div class="brand" aria-label="智能选股工作台">
        <span class="brand-mark" aria-hidden="true"><el-icon><DataAnalysis /></el-icon></span>
        <span class="brand-copy"><strong>选股工作台</strong><small>STOCK WORKBENCH</small></span>
      </div>
      <div class="aside-caption">研究与风险控制</div>
      <el-menu :default-active="activePath" router class="workbench-menu"
               background-color="transparent" text-color="#a8b5c5" active-text-color="#fff">
        <template v-for="group in visibleGroups" :key="group.label">
          <div class="menu-group">{{ group.label }}</div>
          <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
            <el-icon class="menu-icon" aria-hidden="true"><component :is="item.icon" /></el-icon>
            <span>{{ item.title }}</span>
          </el-menu-item>
        </template>
      </el-menu>
      <div class="aside-foot">
        <span class="health-dot" :class="`health-${dataHealthTone}`" aria-hidden="true" />
        <span><strong>{{ dataHealthLabel }}</strong><small>{{ dataHealthDetail }}</small></span>
      </div>
    </el-aside>

    <el-drawer v-else v-model="drawerOpen" direction="ltr" :size="260" :with-header="false"
               class="m-drawer">
      <div class="aside aside-mobile">
        <div class="brand" aria-label="智能选股工作台">
          <span class="brand-mark" aria-hidden="true"><el-icon><DataAnalysis /></el-icon></span>
          <span class="brand-copy"><strong>选股工作台</strong><small>STOCK WORKBENCH</small></span>
        </div>
        <div class="aside-caption">研究与风险控制</div>
        <el-menu :default-active="activePath" router class="workbench-menu"
                 background-color="transparent" text-color="#a8b5c5" active-text-color="#fff"
                 @select="drawerOpen = false">
          <template v-for="group in visibleGroups" :key="group.label">
            <div class="menu-group">{{ group.label }}</div>
            <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
              <el-icon class="menu-icon" aria-hidden="true"><component :is="item.icon" /></el-icon>
              <span>{{ item.title }}</span>
            </el-menu-item>
          </template>
        </el-menu>
        <div class="aside-foot">
          <span class="health-dot" :class="`health-${dataHealthTone}`" aria-hidden="true" />
          <span><strong>{{ dataHealthLabel }}</strong><small>{{ dataHealthDetail }}</small></span>
        </div>
      </div>
    </el-drawer>

    <el-container class="workspace-container">
      <el-header class="header">
        <div class="header-left">
          <el-button v-if="isMobile" class="m-burger" text aria-label="打开导航" @click="drawerOpen = true">
            <el-icon :size="21"><Menu /></el-icon>
          </el-button>
          <div class="page-heading">
            <span class="eyebrow">MARKET WORKSPACE</span>
            <strong>{{ headerTitle || '智能选股工作台' }}</strong>
          </div>
        </div>
        <div class="header-search" :class="{ 'mobile-search-open': mobileSearchOpen }">
          <button class="header-search-icon" type="button" :aria-expanded="mobileSearchOpen" aria-label="聚焦股票搜索" @click="focusSearch">
            <el-icon aria-hidden="true"><Search /></el-icon>
          </button>
          <el-autocomplete ref="searchInput" v-model="stockQ" :fetch-suggestions="fetchStocks"
                           placeholder="搜索股票代码 / 名称 / 拼音" clearable
                           value-key="label" @select="onPickStock" />
          <kbd>⌘ K</kbd>
        </div>
        <div class="header-actions">
          <div class="date-chip">
            <el-icon aria-hidden="true"><Calendar /></el-icon>
            <span>{{ todayText }}</span>
          </div>
          <div class="market-chip" :class="`market-${marketTone}`">
            <span class="market-dot" aria-hidden="true" />
            <span>{{ marketStatus }}</span>
          </div>
          <el-button class="refresh-button" text aria-label="刷新当前页面" @click="refreshCurrent">
            <el-icon><Refresh /></el-icon><span class="refresh-label">刷新</span>
          </el-button>
          <el-dropdown @command="onCommand">
            <button class="user-chip" type="button" aria-label="打开用户菜单">
              <span class="avatar"><el-icon><User /></el-icon></span>
              <span class="user-meta"><strong class="user-name">{{ userStore.user?.nickname || userStore.user?.username || '未登录' }}</strong><small>{{ roleLabel }}</small></span>
              <el-icon class="user-chevron" aria-hidden="true"><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="password">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>

    <el-dialog v-model="pwdDialog" title="修改密码" :width="isMobile ? '92%' : '420px'">
      <el-form label-width="90px">
        <el-form-item label="原密码">
          <el-input v-model="pwdForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwdForm.new_password" type="password" show-password placeholder="至少 8 位" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="pwdForm.confirm" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdDialog = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="savePassword">保存</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown, Bell, Briefcase, Calendar, CircleCheck, CollectionTag, DataAnalysis, Document, HelpFilled, Menu, Refresh, Search, Setting, Tickets, TrendCharts, User } from '@element-plus/icons-vue'
import api from '../api'
import { useUserStore } from '../store'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

// 移动端判定(<=768px):侧边栏改抽屉
const isMobile = ref(window.matchMedia('(max-width: 768px)').matches)
const _mq = window.matchMedia('(max-width: 768px)')
const _onMq = (e) => { isMobile.value = e.matches }
const clockNow = ref(new Date())
let clockTimer = null
let sessionTimer = null
let dataHealthTimer = null
if (_mq.addEventListener) _mq.addEventListener('change', _onMq)
onUnmounted(() => {
  if (_mq.removeEventListener) _mq.removeEventListener('change', _onMq)
  if (_searchTimer) clearTimeout(_searchTimer)
  if (clockTimer) clearInterval(clockTimer)
  if (sessionTimer) clearInterval(sessionTimer)
  if (dataHealthTimer) clearInterval(dataHealthTimer)
  _searchSeq += 1
})

const drawerOpen = ref(false)

// 静态菜单配置(roles 为空 = 所有登录角色可见)
const menuGroups = [
  {
    label: '智能选股工作台',
    items: [
      { path: '/dashboard', title: '仪表盘', icon: DataAnalysis },
      { path: '/market', title: '大盘指数', icon: TrendCharts },
      { path: '/dual-core', title: '双核共振', icon: CircleCheck },
      { path: '/pool', title: '选股池', icon: CollectionTag },
      { path: '/lhb', title: '龙虎榜', icon: Tickets },
      { path: '/industries', title: '行业与赛道', icon: Briefcase },
      { path: '/signals', title: '技术信号', icon: DataAnalysis },
      { path: '/news', title: '新闻甄别', icon: Document },
      { path: '/strategy', title: '策略配置', icon: Setting, roles: ['admin', 'analyst'] },
      { path: '/cards', title: '策略卡', icon: CollectionTag, roles: ['admin', 'analyst'] },
      { path: '/tasks', title: '任务中心', icon: Tickets, roles: ['admin', 'analyst'] },
    ],
  },
  {
    label: '持仓工作台',
    items: [
      { path: '/portfolio', title: '我的持仓', icon: Briefcase },
      { path: '/watchlist', title: '自选预警', icon: Bell },
      { path: '/trades', title: '交易历史', icon: Tickets },
      { path: '/reports', title: '报告中心', icon: Document },
      { path: '/settings', title: 'LLM 设置', icon: Setting },
    ],
  },
  {
    label: '系统管理',
    roles: ['admin'],
    items: [{ path: '/users', title: '用户与权限', icon: User }],
  },
  {
    label: '使用帮助',
    items: [{ path: '/help', title: '使用帮助', icon: HelpFilled }],
  },
]

const visibleGroups = computed(() =>
  menuGroups
    .filter((g) => !g.roles?.length || g.roles.includes(userStore.role))
    .map((g) => ({ ...g, items: g.items.filter((i) => !i.roles?.length || i.roles.includes(userStore.role)) }))
    .filter((g) => g.items.length),
)

const activePath = computed(() => route.path)
const currentTitle = computed(() => {
  for (const g of visibleGroups.value) {
    const hit = g.items.find((i) => i.path === route.path)
    if (hit) return hit.title
  }
  return ''
})
const headerTitle = computed(() =>
  (isMobile.value ? currentTitle.value || '智能选股工作台' : currentTitle.value))

const todayText = computed(() => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
}).format(clockNow.value).replace(/星期([一二三四五六日])/, '周$1'))
const serverSession = ref(null)
const dataHealth = ref(null)
const localMarketState = computed(() => {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', hourCycle: 'h23', weekday: 'short', hour: '2-digit', minute: '2-digit',
  }).formatToParts(clockNow.value).map((part) => [part.type, part.value]))
  if (parts.weekday === 'Sat' || parts.weekday === 'Sun') return '非交易日'
  const minute = Number(parts.hour) * 60 + Number(parts.minute)
  if (minute < 570) return '未开盘'
  if ((minute >= 570 && minute < 690) || (minute >= 780 && minute < 900)) return '交易中'
  if (minute < 780) return '午间休市'
  return '已收盘'
})
const marketState = computed(() => serverSession.value?.state || ({ '交易中': 'trading', '午间休市': 'lunch', '已收盘': 'closed', '非交易日': 'non_trading', '未开盘': 'preopen' }[localMarketState.value] || 'unknown'))
const marketStatus = computed(() => {
  const label = serverSession.value?.label || ({ trading: '交易中', lunch: '午间休市', closed: '已收盘', non_trading: '非交易日', preopen: '盘前' }[marketState.value] || localMarketState.value)
  return !serverSession.value || serverSession.value.calendar_verified === false ? `${label} · 待核验` : label
})
const marketTone = computed(() => ({ trading: 'live', lunch: 'pause', closed: 'closed', non_trading: 'closed', preopen: 'pause' })[marketState.value] || 'pause')

const dataHealthTone = computed(() => {
  const status = dataHealth.value?.status
  return status === 'ok' ? 'ok' : status === 'degraded' ? 'degraded' : 'unknown'
})
const dataHealthLabel = computed(() => {
  const status = dataHealth.value?.status
  if (status === 'ok') return '实时源已连接'
  if (status === 'degraded') return '实时源已降级'
  return '数据服务待核验'
})
const dataHealthDetail = computed(() => {
  if (!dataHealth.value) return '尚未收到质量心跳'
  if (dataHealth.value.status === 'ok') return `已接收 ${dataHealth.value.latest_count || 0} 个标的`
  if (dataHealth.value.status === 'degraded') return '延迟、未授权或序列缺口'
  return dataHealth.value.stream_enabled === false ? '推送层未启用，使用公共源' : '暂无可信推送事件'
})

const roleLabel = computed(
  () => ({ admin: '管理员', analyst: '策略员', user: '普通用户' })[userStore.role] || userStore.role)
const roleTagType = computed(
  () => ({ admin: 'danger', analyst: 'warning', user: 'info' })[userStore.role] || 'info')

const pwdDialog = ref(false)
const pwdSaving = ref(false)
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })
const searchInput = ref(null)
const mobileSearchOpen = ref(false)

function focusSearch() {
  if (isMobile.value && !mobileSearchOpen.value) {
    mobileSearchOpen.value = true
    nextTick(() => focusSearch())
    return
  }
  const component = searchInput.value
  const input = component?.$el?.querySelector?.('input') || component?.input
  input?.focus?.()
}

function onGlobalKeydown(event) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    focusSearch()
  } else if (event.key === 'Escape' && mobileSearchOpen.value) {
    mobileSearchOpen.value = false
    stockQ.value = ''
  }
}

async function refreshMarketSession() {
  try {
    // Only the session badge is needed here.  The full cockpit feed starts
    // four minute-index and two fund-flow refreshes, so keep that expensive
    // call confined to the dashboard itself.
    const data = await api.get('/api/screener/cockpit/session', { silent: true })
    if (data?.session) serverSession.value = data.session
  } catch {
    // The local clock remains visible while the server calendar is unavailable.
    serverSession.value = null
  }
}

async function refreshDataHealth() {
  try {
    dataHealth.value = await api.get('/api/marketdata/health', { silent: true })
  } catch {
    dataHealth.value = null
  }
}

onMounted(() => {
  userStore.fetchMe()
  clockTimer = setInterval(() => { clockNow.value = new Date() }, 1000)
  window.addEventListener('keydown', onGlobalKeydown)
  refreshMarketSession()
  sessionTimer = setInterval(refreshMarketSession, 60000)
  refreshDataHealth()
  dataHealthTimer = setInterval(refreshDataHealth, 15000)
})
onUnmounted(() => window.removeEventListener('keydown', onGlobalKeydown))
watch(() => userStore.role, (role) => {
  const allowed = route.meta.roles
  if (Array.isArray(allowed) && allowed.length && !allowed.includes(role)) {
    ElMessage.warning('当前账号已无权访问该页面')
    router.replace('/dashboard')
  }
})

// 全局搜股:选中后跳 /pool + localStorage 透传,Pool.onMounted 读取填 search
const stockQ = ref('')
// 防抖 300ms:避免每敲一个字符就打一次 /search(后端拼音索引有缓存但仍省请求)
let _searchTimer = null
let _searchSeq = 0
function fetchStocks(q, cb) {
  if (_searchTimer) clearTimeout(_searchTimer)
  const seq = ++_searchSeq
  if (!q?.trim()) return cb([])
  _searchTimer = setTimeout(async () => {
    try {
      const data = await api.get('/api/screener/search',
        { params: { q: q.trim(), limit: 20 }, silent: true }).catch(() => [])
      if (seq !== _searchSeq) return
      const list = (Array.isArray(data) ? data : []).map((s) => ({
        ...s, label: `${s.code} ${s.name || ''}`.trim(), value: `${s.code} ${s.name || ''}`.trim(),
      }))
      cb(list)
    } catch { if (seq === _searchSeq) cb([]) }
  }, 300)
}
function onPickStock(item) {
  if (!item?.code) return
  stockQ.value = ''
  mobileSearchOpen.value = false
  ElMessage.success(`已定位 ${item.code} ${item.name || ''}`.trim())
  // 用路由 query 透传,避免 location.reload() 整页刷新丢失其它页面状态
  const query = { q: item.code }
  // 已在池页且是同一代码时补时间戳,让 Pool 的 query 监听重新触发定位
  if (route.path === '/pool' && route.query.q === item.code) query.t = String(Date.now())
  router.push({ path: '/pool', query })
}

function refreshCurrent() {
  window.location.reload()
}

function onCommand(cmd) {
  if (cmd === 'logout') {
    userStore.logout()
    router.push('/login')
  } else if (cmd === 'password') {
    pwdForm.old_password = pwdForm.new_password = pwdForm.confirm = ''
    pwdDialog.value = true
  }
}

async function savePassword() {
  if (!pwdForm.old_password) return ElMessage.warning('请输入原密码')
  if (pwdForm.new_password.length < 8) return ElMessage.warning('新密码至少 8 位')
  if (pwdForm.new_password !== pwdForm.confirm) return ElMessage.warning('两次输入的新密码不一致')
  pwdSaving.value = true
  try {
    await api.patch('/api/auth/me', {
      old_password: pwdForm.old_password,
      new_password: pwdForm.new_password,
    })
    // 改密后后端 credential_fingerprint 立即失效旧 token,主动清登录态并跳登录页,
    // 避免下一次任意请求 401 才被动踢出(像"改密把账号搞坏了")
    ElMessage.success('密码已修改,请重新登录')
    pwdDialog.value = false
    userStore.logout()
    router.push('/login')
  } finally {
    pwdSaving.value = false
  }
}
</script>

<style scoped>
.workbench-shell { height: 100vh; min-width: 0; background: var(--wb-page, #f3f6fa); }
.workspace-container { min-width: 0; }
.aside { position: relative; display: flex; flex-direction: column; overflow-y: auto; background: var(--wb-sidebar, #122238); color: #dce8f4; box-shadow: 8px 0 24px rgb(18 34 56 / 5%); z-index: 3; }
.aside-mobile { min-height: 100%; }
.brand { display: flex; align-items: center; gap: 10px; padding: 20px 16px 12px; }
.brand-mark { display: grid; place-items: center; flex: 0 0 32px; width: 32px; height: 32px; border-radius: 10px; background: #2d7df0; color: #fff; font-size: 18px; box-shadow: 0 4px 12px rgb(45 125 240 / 30%); }
.brand-copy { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.brand-copy strong { color: #fff; font-size: 15px; letter-spacing: .03em; white-space: nowrap; }
.brand-copy small { color: #8194aa; font-size: 8px; letter-spacing: .14em; white-space: nowrap; }
.aside-caption { padding: 14px 16px 7px; color: #7388a0; font-size: 10px; letter-spacing: .08em; }
.workbench-menu { flex: 1; border-right: 0; padding: 0 9px 18px; }
.workbench-menu :deep(.el-menu-item) { position: relative; height: 39px; margin: 2px 0; padding: 0 12px !important; border-radius: 8px; color: #a8b5c5; font-size: 12px; gap: 10px; transition: background .16s ease, color .16s ease; }
.workbench-menu :deep(.el-menu-item:hover) { background: rgb(255 255 255 / 7%); color: #f7fbff; }
.workbench-menu :deep(.el-menu-item.is-active) { background: linear-gradient(90deg, #2367cb, #2c7ce8); color: #fff; font-weight: 600; box-shadow: 0 5px 12px rgb(29 104 207 / 20%); }
.workbench-menu :deep(.el-menu-item.is-active)::before { content: ''; position: absolute; left: -9px; width: 3px; height: 21px; border-radius: 0 3px 3px 0; background: #83c5ff; }
.menu-icon { flex: 0 0 16px; width: 16px; height: 16px; color: currentColor; opacity: .82; }
.workbench-menu :deep(.el-menu-item.is-active) .menu-icon { opacity: 1; }
.menu-group { padding: 17px 12px 5px; color: #6f829a; font-size: 10px; letter-spacing: .04em; }
.aside-foot { display: flex; align-items: center; gap: 8px; margin: auto 12px 14px; padding: 11px 10px; border: 1px solid rgb(255 255 255 / 8%); border-radius: 9px; color: #d5e4f2; background: rgb(255 255 255 / 4%); font-size: 11px; }
.aside-foot > span:last-child { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.aside-foot strong { font-size: 10px; font-weight: 600; }
.aside-foot small { color: #71849b; font-size: 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.health-dot { flex: 0 0 7px; width: 7px; height: 7px; border-radius: 50%; background: #8797aa; box-shadow: 0 0 0 4px rgb(135 151 170 / 12%); }
.health-dot.health-ok { background: #42c88a; box-shadow: 0 0 0 4px rgb(66 200 138 / 12%); }
.health-dot.health-degraded { background: #e8a63f; box-shadow: 0 0 0 4px rgb(232 166 63 / 12%); }
.header { display: flex; align-items: center; gap: 18px; height: 70px; padding: 0 26px; border-bottom: 1px solid var(--wb-line, #e4eaf1); background: rgb(255 255 255 / 92%); backdrop-filter: blur(12px); }
.header-left { display: flex; align-items: center; flex: 0 0 auto; gap: 12px; min-width: 154px; }
.page-heading { display: flex; flex-direction: column; gap: 2px; }
.page-heading .eyebrow { color: #9aa9ba; font-size: 8px; letter-spacing: .13em; }
.page-heading strong { color: #1b2c40; font-size: 15px; white-space: nowrap; }
.header-search { display: flex; align-items: center; flex: 1 1 300px; max-width: 520px; min-width: 150px; height: 38px; padding: 0 10px 0 12px; border: 1px solid #dfe7f0; border-radius: 10px; background: #f7f9fc; color: #8091a5; transition: border .16s ease, box-shadow .16s ease, background .16s ease; }
.header-search:focus-within { border-color: #77aef0; background: #fff; box-shadow: 0 0 0 3px rgb(45 125 240 / 10%); }
.header-search-icon { display: grid; place-items: center; flex: 0 0 22px; padding: 0; margin-right: 6px; border: 0; color: #8091a5; background: transparent; cursor: pointer; }
.header-search-icon:focus-visible { outline: 2px solid #4c91e6; outline-offset: 2px; border-radius: 4px; }
.header-search :deep(.el-autocomplete) { flex: 1; min-width: 0; }
.header-search :deep(.el-input__wrapper) { padding: 0; border-radius: 0; background: transparent; box-shadow: none !important; }
.header-search :deep(.el-input__inner) { height: 34px; color: #23364b; font-size: 12px; }
.header-search kbd { flex: 0 0 auto; padding: 2px 5px; border: 1px solid #d7e0e9; border-radius: 4px; color: #93a2b3; background: #fff; font-size: 9px; font-family: inherit; }
.header-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; min-width: 0; margin-left: auto; }
.date-chip, .market-chip { display: inline-flex; align-items: center; gap: 6px; color: #5f7082; font-size: 11px; white-space: nowrap; }
.date-chip .el-icon { color: #7e91a5; font-size: 15px; }
.market-chip { padding: 6px 9px; border-radius: 999px; background: #f0f5fa; color: #617487; font-weight: 600; }
.market-chip.market-live { color: #18835a; background: #eaf8f1; }
.market-chip.market-closed { color: #66788a; background: #f0f3f6; }
.market-chip.market-pause { color: #99681a; background: #fff6df; }
.market-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.market-live .market-dot { box-shadow: 0 0 0 3px rgb(24 131 90 / 12%); }
.refresh-button { color: #50657a !important; font-size: 12px; }
.refresh-button:hover { color: #1769d1 !important; background: #eef5ff !important; }
.user-chip { display: inline-flex; align-items: center; gap: 8px; padding: 3px 0 3px 3px; border: 0; background: transparent; color: #26384c; cursor: pointer; text-align: left; }
.user-chip:focus-visible { outline: 2px solid #6ba7ed; outline-offset: 3px; border-radius: 7px; }
.avatar { display: grid; place-items: center; width: 31px; height: 31px; border-radius: 9px; color: #2569bf; background: #e8f1fc; }
.user-meta { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.user-meta strong { max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; font-weight: 600; }
.user-meta small { color: #8a9aab; font-size: 9px; }
.user-chevron { color: #8b9bac; font-size: 12px; }
.main { min-width: 0; background: var(--wb-page, #f3f6fa); padding: 20px 24px 28px; overflow-y: auto; }
.m-burger { padding: 4px !important; color: #29425e !important; }
:deep(.el-drawer) { border: 0; }
:deep(.m-drawer .el-drawer__body) { padding: 0; background: var(--wb-sidebar, #122238); }
@media (max-width: 1100px) {
  .header { padding: 0 16px; gap: 12px; }
  .header-actions { gap: 8px; }
  .date-chip { display: none; }
  .main { padding: 16px; }
}
@media (max-width: 768px) {
  .header { height: 60px; padding: 0 12px; }
  .header-left { min-width: auto; }
  .page-heading .eyebrow { display: none; }
  .page-heading strong { font-size: 14px; }
  .header-search { position: relative; flex-basis: 36px; flex-grow: 0; width: 36px; min-width: 36px; padding: 0; justify-content: center; border-color: transparent; background: transparent; }
  .header-search-icon { flex-basis: 36px; margin: 0; }
  .header-search :deep(.el-autocomplete), .header-search kbd { display: none; }
  .header-search.mobile-search-open { position: absolute; left: 12px; right: 12px; top: 11px; z-index: 5; width: auto; min-width: 0; height: 38px; padding: 0 10px 0 12px; justify-content: initial; border-color: #77aef0; background: #fff; box-shadow: 0 0 0 3px rgb(45 125 240 / 10%), 0 8px 24px rgb(31 58 89 / 12%); }
  .header-search.mobile-search-open .header-search-icon { flex-basis: 22px; margin-right: 6px; }
  .header-search.mobile-search-open :deep(.el-autocomplete) { display: block; flex: 1; min-width: 0; }
  .header-actions { gap: 5px; }
  .refresh-button { padding: 5px !important; }
  .refresh-label { display: none; }
  .market-chip { padding: 5px 7px; font-size: 10px; }
  .user-meta, .user-chevron { display: none; }
  .main { padding: 10px !important; }
}
</style>
