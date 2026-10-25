import { createRouter, createWebHashHistory } from 'vue-router'

// roles 为空 = 登录即可访问
const routes = [
  { path: '/login', component: () => import('./views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('./views/Layout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', component: () => import('./views/Dashboard.vue'), meta: { group: 'screener', title: '仪表盘' } },
      { path: 'pool', component: () => import('./views/Pool.vue'), meta: { group: 'screener', title: '选股池' } },
      { path: 'industries', component: () => import('./views/Industries.vue'), meta: { group: 'screener', title: '行业与赛道' } },
      { path: 'signals', component: () => import('./views/Signals.vue'), meta: { group: 'screener', title: '技术信号' } },
      { path: 'news', component: () => import('./views/News.vue'), meta: { group: 'screener', title: '新闻甄别' } },
      { path: 'strategy', component: () => import('./views/Strategy.vue'), meta: { group: 'screener', title: '策略配置', roles: ['admin', 'analyst'] } },
      { path: 'cards', component: () => import('./views/Cards.vue'), meta: { group: 'screener', title: '策略卡', roles: ['admin', 'analyst'] } },
      { path: 'tasks', component: () => import('./views/Tasks.vue'), meta: { group: 'screener', title: '任务中心', roles: ['admin', 'analyst'] } },
      { path: 'portfolio', component: () => import('./views/Portfolio.vue'), meta: { group: 'portfolio', title: '我的持仓' } },
      { path: 'trades', component: () => import('./views/Trades.vue'), meta: { group: 'portfolio', title: '交易历史' } },
      { path: 'reports', component: () => import('./views/Reports.vue'), meta: { group: 'portfolio', title: '报告中心' } },
      { path: 'settings', component: () => import('./views/Settings.vue'), meta: { group: 'portfolio', title: 'LLM 设置' } },
      { path: 'users', component: () => import('./views/Users.vue'), meta: { group: 'system', title: '用户与权限', roles: ['admin'] } },
    ],
  },
]

const router = createRouter({ history: createWebHashHistory(), routes })

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (!to.meta.public && !token) return '/login'
  // 角色路由守卫(菜单已过滤,这里防手输 URL)
  if (to.meta.roles?.length) {
    const user = JSON.parse(localStorage.getItem('user') || 'null')
    const role = user?.role || 'user'
    if (!to.meta.roles.includes(role)) return '/dashboard'
  }
  return true
})

export default router
