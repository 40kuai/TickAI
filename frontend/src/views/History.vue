<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import api from '@/api'

const runs = ref([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref('')

// 详情弹窗
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

// 筛选条件
const filters = reactive({
  server_name: '',
  status: ''
})

// 分页
const page = ref(1)
const pageSize = ref(15)

const totalPages = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize.value))
)

const pagedRuns = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return runs.value.slice(start, start + pageSize.value)
})

// 工具名映射
const TOOL_LABELS = {
  ldap_search_user: 'LDAP 查询',
  check_disk_usage: '磁盘检查',
  check_resources_on_server: '资源检查',
  list_services_on_server: '服务列表',
  check_resources: '资源检查',
  list_services: '服务列表',
  list_servers: '服务器列表',
  query_runs: '历史查询',
  df: '磁盘检查',
  check_resources: '资源检查',
  list_services: '服务列表',
}

function toolLabel(cmd) {
  return TOOL_LABELS[cmd] || cmd || '-'
}

// 状态映射
const STATUS_LABELS = {
  success: '成功',
  failed: '失败',
  ssh_error: 'SSH错误',
  timeout: '超时',
  running: '运行中',
  pending: '等待中',
}

function statusLabel(s) {
  return STATUS_LABELS[s] || s || '-'
}

// 触发来源映射
const TRIGGER_LABELS = {
  user_button: '手动操作',
  llm_tool_call: 'AI 对话',
}

function triggerLabel(t) {
  return TRIGGER_LABELS[t] || t || '-'
}

