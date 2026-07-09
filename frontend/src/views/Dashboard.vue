<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const stats = ref({
  total: 0,
  success: 0,
  failed: 0,
  successRate: 0
})
const runs = ref([])
const loading = ref(false)
const errorMsg = ref('')

// 计算成功率
function calcRate(total, success) {
  if (!total) return 0
  return Math.round((success / total) * 100)
}

// 格式化时间
function formatTime(t) {
  if (!t) return '-'
  try {
    const d = new Date(t)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return t
  }
}

// 状态徽章类型
function statusBadge(status) {
  const s = String(status || '').toLowerCase()
  if (s === 'success' || s === 'succeeded') return 'badge-success'
  if (s === 'running' || s === 'pending') return 'badge-info'
  if (s === 'failed' || s === 'error') return 'badge-danger'
  return 'badge-gray'
}

// 统计数字卡片
const statCards = computed(() => [
  { label: '运行总次数', value: stats.value.total, icon: '⟳', tone: 'primary' },
  { label: '成功次数', value: stats.value.success, icon: '✓', tone: 'success' },
  { label: '失败次数', value: stats.value.failed, icon: '✕', tone: 'danger' },
  { label: '成功率', value: stats.value.successRate + '%', icon: '%', tone: 'info' }
])

// 加载历史数据用于首页统计
async function loadData() {
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await api.get('/history', { params: { limit: 100 } })
    const data = res.data || {}
    const list = data.runs || []
    const total = data.count ?? list.length
    const success = list.filter((r) =>
      ['success', 'succeeded'].includes(String(r.status || '').toLowerCase())
    ).length
    const failed = list.filter((r) =>
      ['failed', 'error'].includes(String(r.status || '').toLowerCase())
    ).length
    stats.value = {
      total,
      success,
      failed,
      successRate: calcRate(total, success)
    }
    runs.value = list.slice(0, 8)
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '加载数据失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

<template>
  <div class="dashboard">
    <!-- 欢迎卡片 -->
    <div class="welcome-card">
      <div class="welcome-text">
        <h2>欢迎回来，{{ auth.user?.username || '用户' }} 👋</h2>
        <p>这里是你的运维概览，可快速查看运行状态与最近记录。</p>
      </div>
      <div class="welcome-actions">
        <button class="btn btn-ghost" @click="router.push('/chat')">开始 AI 对话</button>
        <button class="btn btn-outline" @click="router.push('/servers')">管理服务器</button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="stat-grid">
      <div
        v-for="card in statCards"
        :key="card.label"
        class="stat-card"
        :class="'tone-' + card.tone"
      >
        <div class="stat-icon">{{ card.icon }}</div>
        <div class="stat-body">
          <div class="stat-value">{{ card.value }}</div>
          <div class="stat-label">{{ card.label }}</div>
        </div>
      </div>
    </div>

    <!-- 最近运行记录 -->
    <div class="card recent-card">
      <div class="card-head">
        <h3 class="card-title">最近运行记录</h3>
        <button class="btn btn-outline btn-sm" @click="router.push('/history')">
          查看全部
        </button>
      </div>

      <div v-if="loading" class="state-tip">加载中…</div>
      <div v-else-if="errorMsg" class="state-tip error">{{ errorMsg }}</div>
      <div v-else-if="!runs.length" class="state-tip">暂无运行记录</div>

      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>时间</th>
              <th>服务器</th>
              <th>状态</th>
              <th>运行内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in runs" :key="r.id ?? i">
              <td>{{ formatTime(r.created_at || r.started_at || r.timestamp) }}</td>
              <td>{{ r.server_name || r.server || '-' }}</td>
              <td>
                <span class="badge" :class="statusBadge(r.status)">{{ r.status || '-' }}</span>
              </td>
              <td class="col-content">{{ r.query || r.message || r.task || '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-lg);
}

/* 欢迎卡片 */
.welcome-card {
  background: var(--gradient-primary);
  color: #fff;
  border-radius: var(--radius-md);
  padding: 28px 30px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
  box-shadow: var(--shadow-md);
}
.welcome-text h2 {
  font-size: 20px;
  margin-bottom: 6px;
}
.welcome-text p {
  font-size: 13px;
  opacity: 0.9;
}
.welcome-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.welcome-actions .btn-ghost {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}
.welcome-actions .btn-ghost:hover {
  background: rgba(255, 255, 255, 0.3);
}
.welcome-actions .btn-outline {
  background: transparent;
  color: #fff;
  border-color: rgba(255, 255, 255, 0.5);
}
.welcome-actions .btn-outline:hover {
  border-color: #fff;
}

/* 统计卡片 */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--spacing-md);
}
.stat-card {
  background: #fff;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
}
.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 600;
}
.tone-primary .stat-icon { background: rgba(102, 126, 234, 0.12); color: var(--color-primary); }
.tone-success .stat-icon { background: rgba(16, 185, 129, 0.12); color: var(--color-success); }
.tone-danger .stat-icon { background: rgba(239, 68, 68, 0.12); color: var(--color-danger); }
.tone-info .stat-icon { background: rgba(59, 130, 246, 0.12); color: var(--color-info); }
.stat-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}
.stat-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

/* 最近记录 */
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
}
.state-tip {
  padding: 30px 0;
  text-align: center;
  color: var(--color-text-secondary);
}
.state-tip.error {
  color: var(--color-danger);
}
.table-wrap {
  overflow-x: auto;
}
.col-content {
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-secondary);
}
</style>
