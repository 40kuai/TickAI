import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true, title: '登录' }
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      {
        path: '',
        name: 'dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '首页' }
      },
      {
        path: 'servers',
        name: 'servers',
        component: () => import('@/views/Servers.vue'),
        meta: { title: '服务器管理' }
      },
      {
        path: 'chat',
        name: 'chat',
        component: () => import('@/views/Chat.vue'),
        meta: { title: 'AI 对话' }
      },
      {
        path: 'history',
        name: 'history',
        component: () => import('@/views/History.vue'),
        meta: { title: '历史记录' }
      },
      {
        path: 'tools',
        name: 'tools',
        component: () => import('@/views/Tools.vue'),
        meta: { title: '工具浏览' }
      }
    ]
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/'
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  }
})

// 全局前置守卫：未登录时跳转 /login
router.beforeEach(async (to) => {
  const auth = useAuthStore()

  // 首次进入时尝试恢复会话
  if (!auth.initialized) {
    await auth.fetchUser()
  }

  if (!auth.isAuthenticated && !to.meta.public) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  // 已登录用户访问登录页，直接跳转首页
  if (auth.isAuthenticated && to.name === 'login') {
    return { name: 'dashboard' }
  }

  return true
})

// 设置页面标题
router.afterEach((to) => {
  const title = to.meta.title
  document.title = title ? `${title} - 运维管理平台` : '运维管理平台'
})

export default router
