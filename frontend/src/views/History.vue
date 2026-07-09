<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import api from '@/api'

const runs = ref([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref('')

// 筛选条件
const filters = reactive({
  server_name: '',
  status: ''
})

// 分页（前端分页，因为后端按 limit 返回）
const page = ref(1)
const pageSize = ref(15)

const totalPages = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize.value))
)

const pagedRuns = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return runs.value.slice(start, start + pageSize.value)
})

// 状态徽章
function statusBadge(status) {
  const s = String(status || '').toLowerCase()
  if (s === 'success' || s === 'succeeded') return 'badge-success'
  if (s === 'running' || s === 'pending') return 'badge-info'
  if (s === 'failed' || s === 'error') return 'badge-danger'
  return 'badge-gray'
}

// 格式化时间
function formatTime(t) {
  if (!t) return '-'
  try {
    return new Date(t).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return t
  }
}

// 加载数据
async function loadData() {
  loading.value = true
  errorMsg.value = ''
  try {
    const params = { limit: 100 }
    if (filters.server_name) params.server_name = filters.server_name
    if (filters.status) params.status = filters.status
    const res = await api.get('/history', { params })
    const data = res.data || {}
    runs.value = data.runs || []
    total.value = data.count ?? runs.value.length
    page.value = 1
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '加载历史记录失败'
  } finally {
    loading.value = false
  }
}

function applyFilter() {
  loadData()
}

function resetFilter() {
  filters.server_name = ''
  filters.status = ''
  loadData()
}

function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
}

onMounted(loadData)
</script>

<template>
  <div class="history-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">历史记录</h2>
        <p class="page-sub">查看所有运行历史与执行结果</p>
      </div>
      <button class="btn btn-outline" @click="loadData">刷新</button>
    </div>

    <!-- 筛选栏 -->
    <div class="card filter-bar">
      <div class="filter-group">
        <label class="form-label">服务器</label>
        <input
          v-model="filters.server_name"
          class="form-input"
          placeholder="按服务器名称筛选"
          @keyup.enter="applyFilter"
        />
      </div>
      <div class="filter-group">
        <label class="form-label">状态</label>
        <select v-model="filters.status" class="form-select">
          <option value="">全部</option>
          <option value="success">成功</option>
          <option value="failed">失败</option>
          <option value="running">运行中</option>
        </select>
      </div>
      <div class="filter-actions">
        <button class="btn btn-primary" @click="applyFilter">查询</button>
        <button class="btn btn-ghost" @click="resetFilter">重置</button>
      </div>
    </div>

    <!-- 表格 -->
    <div class="card">
      <div v-if="loading" class="state-tip">加载中…</div>
      <div v-else-if="errorMsg" class="state-tip error">{{ errorMsg }}</div>
      <div v-else-if="!runs.length" class="state-tip">暂无历史记录</div>
      <div v-else>
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>时间</th>
                <th>服务器</th>
                <th>状态</th>
                <th>运行内容</th>
                <th>耗时</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in pagedRuns" :key="r.id ?? i">
                <td>{{ formatTime(r.created_at || r.started_at || r.timestamp) }}</td>
                <td>{{ r.server_name || r.server || '-' }}</td>
                <td>
                  <span class="badge" :class="statusBadge(r.status)">{{ r.status || '-' }}</span>
                </td>
                <td class="col-content" :title="r.query || r.message || r.task || ''">
                  {{ r.query || r.message || r.task || '-' }}
                </td>
                <td>{{ r.duration || r.elapsed || '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 分页 -->
        <div class="pagination">
          <span class="page-info">共 {{ total }} 条，第 {{ page }} / {{ totalPages }} 页</span>
          <div class="page-btns">
            <button class="btn btn-outline btn-sm" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
            <button class="btn btn-outline btn-sm" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.history-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-lg);
}
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.page-title {
  font-size: 20px;
  font-weight: 600;
}
.page-sub {
  margin-top: 4px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.filter-bar {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
  padding: 20px;
}
.filter-group {
  flex: 1;
  min-width: 180px;
}
.filter-actions {
  display: flex;
  gap: 8px;
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

.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  flex-wrap: wrap;
  gap: 12px;
}
.page-info {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.page-btns {
  display: flex;
  gap: 8px;
}
</style>
