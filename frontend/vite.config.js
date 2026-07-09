import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Vite 配置：开发服务器代理 /api 到后端 FastAPI
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5173,
    proxy: {
      // 所有 /api 请求代理到后端，保证 Cookie 同源
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
})
