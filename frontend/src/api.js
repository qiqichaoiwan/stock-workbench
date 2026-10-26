import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({ baseURL: '/', timeout: 120000 })

api.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

api.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      if (!location.hash.includes('/login')) location.hash = '#/login'
    }
    if (err.config?.silent) return Promise.reject(err) // 静默请求仍须处理登录失效
    ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    return Promise.reject(err)
  },
)

export default api
