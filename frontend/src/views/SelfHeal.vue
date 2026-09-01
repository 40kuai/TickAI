<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import api from '@/api'

// ====== 数据状态 ======
const servers = ref([])
const scenes = ref([])
const actions = ref([])
const stats = ref({ total_executed: 0, success: 0, success_rate: 0, pending: 0 })
const loading = ref(false)
const errorMsg = ref('')

// ====== 手动触发表单 ======
const form = reactive({
  server_id: '',
  scene: '',
  target: ''
})
const running = ref(false)
const runError = ref('')
const runResult = ref(null)

// ====== 审批状态 ======
const approvingId = ref(null)
const actionError = ref('')

// ====== 标签 / 徽章映射 ======
const SCENE_LABELS = {
  process_restart: '进程重启',
  disk_clean: '磁盘清理',
  cache_clean: '缓存清理',
  log_cleanup_script: '日志清理(固定脚本)',
  ai_log_cleanup: '日志清理(AI分析)',
}
const ACTION_LABELS = {
  restart_service: '重启服务',
  truncate_log: '截断日志',
  clean_cache: '清理缓存',
  run_cleanup_script: '清理脚本',
  journal_vacuum: 'journal 压缩',
  docker_log_truncate: 'Docker 日志截断',
}
const STATUS_LABELS = {
  verified: '已验证',
  executed: '已执行',
  pending: '待审批',
  failed: '失败',
  verification_failed: '验证失败',
  rejected: '已驳回',
  approved: '已批准',
  executing: '执行中',
  noop: '无需处理',
}

function sceneLabel(name) {
  return SCENE_LABELS[name] || name || '-'
}
function actionLabel(name) {
  return ACTION_LABELS[name] || name || '-'
}
function statusLabel(s) {
  return STATUS_LABELS[s] || s || '-'
}

// 状态徽章: verified→success / executed→info / pending→warning / failed,verification_failed→danger / rejected→gray
function statusBadge(status) {
  const s = String(status || '').toLowerCase()
  if (s === 'verified') return 'badge-success'
  if (s === 'executed' || s === 'approved' || s === 'executing') return 'badge-info'
  if (s === 'pending') return 'badge-warning'
  if (s === 'failed' || s === 'verification_failed') return 'badge-danger'
  if (s === 'rejected') return 'badge-gray'
  return 'badge-gray'
}

// 风险等级
function severityLabel(sev) {
  const s = String(sev || '').toLowerCase()
  if (s === 'high') return '高危'
  if (s === 'low') return '低危'
  return sev || '-'
}
function severityBadge(sev) {
  return String(sev || '').toLowerCase() === 'high' ? 'badge-danger' : 'badge-warning'
}

// 目标 JSON 截断展示
function truncate(str, len = 42) {
  if (str == null) return '-'
  const s = String(str)
  return s.length > len ? s.slice(0, len) + '…' : s
}

// ====== 统计卡片 ======
const statCards = computed(() => [
  { label: '已执行', value: stats.value.total_executed ?? 0, icon: '⟳', tone: 'primary' },
  { label: '成功', value: stats.value.success ?? 0, icon: '✓', tone: 'success' },
  { label: '成功率', value: (stats.value.success_rate ?? 0) + '%', icon: '%', tone: 'info' },
  { label: '待审批', value: stats.value.pending ?? 0, icon: '⚠', tone: 'warning' },
])

// 场景下拉展示文本
function sceneOptionText(sc) {
  return `${sceneLabel(sc.name)}（${sc.name}）`
}

// 目标参数 placeholder：按所选场景的 required_target 提示
const targetPlaceholder = computed(() => {
  const sc = scenes.value.find((s) => s.name === form.scene)
  if (!sc || !(sc.required_target || []).length) {
    return '目标参数 JSON（可选）'
  }
  const ex = {}
  for (const k of sc.required_target) {
    if (k === 'service') ex[k] = 'nginx'
    else if (k === 'mount') ex[k] = '/data'
    else if (k === 'path') ex[k] = '/var/log/app.log'
    else if (k === 'mode') ex[k] = '3'
    else ex[k] = ''
  }
  return `必填: ${sc.required_target.join(', ')}，例如 ${JSON.stringify(ex)}`
})

// ====== 加载数据 ======
async function loadAll() {
  loading.value = true
  errorMsg.value = ''
  try {
    const [srvRes, sceneRes, actRes, statRes] = await Promise.all([
      api.get('/servers'),
      api.get('/selfheal/scenes'),
      api.get('/selfheal/actions'),
      api.get('/selfheal/stats'),
    ])
    servers.value = srvRes.data?.servers || []
    scenes.value = Array.isArray(sceneRes.data) ? sceneRes.data : []
    actions.value = Array.isArray(actRes.data) ? actRes.data : []
    stats.value = statRes.data || {}
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '加载自愈数据失败'
  } finally {
    loading.value = false
  }
}

