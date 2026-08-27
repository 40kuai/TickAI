import axios from 'axios'
import router from '@/router'

// 创建 Axios 实例
// withCredentials: true 保证携带 HttpOnly Cookie，维持登录会话
const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 响应拦截器：统一处理 401 未授权
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      // 未登录或会话过期，清除本地用户状态并跳转登录页
      // 避免在登录页自身重复跳转
      const currentPath = router.currentRoute.value.path
      if (currentPath !== '/login') {
        router.replace({
          path: '/login',
          query: { redirect: currentPath }
        })
      }
    }
    return Promise.reject(error)
  }
)

export default api
