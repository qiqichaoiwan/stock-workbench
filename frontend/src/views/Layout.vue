<template>
  <el-container style="height:100vh">
    <el-aside v-if="!isMobile" width="220px" class="aside">
      <div class="logo">📈 智能选股工作台</div>
      <el-menu :default-active="activePath" router background-color="#1d2530" text-color="#c0c8d2"
               active-text-color="#409eff">
        <template v-for="group in visibleGroups" :key="group.label">
          <div class="menu-group">{{ group.label }}</div>
          <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
            {{ item.title }}
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-drawer v-else v-model="drawerOpen" direction="ltr" :size="230" :with-header="false"
               class="m-drawer">
      <div class="aside" style="height:100%">
        <div class="logo">📈 智能选股工作台</div>
        <el-menu :default-active="activePath" router background-color="#1d2530" text-color="#c0c8d2"
                 active-text-color="#409eff" @select="drawerOpen = false">
          <template v-for="group in visibleGroups" :key="group.label">
            <div class="menu-group">{{ group.label }}</div>
            <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
              {{ item.title }}
            </el-menu-item>
          </template>
        </el-menu>
      </div>
    </el-drawer>

    <el-container>
      <el-header class="header">
        <el-button v-if="isMobile" class="m-burger" link @click="drawerOpen = true">
          <el-icon :size="22"><Menu /></el-icon>
        </el-button>
        <span class="title">{{ headerTitle }}</span>
        <span class="spacer" />
        <el-autocomplete v-if="!isMobile" v-model="stockQ" :fetch-suggestions="fetchStocks"
                         placeholder="搜股票代码/名称/拼音" clearable style="width:240px"
                         value-key="label" @select="onPickStock" />
        <el-dropdown @command="onCommand">
          <span class="user-chip">
            <el-icon style="margin-right:6px"><User /></el-icon>
            <span class="user-name">{{ userStore.user?.nickname || userStore.user?.username }}</span>
            <el-tag size="small" :type="roleTagType" style="margin-left:8px">{{ roleLabel }}</el-tag>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="password">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
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
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Menu, User } from '@element-plus/icons-vue'
import api from '../api'
import { useUserStore } from '../store'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

// 移动端判定(<=768px):侧边栏改抽屉
const isMobile = ref(window.matchMedia('(max-width: 768px)').matches)
const _mq = window.matchMedia('(max-width: 768px)')
const _onMq = (e) => { isMobile.value = e.matches }
if (_mq.addEventListener) _mq.addEventListener('change', _onMq)
onUnmounted(() => { if (_mq.removeEventListener) _mq.removeEventListener('change', _onMq) })

const drawerOpen = ref(false)

// 静态菜单配置(roles 为空 = 所有登录角色可见)
const menuGroups = [
  {
    label: '智能选股工作台',
    items: [
      { path: '/dashboard', title: '仪表盘' },
      { path: '/market', title: '大盘指数' },
      { path: '/dual-core', title: '双核共振' },
      { path: '/pool', title: '选股池' },
      { path: '/lhb', title: '龙虎榜' },
      { path: '/industries', title: '行业与赛道' },
      { path: '/signals', title: '技术信号' },
      { path: '/news', title: '新闻甄别' },
      { path: '/strategy', title: '策略配置', roles: ['admin', 'analyst'] },
      { path: '/cards', title: '策略卡', roles: ['admin', 'analyst'] },
      { path: '/tasks', title: '任务中心', roles: ['admin', 'analyst'] },
    ],
  },
  {
    label: '持仓工作台',
    items: [
      { path: '/portfolio', title: '我的持仓' },
      { path: '/watchlist', title: '自选预警' },
      { path: '/trades', title: '交易历史' },
      { path: '/reports', title: '报告中心' },
      { path: '/settings', title: 'LLM 设置' },
    ],
  },
  {
    label: '系统管理',
    roles: ['admin'],
    items: [{ path: '/users', title: '用户与权限' }],
  },
  {
    label: '使用帮助',
    items: [{ path: '/help', title: '使用帮助' }],
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

const roleLabel = computed(
  () => ({ admin: '管理员', analyst: '策略员', user: '普通用户' })[userStore.role] || userStore.role)
const roleTagType = computed(
  () => ({ admin: 'danger', analyst: 'warning', user: 'info' })[userStore.role] || 'info')

const pwdDialog = ref(false)
const pwdSaving = ref(false)
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })

onMounted(() => { userStore.fetchMe() })

// 全局搜股:选中后跳 /pool + localStorage 透传,Pool.onMounted 读取填 search
const stockQ = ref('')
// 防抖 300ms:避免每敲一个字符就打一次 /search(后端拼音索引有缓存但仍省请求)
let _searchTimer = null
function fetchStocks(q, cb) {
  if (_searchTimer) clearTimeout(_searchTimer)
  if (!q?.trim()) return cb([])
  _searchTimer = setTimeout(async () => {
    try {
      const data = await api.get('/api/screener/search',
        { params: { q: q.trim(), limit: 20 }, silent: true }).catch(() => [])
      const list = (Array.isArray(data) ? data : []).map((s) => ({
        ...s, label: `${s.code} ${s.name || ''}`.trim(), value: `${s.code} ${s.name || ''}`.trim(),
      }))
      cb(list)
    } catch { cb([]) }
  }, 300)
}
function onPickStock(item) {
  if (!item?.code) return
  stockQ.value = ''
  ElMessage.success(`已定位 ${item.code} ${item.name || ''}`.trim())
  // 用路由 query 透传,避免 location.reload() 整页刷新丢失其它页面状态
  const query = { q: item.code }
  // 已在池页且是同一代码时补时间戳,让 Pool 的 query 监听重新触发定位
  if (route.path === '/pool' && route.query.q === item.code) query.t = String(Date.now())
  router.push({ path: '/pool', query })
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
.aside { background: #1d2530; overflow-y: auto; }
.logo { color: #fff; font-weight: 700; padding: 18px 16px; font-size: 16px; letter-spacing: 1px; }
.menu-group { color: #6b7480; font-size: 11px; padding: 12px 16px 4px; }
.header { background: #fff; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid #ebeef5; }
.title { font-weight: 600; }
.spacer { flex: 1; }
.user-chip { display: inline-flex; align-items: center; cursor: pointer; font-size: 13px; color: #303133; outline: none; }
.main { background: #f5f7fa; padding: 16px; overflow-y: auto; }
.m-burger { margin-left: -8px; }
@media (max-width: 768px) {
  .header { padding: 0 10px; }
}
</style>