// 刷新动作与统计（审批 / 触发后使用）
async function refreshActionsAndStats() {
  try {
    const [actRes, statRes] = await Promise.all([
      api.get('/selfheal/actions'),
      api.get('/selfheal/stats'),
    ])
    actions.value = Array.isArray(actRes.data) ? actRes.data : []
    stats.value = statRes.data || {}
  } catch {
    /* 刷新失败时保留现有数据 */
  }
}

// ====== 手动触发自愈 ======
async function handleRun() {
  if (running.value) return
  runError.value = ''
  runResult.value = null
  if (!form.server_id) {
    runError.value = '请选择服务器'
    return
  }
  if (!form.scene) {
    runError.value = '请选择场景'
    return
  }
  let target
  try {
    target = form.target.trim() ? JSON.parse(form.target.trim()) : {}
  } catch {
    runError.value = '目标参数必须是合法 JSON，例如 {"service":"nginx"}'
    return
  }
  running.value = true
  try {
    const res = await api.post('/selfheal/run', {
      server_id: Number(form.server_id),
      scene: form.scene,
      target,
    })
    runResult.value = res.data
    await refreshActionsAndStats()
  } catch (err) {
    runError.value = err.response?.data?.detail || '触发自愈失败'
  } finally {
    running.value = false
  }
}

// ====== 审批 / 驳回 ======
async function handleApprove(item) {
  if (approvingId.value !== null) return
  if (!confirm(`确认批准自愈动作 #${item.id}（${actionLabel(item.action_name)}）？`)) return
  approvingId.value = item.id
  actionError.value = ''
  try {
    await api.post(`/selfheal/actions/${item.id}/approve`)
  } catch (err) {
    actionError.value = err.response?.data?.detail || '批准失败'
  } finally {
    approvingId.value = null
    await refreshActionsAndStats()
  }
}

async function handleReject(item) {
  if (approvingId.value !== null) return
  if (!confirm(`确认驳回自愈动作 #${item.id}？`)) return
  approvingId.value = item.id
  actionError.value = ''
  try {
    await api.post(`/selfheal/actions/${item.id}/reject`)
  } catch (err) {
    actionError.value = err.response?.data?.detail || '驳回失败'
  } finally {
    approvingId.value = null
    await refreshActionsAndStats()
  }
}

// ====== 日志清理(通道一固定脚本 + 通道二AI分析) ======
const cleanupForm = reactive({ server_id: '', category: 'system' })
const CLEANUP_CATEGORIES = [
  { value: 'system', label: '系统日志' },
  { value: 'service', label: '服务日志 /data/*/logs' },
  { value: 'docker-log', label: 'Docker 日志' },
  { value: 'docker-prune', label: 'Docker 容器/镜像回收' },
  { value: 'all', label: '全部' },
]
const cleanupRunning = ref(false)
const cleanupError = ref('')
const cleanupResult = ref(null)
const scanRunning = ref(false)
const scanResult = ref(null)
const scanError = ref('')
const aiPlanRunning = ref(false)
const aiPlanError = ref('')
const aiPlanResult = ref(null)
const strategyJson = ref('')

// 触发固定清理脚本(生成审批单)
async function handleCleanup() {
  if (cleanupRunning.value) return
  cleanupError.value = ''
  cleanupResult.value = null
  if (!cleanupForm.server_id) { cleanupError.value = '请选择服务器'; return }
  cleanupRunning.value = true
  try {
    const res = await api.post('/selfheal/run', {
      server_id: Number(cleanupForm.server_id),
      scene: 'log_cleanup_script',
      target: { category: cleanupForm.category, mount: '/' },
    })
    cleanupResult.value = res.data
    await refreshActionsAndStats()
  } catch (err) {
    cleanupError.value = err.response?.data?.detail || '触发清理失败'
  } finally {
    cleanupRunning.value = false
  }
}

// 只读扫描
async function handleScan() {
  if (scanRunning.value) return
  scanError.value = ''
  scanResult.value = null
  if (!cleanupForm.server_id) { scanError.value = '请选择服务器'; return }
  scanRunning.value = true
  try {
    const res = await api.post('/selfheal/scan-log', { server_id: Number(cleanupForm.server_id) })
    scanResult.value = res.data
  } catch (err) {
    scanError.value = err.response?.data?.detail || '扫描失败'
  } finally {
    scanRunning.value = false
  }
}