// 状态徽章
function statusBadge(status) {
  const s = String(status || '').toLowerCase()
  if (s === 'success' || s === 'succeeded') return 'badge-success'
  if (s === 'running' || s === 'pending') return 'badge-info'
  if (s === 'failed' || s === 'error' || s === 'ssh_error') return 'badge-danger'
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

// 格式化耗时
function formatDuration(ms) {
  if (ms == null) return '-'
  if (ms < 1000) return ms + 'ms'
  return (ms / 1000).toFixed(1) + 's'
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

// 查看详情
async function viewDetail(id) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    const res = await api.get(`/history/${id}`)
    detail.value = res.data
  } catch (err) {
    detail.value = { error: err.response?.data?.detail || '加载失败' }
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detailVisible.value = false
  detail.value = null
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

// 格式化 JSON 结果用于展示
function formatResult(data) {
  if (!data) return ''
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
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
          <option value="ssh_error">SSH错误</option>
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
                <th>类型</th>
                <th>服务器</th>
                <th>状态</th>
                <th>来源</th>
                <th>耗时</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(r, i) in pagedRuns"
                :key="r.id ?? i"
                class="clickable-row"
                @click="viewDetail(r.id)"
              >
                <td class="col-time">{{ formatTime(r.started_at) }}</td>
                <td>
                  <span class="tool-tag">{{ toolLabel(r.command) }}</span>
                </td>
                <td>{{ r.server_name || '-' }}</td>
                <td>
                  <span class="badge" :class="statusBadge(r.status)">{{ statusLabel(r.status) }}</span>
                </td>
                <td class="col-trigger">{{ triggerLabel(r.triggered_by) }}</td>
                <td class="col-duration">{{ formatDuration(r.duration_ms) }}</td>
                <td class="col-action">
                  <span class="detail-link">详情</span>
                </td>
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

    <!-- 详情弹窗 -->
    <div v-if="detailVisible" class="modal-overlay" @click.self="closeDetail">
      <div class="modal-box detail-modal">
        <div class="modal-header">
          <h3>执行详情</h3>
          <button class="modal-close" @click="closeDetail">×</button>
        </div>
        <div class="modal-body">
          <div v-if="detailLoading" class="state-tip">加载中…</div>
          <div v-else-if="detail" class="detail-content">
            <!-- 基本信息 -->
            <div class="detail-section">
              <div class="detail-grid">
                <div class="detail-item">
                  <span class="detail-label">类型</span>
                  <span class="detail-value">
                    <span class="tool-tag">{{ toolLabel(detail.command) }}</span>
                  </span>
                </div>
                <div class="detail-item">
                  <span class="detail-label">状态</span>
                  <span class="detail-value">
                    <span class="badge" :class="statusBadge(detail.status)">{{ statusLabel(detail.status) }}</span>
                  </span>
                </div>
                <div class="detail-item">
                  <span class="detail-label">服务器</span>
                  <span class="detail-value">
                    {{ detail.server_name || '-' }}
                    <span v-if="detail.server_host" class="text-light">({{ detail.server_host }})</span>
                  </span>
                </div>
                <div class="detail-item">
                  <span class="detail-label">来源</span>
                  <span class="detail-value">{{ triggerLabel(detail.triggered_by) }}</span>
                </div>
                <div class="detail-item">
                  <span class="detail-label">开始时间</span>
                  <span class="detail-value">{{ formatTime(detail.started_at) }}</span>
                </div>
                <div class="detail-item">
                  <span class="detail-label">耗时</span>
                  <span class="detail-value">{{ formatDuration(detail.duration_ms) }}</span>
                </div>
              </div>
            </div>

            <!-- 错误信息 -->
            <div v-if="detail.stderr" class="detail-section">
              <h4 class="detail-section-title">错误信息</h4>
              <pre class="detail-code error-code">{{ detail.stderr }}</pre>
            </div>

            <!-- 执行结果 -->
            <div v-if="detail.result" class="detail-section">
              <h4 class="detail-section-title">执行结果</h4>
              <!-- LDAP 用户信息特殊渲染 -->
              <div v-if="detail.result.users && detail.result.users.length" class="result-users">
                <div v-for="u in detail.result.users" :key="u.dn || u.cn" class="user-card">
                  <div class="user-card-header">
                    <span class="user-name">{{ u.cn || u.name || '-' }}</span>
                    <span v-if="u.title" class="user-title">{{ u.title }}</span>
                  </div>
                  <div class="user-card-body">
                    <div v-if="u.mail" class="user-field"><span class="uf-label">邮箱</span><span class="uf-value">{{ u.mail }}</span></div>
                    <div v-if="u.department" class="user-field"><span class="uf-label">部门</span><span class="uf-value">{{ u.department }}</span></div>
                    <div v-if="u.telephone" class="user-field"><span class="uf-label">电话</span><span class="uf-value">{{ u.telephone }}</span></div>
                    <div v-if="u.dn" class="user-field"><span class="uf-label">DN</span><span class="uf-value mono">{{ u.dn }}</span></div>
                  </div>
                </div>
              </div>
              <!-- 磁盘信息特殊渲染 -->
              <div v-else-if="detail.result.mounts && detail.result.mounts.length" class="result-mounts">
                <table class="table detail-table">
                  <thead>
                    <tr><th>挂载点</th><th>文件系统</th><th>类型</th><th>总量</th><th>已用</th><th>可用</th><th>使用率</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="m in detail.result.mounts" :key="m.mount">
                      <td>{{ m.mount }}</td>
                      <td>{{ m.filesystem }}</td>
                      <td>{{ m.type }}</td>
                      <td>{{ m.size }}</td>
                      <td>{{ m.used }}</td>
                      <td>{{ m.avail }}</td>
                      <td>
                        <span class="usage-bar" :class="{ warning: m.warning, critical: m.critical }">{{ m.use_pct }}%</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="detail.result.summary" class="result-summary">
                  共 {{ detail.result.summary.total_mounts }} 个挂载点，
                  <span v-if="detail.result.summary.warning_count" class="text-warning">{{ detail.result.summary.warning_count }} 个警告</span>
                  <span v-if="detail.result.summary.critical_count" class="text-danger">{{ detail.result.summary.critical_count }} 个严重</span>
                </div>
              </div>
              <!-- 默认 JSON 展示 -->
              <pre v-else class="detail-code">{{ formatResult(detail.result) }}</pre>
            </div>

            <!-- 原始输出 -->
            <div v-if="detail.stdout && !detail.result" class="detail-section">
              <h4 class="detail-section-title">输出</h4>
              <pre class="detail-code">{{ detail.stdout }}</pre>
            </div>
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

/* 可点击行 */
.clickable-row {
  cursor: pointer;
  transition: background 0.15s;
}
.clickable-row:hover {
  background: var(--color-bg-hover, rgba(99, 102, 241, 0.05));
}

/* 列样式 */
.col-time {
  white-space: nowrap;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.col-trigger {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.col-duration {
  white-space: nowrap;
  font-size: 13px;
}
.col-action {
  width: 60px;
  text-align: right;
}
.detail-link {
  font-size: 13px;
  color: var(--color-primary, #6366f1);
}

/* 工具标签 */
.tool-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  background: rgba(99, 102, 241, 0.1);
  color: #6366f1;
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

/* 详情弹窗 */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.modal-box {
  background: var(--color-bg, #fff);
  border-radius: 12px;
  width: 90%;
  max-width: 720px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
}
.detail-modal {
  max-width: 720px;
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  border-bottom: 1px solid var(--color-border, #e5e7eb);
}
.modal-header h3 {
  font-size: 16px;
  font-weight: 600;
}
.modal-close {
  background: none;
  border: none;
  font-size: 22px;
  cursor: pointer;
  color: var(--color-text-secondary);
  padding: 0 4px;
  line-height: 1;
}
.modal-close:hover {
  color: var(--color-text);
}
.modal-body {
  padding: 20px 24px;
  overflow-y: auto;
  flex: 1;
}

/* 详情内容 */
.detail-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.detail-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.detail-section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 24px;
}
.detail-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.detail-label {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.detail-value {
  font-size: 14px;
}
.text-light {
  color: var(--color-text-secondary);
  font-size: 13px;
}

/* 代码块 */
.detail-code {
  background: var(--color-bg-code, #1e1e2e);
  color: #cdd6f4;
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 13px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 300px;
  overflow-y: auto;
}
.error-code {
  background: rgba(239, 68, 68, 0.08);
  color: #dc2626;
}

/* LDAP 用户卡片 */
.result-users {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.user-card {
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 8px;
  overflow: hidden;
}
.user-card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: var(--color-bg-hover, #f9fafb);
}
.user-name {
  font-weight: 600;
  font-size: 14px;
}
.user-title {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.user-card-body {
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.user-field {
  display: flex;
  gap: 8px;
  font-size: 13px;
}
.uf-label {
  min-width: 48px;
  color: var(--color-text-secondary);
}
.uf-value {
  flex: 1;
}
.mono {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12px;
  word-break: break-all;
}

/* 磁盘信息表格 */
.detail-table {
  font-size: 13px;
}
.usage-bar {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(34, 197, 94, 0.1);
  color: #16a34a;
}
.usage-bar.warning {
  background: rgba(245, 158, 11, 0.1);
  color: #d97706;
}
.usage-bar.critical {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}
.result-summary {
  margin-top: 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.text-warning { color: #d97706; }
.text-danger { color: #dc2626; }
</style>
