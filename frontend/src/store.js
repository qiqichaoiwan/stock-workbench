import { defineStore } from 'pinia'
import api from './api'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: JSON.parse(localStorage.getItem('user') || 'null'),
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    role: (s) => s.user?.role || 'user',
    isAdmin: (s) => s.user?.role === 'admin',
    // 选股工作台写操作(任务/参数/策略卡/赛道)
    isAnalyst: (s) => ['admin', 'analyst'].includes(s.user?.role),
  },
  actions: {
    async login(username, password) {
      const data = await api.post('/api/auth/login', { username, password })
      this.token = data.token
      this.user = data.user
      localStorage.setItem('token', data.token)
      localStorage.setItem('user', JSON.stringify(data.user))
    },
    // 启动/登录后刷新角色等信息(管理员改了角色后这里拿到最新值)
    async fetchMe() {
      if (!this.token) return
      try {
        const me = await api.get('/api/auth/me')
        this.user = { ...this.user, ...me }
        localStorage.setItem('user', JSON.stringify(this.user))
      } catch { /* 401 已由拦截器处理 */ }
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
      localStorage.removeItem('user')
    },
  },
})