// 提交 AI 策略(生成审批单)
async function handleAiPlan() {
  if (aiPlanRunning.value) return
  aiPlanError.value = ''
  aiPlanResult.value = null
  let strategy
  try {
    strategy = strategyJson.value.trim() ? JSON.parse(strategyJson.value.trim()) : {}
  } catch {
    aiPlanError.value = '策略必须是合法 JSON'
    return
  }
  aiPlanRunning.value = true
  try {
    const res = await api.post('/selfheal/ai-plan', {
      server_id: Number(cleanupForm.server_id),
      strategy,
    })
    aiPlanResult.value = res.data
    await refreshActionsAndStats()
  } catch (err) {
    aiPlanError.value = err.response?.data?.detail || '提交策略失败'
  } finally {
    aiPlanRunning.value = false
  }
}

// 把扫描结果预填为策略示例(帮助用户手写/让 LLM 出策略)
function fillStrategyExample() {
  const items = []
  if (scanResult.value?.journal?.disk_used) {
    items.push({ type: 'journal_vacuum', size: '200' })
  }
  if (scanResult.value?.docker_logs?.length) {
    for (const dl of scanResult.value.docker_logs.slice(0, 3)) {
      items.push({ type: 'docker_log_truncate', path: dl.path })
    }
  }
  strategyJson.value = JSON.stringify({ mount: '/', items }, null, 2)
}

onMounted(loadAll)
</script>

