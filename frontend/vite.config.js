import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // dev 模式下前端请求转发到后端,无需改 baseURL
      '/api': { target: 'http://127.0.0.1:8899', changeOrigin: true },
    },
  },
})
