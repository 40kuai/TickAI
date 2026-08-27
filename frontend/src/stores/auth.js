import { defineStore } from 'pinia'
import api from '@/api'

// 认证状态管理
export const useAuthStore = defineStore('auth', {
  state: () => ({
    // 当前登录用户信息
    user: null,
    // 是否已发起过 fetchUser 请求（用于路由守卫判断）
    initialized: false
  }),

  getters: {
    // 是否已认证
    isAuthenticated: (state) => !!state.user
  },

  actions: {
    // 登录
    async login(username, password) {
      const res = await api.post('/auth/login', { username, password })
      this.user = res.data
      return res.data
    },

    // 退出登录
    async logout() {
      try {
        await api.post('/auth/logout')
      } finally {
        // 无论后端是否成功响应，前端都清除本地状态
        this.user = null
      }
    },

    // 获取当前用户信息（用于刷新页面后恢复会话）
    async fetchUser() {
      try {
        const res = await api.get('/auth/me')
        this.user = res.data
        return res.data
      } catch (err) {
        // 401 或其他错误，视为未登录
        this.user = null
        return null
      } finally {
        this.initialized = true
      }
    }
  }
})