<template>
  <div class="selfheal-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">自愈中心</h2>
        <p class="page-sub">查看自愈统计、手动触发自愈闭环并审批高危动作</p>
      </div>
      <button class="btn btn-outline" :disabled="loading" @click="loadAll">刷新</button>
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

    <!-- 手动触发自愈 -->
    <div class="card">
      <h3 class="card-title">手动触发自愈</h3>
      <div class="form-grid">
        <div class="form-group">
          <label class="form-label">服务器 *</label>
          <select v-model="form.server_id" class="form-select">
            <option value="">请选择服务器</option>
            <option v-for="s in servers" :key="s.id" :value="s.id">
              {{ s.name }}（{{ s.host }}）
            </option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">场景 *</label>
          <select v-model="form.scene" class="form-select">
            <option value="">请选择场景</option>
            <option v-for="sc in scenes" :key="sc.name" :value="sc.name">
              {{ sceneOptionText(sc) }}
            </option>
          </select>
        </div>
        <div class="form-group full">
          <label class="form-label">目标参数（JSON）</label>
          <input v-model="form.target" class="form-input" :placeholder="targetPlaceholder" />
        </div>
      </div>
      <div v-if="runError" class="error-tip">{{ runError }}</div>
      <div v-if="actionError" class="error-tip">{{ actionError }}</div>
      <div class="form-actions">
        <button class="btn btn-primary" :disabled="running" @click="handleRun">
          {{ running ? '运行中…' : '▶ 运行自愈' }}
        </button>
      </div>

      <!-- 运行结果 -->
      <div v-if="runResult" class="result-box">
        <div class="result-head">
          <span class="result-title">运行结果</span>
          <button class="result-close" @click="runResult = null">×</button>
        </div>
        <pre class="result-pre">{{ JSON.stringify(runResult, null, 2) }}</pre>
      </div>
    </div>

    <!-- 日志清理(通道一固定脚本 + 通道二AI分析) -->
    <div class="card">
      <h3 class="card-title">日志清理</h3>
      <div class="form-grid">
        <div class="form-group">
          <label class="form-label">服务器 *</label>
          <select v-model="cleanupForm.server_id" class="form-select">
            <option value="">请选择服务器</option>
            <option v-for="s in servers" :key="s.id" :value="s.id">
              {{ s.name }}（{{ s.host }}）
            </option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">清理类别</label>
          <select v-model="cleanupForm.category" class="form-select">
            <option v-for="c in CLEANUP_CATEGORIES" :key="c.value" :value="c.value">
              {{ c.label }}
            </option>
          </select>
        </div>
      </div>
      <div v-if="cleanupError" class="error-tip">{{ cleanupError }}</div>
      <div class="form-actions">
        <button class="btn btn-primary" :disabled="cleanupRunning" @click="handleCleanup">
          {{ cleanupRunning ? '触发中…' : '▶ 触发固定清理(生成审批单)' }}
        </button>
        <button class="btn btn-outline" :disabled="scanRunning" @click="handleScan">
          {{ scanRunning ? '扫描中…' : '🔍 只读扫描' }}
        </button>
        <button class="btn btn-outline" :disabled="scanRunning" @click="fillStrategyExample">
          填充策略示例
        </button>
      </div>

      <!-- 扫描结果 -->
      <div v-if="scanError" class="error-tip">{{ scanError }}</div>
      <div v-if="scanResult" class="result-box">
        <div class="result-head">
          <span class="result-title">扫描结果</span>
          <button class="result-close" @click="scanResult = null">×</button>
        </div>
        <pre class="result-pre">{{ JSON.stringify(scanResult, null, 2) }}</pre>
      </div>

      <!-- AI 策略 -->
      <div class="form-group" style="margin-top: 12px">
        <label class="form-label">AI 清理策略（JSON，每项映射到白名单模板）</label>
        <textarea v-model="strategyJson" class="form-input" rows="5"
          placeholder='{"mount": "/", "items": [{"type": "journal_vacuum", "size": "200"}]}'></textarea>
      </div>
      <div v-if="aiPlanError" class="error-tip">{{ aiPlanError }}</div>
      <div class="form-actions">
        <button class="btn btn-primary" :disabled="aiPlanRunning" @click="handleAiPlan">
          {{ aiPlanRunning ? '提交中…' : '提交 AI 策略(生成审批单)' }}
        </button>
      </div>
      <div v-if="aiPlanResult" class="result-box">
        <div class="result-head">
          <span class="result-title">AI 策略结果</span>
          <button class="result-close" @click="aiPlanResult = null">×</button>
        </div>
        <pre class="result-pre">{{ JSON.stringify(aiPlanResult, null, 2) }}</pre>
      </div>
    </div>

    <!-- 审批队列 -->
    <div class="card">
      <h3 class="card-title">审批队列</h3>
      <div v-if="loading" class="state-tip">加载中…</div>
      <div v-else-if="errorMsg" class="state-tip error">{{ errorMsg }}</div>
      <div v-else-if="!actions.length" class="state-tip">暂无自愈动作</div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>服务器</th>
              <th>场景</th>
              <th>目标</th>
              <th>风险</th>
              <th>动作</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in actions" :key="a.id">
              <td>{{ a.id }}</td>
              <td class="cell-name">{{ a.server_name || a.server_id || '-' }}</td>
              <td>{{ sceneLabel(a.scene) }}</td>
              <td class="col-target" :title="a.target">{{ truncate(a.target) }}</td>
              <td>
                <span class="badge" :class="severityBadge(a.severity)">{{ severityLabel(a.severity) }}</span>
              </td>
              <td>
                <code class="cmd-text" :title="a.rendered_command">{{ a.rendered_command || '-' }}</code>
              </td>
              <td>
                <span class="badge" :class="statusBadge(a.status)">{{ statusLabel(a.status) }}</span>
              </td>
              <td>
                <div v-if="a.status === 'pending'" class="action-btns">
                  <button
                    class="btn btn-primary btn-sm"
                    :disabled="approvingId !== null"
                    @click="handleApprove(a)"
                  >
                    {{ approvingId === a.id ? '处理中…' : '批准' }}
                  </button>
                  <button
                    class="btn btn-danger btn-sm"
                    :disabled="approvingId !== null"
                    @click="handleReject(a)"
                  >
                    拒绝
                  </button>
                </div>
                <span v-else class="text-light">-</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.selfheal-page {
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
.card-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 16px;
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
.cell-name {
  font-weight: 600;
}
.col-target {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.cmd-text {
  display: inline-block;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  font-size: 12px;
  background: var(--color-border-light);
  border-radius: 4px;
  padding: 2px 6px;
  color: var(--color-text-secondary);
}
.action-btns {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.text-light {
  color: var(--color-text-secondary);
  font-size: 13px;
}

/* 统计卡片（与 Dashboard 风格一致） */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--spacing-md);
}
.stat-card {
  background: var(--color-card);
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
.tone-warning .stat-icon { background: rgba(245, 158, 11, 0.12); color: var(--color-warning); }
.stat-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}
.stat-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

/* 表单 */
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--spacing-md);
}
.form-grid .full {
  grid-column: 1 / -1;
}
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}
.error-tip {
  margin: 8px 0;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: rgba(239, 68, 68, 0.08);
  color: var(--color-danger);
  font-size: 13px;
}

/* 运行结果 */
.result-box {
  margin-top: 16px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: var(--color-border-light);
}
.result-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
}
.result-close {
  font-size: 18px;
  line-height: 1;
  color: var(--color-text-secondary);
  padding: 0 4px;
}
.result-close:hover {
  color: var(--color-text);
}
.result-pre {
  margin: 0;
  padding: 14px;
  background: var(--color-bg-code);
  color: #e2e8f0;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 640px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}

.form-input textarea,
textarea.form-input {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}
</style>
