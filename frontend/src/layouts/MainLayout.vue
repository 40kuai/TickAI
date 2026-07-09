<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// 移动端侧边栏折叠状态
const sidebarOpen = ref(false)

// 导航菜单
const menuItems = [
  { name: 'dashboard', path: '/', label: '首页', icon: '⌂' },
  { name: 'servers', path: '/servers', label: '服务器', icon: '▢' },
  { name: 'chat', path: '/chat', label: 'AI 对话', icon: '✦' },
  { name: 'history', path: '/history', label: '历史记录', icon: '⟳' },
  { name: 'tools', path: '/tools', label: '工具', icon: '⚒' }
]

// 当前激活菜单
const activeName = computed(() => route.name)

// 当前用户名
const username = computed(() => auth.user?.username || '用户')

// 格式化创建时间
const createdAt = computed(() => {
  if (!auth.user?.created_at) return ''
  try {
    return new Date(auth.user.created_at).toLocaleDateString('zh-CN')
  } catch {
    return ''
  }
})

// 点击菜单项
function go(path) {
  router.push(path)
  sidebarOpen.value = false
}

// 退出登录
async function handleLogout() {
  await auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="layout">
    <!-- 移动端遮罩 -->
    <div
      v-if="sidebarOpen"
      class="overlay"
      @click="sidebarOpen = false"
    ></div>

    <!-- 侧边栏 -->
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="logo">
        <span class="logo-icon">◆</span>
        <span class="logo-text">运维平台</span>
      </div>

      <nav class="menu">
        <a
          v-for="item in menuItems"
          :key="item.name"
          class="menu-item"
          :class="{ active: activeName === item.name }"
          @click="go(item.path)"
        >
          <span class="menu-icon">{{ item.icon }}</span>
          <span class="menu-label">{{ item.label }}</span>
        </a>
      </nav>

      <div class="sidebar-footer">
        <div class="user-card">
          <div class="avatar">{{ username.charAt(0).toUpperCase() }}</div>
          <div class="user-info">
            <div class="user-name">{{ username }}</div>
            <div class="user-meta" v-if="createdAt">注册于 {{ createdAt }}</div>
          </div>
        </div>
        <button class="btn btn-ghost btn-sm logout-btn" @click="handleLogout">
          退出登录
        </button>
      </div>
    </aside>

    <!-- 主内容区 -->
    <div class="main">
      <!-- 顶部栏（移动端可见） -->
      <header class="topbar">
        <button class="toggle-btn" @click="sidebarOpen = !sidebarOpen">
          ☰
        </button>
        <span class="topbar-title">{{ route.meta.title || '运维平台' }}</span>
      </header>

      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100%;
}

/* 侧边栏 */
.sidebar {
  width: 240px;
  background: var(--color-sidebar);
  color: var(--color-text-inverse);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: transform 0.25s ease;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 22px;
  font-size: 18px;
  font-weight: 600;
}
.logo-icon {
  background: var(--gradient-primary);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.menu {
  flex: 1;
  padding: 12px;
  overflow-y: auto;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  border-radius: var(--radius-sm);
  color: rgba(248, 250, 252, 0.7);
  cursor: pointer;
  transition: var(--transition);
  margin-bottom: 4px;
}
.menu-item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.menu-item.active {
  background: var(--gradient-primary);
  color: #fff;
  box-shadow: var(--shadow-md);
}
.menu-icon {
  width: 20px;
  text-align: center;
  font-size: 16px;
}
.menu-label {
  font-size: 14px;
}

.sidebar-footer {
  padding: 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.user-card {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.avatar {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-full);
  background: var(--gradient-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 15px;
}
.user-info {
  flex: 1;
  min-width: 0;
}
.user-name {
  font-size: 14px;
  font-weight: 500;
}
.user-meta {
  font-size: 11px;
  color: rgba(248, 250, 252, 0.5);
}
.logout-btn {
  width: 100%;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.logout-btn:hover {
  background: rgba(239, 68, 68, 0.3);
}

/* 主区 */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.topbar {
  display: none;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  background: #fff;
  border-bottom: 1px solid var(--color-border);
}
.toggle-btn {
  font-size: 20px;
  color: var(--color-text);
  padding: 4px 8px;
}
.topbar-title {
  font-weight: 600;
}
.content {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-lg);
}

/* 遮罩 */
.overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.4);
  z-index: 40;
}

/* 响应式：移动端折叠侧边栏 */
@media (max-width: 768px) {
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    z-index: 50;
    transform: translateX(-100%);
  }
  .sidebar.open {
    transform: translateX(0);
  }
  .topbar {
    display: flex;
  }
  .overlay {
    display: block;
  }
  .content {
    padding: var(--spacing-md);
  }
}
</style>
