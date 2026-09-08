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
const detailItem = ref(null) // 查看详情弹窗当前项
const confirmBox = ref(null) // 审批/驳回确认弹窗 { type: 'approve'|'reject', item }

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
// 自定义确认弹窗替代原生 confirm：浏览器可能拦截对话框使 confirm() 返回 false，
// 导致点击批准/驳回毫无响应（请求不发出），因此改为页面内弹窗确认。
function askApprove(item) {
  confirmBox.value = { type: 'approve', item }
}
function askReject(item) {
  confirmBox.value = { type: 'reject', item }
}
function closeConfirm() {
  if (approvingId.value !== null) return
  confirmBox.value = null
}

async function doApprove(item) {
  if (approvingId.value !== null) return
  approvingId.value = item.id
  actionError.value = ''
  try {
    await api.post(`/selfheal/actions/${item.id}/approve`)
  } catch (err) {
    actionError.value = err.response?.data?.detail || '批准失败'
  } finally {
    approvingId.value = null
    confirmBox.value = null
    await refreshActionsAndStats()
  }
}

async function doReject(item) {
  if (approvingId.value !== null) return
  approvingId.value = item.id
  actionError.value = ''
  try {
    await api.post(`/selfheal/actions/${item.id}/reject`)
  } catch (err) {
    actionError.value = err.response?.data?.detail || '驳回失败'
  } finally {
    approvingId.value = null
    confirmBox.value = null
    await refreshActionsAndStats()
  }
}

// ====== 审批详情弹窗 ======
function openDetail(item) {
  detailItem.value = item
}
function closeDetail() {
  detailItem.value = null
}
// 美化 JSON: 已解析对象格式化输出, 其余原样
function fmtJson(v) {
  if (v === null || v === undefined || v === '') return '-'
  try {
    const obj = typeof v === 'string' ? JSON.parse(v) : v
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(v)
  }
}
// 目标参数: 兼容已 JSON 字符串化字段
function fmtTarget(v) {
  try {
    const obj = JSON.parse(v || '{}')
    return Object.keys(obj).length ? JSON.stringify(obj, null, 2) : '-'
  } catch {
    return v || '-'
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

// ====== 日志清理: 折叠面板与说明 ======
const showQuickCleanup = ref(false) // 快捷清理(原固定脚本)
const showAdvanced = ref(false)     // 高级模式(手写 JSON)
// 清理类别说明
const CLEANUP_CATEGORY_DESC = {
  system: '系统日志(/var/log 下按策略清理)',
  service: '服务日志(/data/*/logs 下按策略清理)',
  'docker-log': 'Docker 容器 json.log 截断',
  'docker-prune': '回收停止容器与 dangling 镜像（高风险，强制人工审批）',
  all: '整批执行以上全部类别',
}

// ====== 日志清理向导: 勾选状态 + 策略生成 ======
// 勾选状态: Map<selectionKey, {type, params, label, risk}>
// selectionKey 唯一标识可勾选项: file:<kind>:<path> | journal | prune
const selected = ref(new Map())
// journal 压缩大小(MB); 生成策略时以输入框当前值为准(wizardStrategy 动态读取)
const journalSize = ref(200)
// 清理向导提交状态
const wizardRunning = ref(false)
const wizardError = ref('')
const wizardResult = ref(null)

// 文件行: 附加 kind 标签(tab 归属), 供模板渲染与勾选
function fileRows(list, kind) {
  return (list || []).map((f) => ({ ...f, kind }))
}
const varLogRows = computed(() => fileRows(scanResult.value?.var_log, 'truncate_file'))
const serviceLogRows = computed(() => fileRows(scanResult.value?.service_logs, 'truncate_file'))
const dockerLogRows = computed(() => fileRows(scanResult.value?.docker_logs, 'docker_log_truncate'))

// 清理方式标签文案
const KIND_LABELS = {
  truncate_file: '截断文件',
  docker_log_truncate: '截断容器日志',
}
const KIND_RISKS = {
  truncate_file: 'low',
  docker_log_truncate: 'medium',
}

function selKey(row) {
  return `file:${row.kind}:${row.path}`
}

function isSelected(key) {
  return selected.value.has(key)
}

function toggleFile(row) {
  const key = selKey(row)
  if (selected.value.has(key)) {
    selected.value.delete(key)
  } else {
    selected.value.set(key, {
      type: row.kind,
      params: { path: row.path },
      label: row.path,
      risk: KIND_RISKS[row.kind] || 'high',
    })
  }
  selected.value = new Map(selected.value) // 触发响应式
}

function toggleJournal() {
  if (selected.value.has('journal')) {
    selected.value.delete('journal')
  } else {
    selected.value.set('journal', {
      type: 'journal_vacuum',
      params: { size: journalSize.value },
      label: 'journald 压缩',
      risk: 'low',
    })
  }
  selected.value = new Map(selected.value)
}

function togglePrune() {
  if (selected.value.has('prune')) {
    selected.value.delete('prune')
  } else {
    selected.value.set('prune', {
      type: 'run_cleanup_category',
      params: { category: 'docker-prune' },
      label: 'Docker 残留回收(docker-prune)',
      risk: 'high',
    })
  }
  selected.value = new Map(selected.value)
}

function removeSelected(key) {
  selected.value.delete(key)
  selected.value = new Map(selected.value)
}

// 已选汇总数组(模板渲染)
const selectedList = computed(() => Array.from(selected.value.entries()))

// 策略 JSON 预览(实时生成, 只读; journal size 动态取输入框当前值)
const wizardStrategy = computed(() => ({
  mount: '/',
  items: Array.from(selected.value.values()).map((s) => {
    const params = s.type === 'journal_vacuum' ? { ...s.params, size: journalSize.value } : s.params
    return { type: s.type, ...params }
  }),
}))

// 向导可提交条件: 已选服务器 且 至少勾选一项
const canSubmitWizard = computed(() => Boolean(cleanupForm.server_id) && selected.value.size > 0)

// 提交向导(ai-plan), 结果结构化展示
async function handleSubmitWizard() {
  if (wizardRunning.value) return
  wizardError.value = ''
  wizardResult.value = null
  if (!cleanupForm.server_id) {
    wizardError.value = '请选择服务器'
    return
  }
  if (!selected.value.size) {
    wizardError.value = '请先扫描，再勾选要清理的项'
    return
  }
  // journal size 收敛到 [1,10000] 整数(防止手输 0/负/空)
  if (selected.value.has('journal')) {
    journalSize.value = Math.min(Math.max(Number(journalSize.value) || 1, 1), 10000)
  }
  wizardRunning.value = true
  try {
    const res = await api.post('/selfheal/ai-plan', {
      server_id: Number(cleanupForm.server_id),
      strategy: wizardStrategy.value,
    })
    wizardResult.value = res.data
    selected.value = new Map() // 提交成功后清空勾选
    await refreshActionsAndStats()
  } catch (err) {
    wizardError.value = err.response?.data?.detail || '提交清理失败'
  } finally {
    wizardRunning.value = false
  }
}

// 提交结果派生: {plan_id, accepted, rejected, auto, items}
const wizardSummary = computed(() => {
  const r = wizardResult.value || {}
  return {
    accepted: r.accepted ?? 0,
    pending: Math.max((r.accepted ?? 0) - (r.auto ?? 0), 0), // 挂单数 = 受理 - 直执
    auto: r.auto ?? 0,
    rejected: r.rejected ?? 0,
  }
})
// 被拒项(后端契约: {ok: False, reason})
const wizardRejectedItems = computed(() => {
  const r = wizardResult.value || {}
  const items = Array.isArray(r.items) ? r.items : []
  return items.filter((it) => it && it.ok === false)
})

// 磁盘使用率: scanResult.disk 是多挂载点对象 {<mount>: {use_pct, used, avail}}
const diskMounts = computed(() => Object.entries(scanResult.value?.disk || {}))
function diskTone(pct) {
  if (pct >= 80) return 'danger'
  if (pct >= 60) return 'warning'
  return 'success'
}
// docker 残留: dangling 镜像数 + 停止容器数
const dockerResidue = computed(() => {
  const img = scanResult.value?.docker_images || {}
  const stopped = (scanResult.value?.docker_containers || []).filter(
    (c) => !String(c.status || '').toLowerCase().startsWith('up')
  )
  return { dangling: img.dangling || 0, stopped: stopped.length }
})
// 大文件 tab: 记录当前激活 tab
const fileTab = ref('var_log')
const FILE_TABS = [
  { key: 'var_log', label: '/var/log' },
  { key: 'service_logs', label: '服务日志 /data' },
  { key: 'docker_logs', label: 'Docker 容器日志' },
]

// 按 tab key 取行列表(tab 计数与 activeFileRows 共用)
function rowsForTab(key) {
  if (key === 'service_logs') return serviceLogRows.value
  if (key === 'docker_logs') return dockerLogRows.value
  return varLogRows.value
}

const activeFileRows = computed(() => rowsForTab(fileTab.value))

// 触发固定清理脚本(统一审批出口: auto 直执/approval 挂单/reject 拒绝)
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
    showQuickCleanup.value = true // 请求完成时展开面板,避免结果被隐藏
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

// 提交 AI 策略(统一审批出口: auto 直执/approval 挂单/reject 拒绝)
async function handleAiPlan() {
  if (aiPlanRunning.value) return
  aiPlanError.value = ''
  aiPlanResult.value = null
  if (!cleanupForm.server_id) { aiPlanError.value = '请选择服务器'; return }
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
    showAdvanced.value = true // 请求完成时展开面板,避免结果被隐藏
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
      </div>
      <div class="form-actions">
        <button class="btn btn-outline" :disabled="scanRunning" @click="handleScan">
          {{ scanRunning ? '扫描中…' : '🔍 只读扫描' }}
        </button>
      </div>

      <!-- 扫描结果(结构化) -->
      <div v-if="scanError" class="error-tip">{{ scanError }}</div>
      <div v-if="scanResult" class="wizard-result">
        <div class="result-head">
          <span class="result-title">扫描结果（只读）</span>
          <button class="result-close" @click="scanResult = null">×</button>
        </div>
        <div class="wizard-body">
          <!-- 磁盘使用率 -->
          <div v-if="diskMounts.length" class="disk-cards">
            <div v-for="[mount, info] in diskMounts" :key="mount" class="disk-card" :class="'disk-' + diskTone(info.use_pct)">
              <span class="disk-mount">{{ mount }}</span>
              <span class="disk-pct">{{ info.use_pct }}%</span>
              <span class="disk-detail">已用 {{ info.used }} / 可用 {{ info.avail }}</span>
            </div>
          </div>

          <!-- 大文件清单(tab) -->
          <div v-if="varLogRows.length || serviceLogRows.length || dockerLogRows.length" class="file-section">
            <div class="file-tabs">
              <button
                v-for="t in FILE_TABS"
                :key="t.key"
                class="file-tab"
                :class="{ active: fileTab === t.key }"
                @click="fileTab = t.key"
              >
                {{ t.label }}（{{ rowsForTab(t.key).length }}）
              </button>
            </div>
            <div class="file-list">
              <label v-for="row in activeFileRows" :key="selKey(row)" class="file-row">
                <input type="checkbox" :checked="isSelected(selKey(row))" @change="toggleFile(row)" />
                <span class="file-path" :title="row.path">{{ row.path }}</span>
                <span class="file-size">{{ row.size_mb }} MB</span>
                <span class="badge" :class="KIND_RISKS[row.kind] === 'high' ? 'badge-danger' : 'badge-warning'">
                  {{ KIND_LABELS[row.kind] }}
                </span>
              </label>
            </div>
          </div>

          <!-- journald -->
          <div v-if="scanResult.journal && scanResult.journal.disk_used" class="residue-row">
            <label class="residue-label">
              <input type="checkbox" :checked="isSelected('journal')" @change="toggleJournal" />
              journald 占用 <code>{{ scanResult.journal.disk_used }}</code>
              <span class="residue-desc">→ 压缩为</span>
              <input v-model.number="journalSize" type="number" min="1" max="10000" class="journal-size" />
              <span class="residue-desc">MB</span>
              <span class="badge badge-warning">低危</span>
            </label>
          </div>

          <!-- Docker 残留 -->
          <div v-if="dockerResidue.dangling || dockerResidue.stopped" class="residue-row">
            <label class="residue-label">
              <input type="checkbox" :checked="isSelected('prune')" @change="togglePrune" />
              Docker 残留：dangling 镜像 {{ dockerResidue.dangling }} 个
              <template v-if="dockerResidue.stopped"> / 停止容器 {{ dockerResidue.stopped }} 个</template>
              <span class="residue-desc">→ docker-prune 整批回收</span>
              <span class="badge badge-danger">高风险 · 强制人工审批</span>
            </label>
          </div>
        </div>
      </div>

      <!-- ② 提交清理(向导) -->
      <div class="wizard-submit">
        <div v-if="selectedList.length" class="selected-panel">
          <div class="selected-head">
            <span class="selected-title">已选清理项（{{ selectedList.length }}）</span>
          </div>
          <div class="selected-list">
            <div v-for="[key, sel] in selectedList" :key="key" class="selected-row">
              <span class="selected-label" :title="sel.label">{{ sel.label }}</span>
              <span class="badge" :class="sel.risk === 'high' ? 'badge-danger' : 'badge-warning'">
                {{ sel.risk === 'high' ? '强制审批' : '按风险审批' }}
              </span>
              <button class="btn btn-secondary btn-sm" @click="removeSelected(key)">移除</button>
            </div>
          </div>
        </div>

        <div class="wizard-preview">
          <span class="detail-label">策略预览（提交内容，只读）</span>
          <pre class="detail-pre">{{ JSON.stringify(wizardStrategy, null, 2) }}</pre>
        </div>

        <div v-if="wizardError" class="error-tip">{{ wizardError }}</div>
        <div class="form-actions">
          <button class="btn btn-primary" :disabled="wizardRunning || !canSubmitWizard" @click="handleSubmitWizard">
            {{ wizardRunning ? '提交中…' : '提交清理（按风险审批）' }}
          </button>
        </div>

        <!-- 提交结果(结构化) -->
        <div v-if="wizardResult" class="result-box">
          <div class="result-head">
            <span class="result-title">提交结果</span>
            <button class="result-close" @click="wizardResult = null">×</button>
          </div>
          <div class="wizard-body">
            <div class="submit-summary">
              <span class="submit-stat">受理 {{ wizardSummary.accepted }} 项</span>
              <span class="submit-stat">（挂审批单 {{ wizardSummary.pending }} / 自动执行 {{ wizardSummary.auto }}）</span>
              <span v-if="wizardSummary.rejected" class="submit-stat danger">拒绝 {{ wizardSummary.rejected }} 项</span>
            </div>
            <div v-if="wizardResult.plan_id" class="residue-row">
              批次号：<code>{{ wizardResult.plan_id }}</code>
              <span class="residue-desc">挂单的项请到下方「审批队列」处理</span>
            </div>
            <div v-if="wizardRejectedItems.length" class="rejected-panel">
              <div class="selected-title">被拒项及原因</div>
              <div v-for="(it, i) in wizardRejectedItems" :key="i" class="rejected-row">
                {{ it.reason }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 快捷清理 / 高级模式 折叠开关 -->
      <div class="collapse-row">
        <button class="btn btn-outline btn-sm" @click="showQuickCleanup = !showQuickCleanup">
          {{ showQuickCleanup ? '▾ 收起' : '⚡ 快捷清理（整批，按风险审批）' }}
        </button>
        <button class="btn btn-outline btn-sm" @click="showAdvanced = !showAdvanced">
          {{ showAdvanced ? '▾ 收起' : '🔧 高级模式（手写策略 JSON）' }}
        </button>
      </div>

      <!-- 快捷清理(原固定脚本) -->
      <div v-if="showQuickCleanup" class="collapse-panel">
        <div class="form-grid">
          <div class="form-group">
            <label class="form-label">清理类别</label>
            <select v-model="cleanupForm.category" class="form-select">
              <option v-for="c in CLEANUP_CATEGORIES" :key="c.value" :value="c.value">
                {{ c.label }}
              </option>
            </select>
          </div>
        </div>
        <p class="category-desc">{{ CLEANUP_CATEGORY_DESC[cleanupForm.category] || '' }}</p>
        <div v-if="cleanupError" class="error-tip">{{ cleanupError }}</div>
        <div class="form-actions">
          <button class="btn btn-primary" :disabled="cleanupRunning" @click="handleCleanup">
            {{ cleanupRunning ? '触发中…' : '▶ 触发整批清理（按风险审批）' }}
          </button>
        </div>
        <div v-if="cleanupResult" class="result-box">
          <div class="result-head">
            <span class="result-title">快捷清理结果</span>
            <button class="result-close" @click="cleanupResult = null">×</button>
          </div>
          <pre class="result-pre">{{ JSON.stringify(cleanupResult, null, 2) }}</pre>
        </div>
      </div>

      <!-- 高级模式(手写 JSON) -->
      <div v-if="showAdvanced" class="collapse-panel">
        <div class="form-group">
          <label class="form-label">AI 清理策略（JSON，每项映射到白名单模板）</label>
          <textarea v-model="strategyJson" class="form-input" rows="5"
            placeholder='{"mount": "/", "items": [{"type": "journal_vacuum", "size": "200"}]}'></textarea>
        </div>
        <div class="form-actions">
          <button class="btn btn-outline" :disabled="scanRunning || !scanResult" @click="fillStrategyExample">
            填充策略示例
          </button>
          <button class="btn btn-primary" :disabled="aiPlanRunning" @click="handleAiPlan">
            {{ aiPlanRunning ? '提交中…' : '提交策略（按风险审批）' }}
          </button>
        </div>
        <div v-if="aiPlanError" class="error-tip">{{ aiPlanError }}</div>
        <div v-if="aiPlanResult" class="result-box">
          <div class="result-head">
            <span class="result-title">AI 策略结果</span>
            <button class="result-close" @click="aiPlanResult = null">×</button>
          </div>
          <pre class="result-pre">{{ JSON.stringify(aiPlanResult, null, 2) }}</pre>
        </div>
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
                <div class="action-btns">
                  <button
                    class="btn btn-secondary btn-sm"
                    :disabled="approvingId !== null"
                    @click="openDetail(a)"
                  >
                    详情
                  </button>
                  <template v-if="a.status === 'pending'">
                    <button
                      class="btn btn-primary btn-sm"
                      :disabled="approvingId !== null"
                      @click="askApprove(a)"
                    >
                      {{ approvingId === a.id ? '处理中…' : '批准' }}
                    </button>
                    <button
                      class="btn btn-danger btn-sm"
                      :disabled="approvingId !== null"
                      @click="askReject(a)"
                    >
                      拒绝
                    </button>
                  </template>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 审批详情弹窗 -->
    <div v-if="detailItem" class="modal-mask" @click.self="closeDetail">
      <div class="modal-panel">
        <div class="modal-head">
          <h3>自愈动作详情 #{{ detailItem.id }}</h3>
          <button class="btn btn-secondary btn-sm" @click="closeDetail">关闭</button>
        </div>
        <div class="modal-body">
          <div class="detail-grid">
            <div class="detail-field">
              <span class="detail-label">服务器</span>
              <span class="detail-value">{{ detailItem.server_name || detailItem.server_id || '-' }}</span>
            </div>
            <div class="detail-field">
              <span class="detail-label">场景</span>
              <span class="detail-value">{{ sceneLabel(detailItem.scene) }}（{{ detailItem.action_name }}）</span>
            </div>
            <div class="detail-field">
              <span class="detail-label">风险 / 状态</span>
              <span class="detail-value">
                <span class="badge" :class="severityBadge(detailItem.severity)">{{ severityLabel(detailItem.severity) }}</span>
                <span class="badge" :class="statusBadge(detailItem.status)">{{ statusLabel(detailItem.status) }}</span>
              </span>
            </div>
            <div class="detail-field">
              <span class="detail-label">触发方式</span>
              <span class="detail-value">{{ detailItem.triggered_by === 'dialog' ? 'AI 对话' : '手动' }}</span>
            </div>
            <div class="detail-field">
              <span class="detail-label">创建时间</span>
              <span class="detail-value">{{ detailItem.created_at || '-' }}</span>
            </div>
            <div class="detail-field">
              <span class="detail-label">审批人 / 批准时间</span>
              <span class="detail-value">{{ detailItem.approver || '-' }} / {{ detailItem.approved_at || '-' }}</span>
            </div>
          </div>

          <div class="detail-block">
            <span class="detail-label">目标参数</span>
            <pre class="detail-pre">{{ fmtTarget(detailItem.target) }}</pre>
          </div>

          <div class="detail-block">
            <span class="detail-label">执行命令</span>
            <pre class="detail-pre">{{ detailItem.rendered_command || '-' }}</pre>
          </div>

          <div class="detail-block">
            <span class="detail-label">分级理由</span>
            <pre class="detail-pre">{{ fmtJson(detailItem.grade_reasons) }}</pre>
          </div>

          <div class="detail-block">
            <span class="detail-label">执行结果（{{ detailItem.executed_at || '未执行' }}）</span>
            <pre class="detail-pre">{{ fmtJson(detailItem.execution_result) }}</pre>
          </div>

          <div class="detail-block">
            <span class="detail-label">验证结果（success={{ detailItem.success }}）</span>
            <pre class="detail-pre">{{ fmtJson(detailItem.verification_result) }}</pre>
          </div>

          <div v-if="detailItem.plan_id" class="detail-block">
            <span class="detail-label">AI 策略批次</span>
            <pre class="detail-pre">{{ detailItem.plan_id }}</pre>
          </div>
        </div>
      </div>
    </div>
    <!-- 审批/驳回确认弹窗(替代原生 confirm,避免浏览器拦截对话框导致无响应) -->
    <div v-if="confirmBox" class="modal-mask" @click.self="closeConfirm">
      <div class="modal-panel confirm-panel">
        <div class="modal-head">
          <h3>{{ confirmBox.type === 'approve' ? '批准' : '驳回' }}自愈动作 #{{ confirmBox.item.id }}</h3>
          <button class="btn btn-secondary btn-sm" @click="closeConfirm">关闭</button>
        </div>
        <div class="modal-body">
          <p class="confirm-text">
            确认{{ confirmBox.type === 'approve' ? '批准' : '驳回' }}自愈动作
            <strong>#{{ confirmBox.item.id }}</strong>（{{ actionLabel(confirmBox.item.action_name) }}）？
          </p>
          <div v-if="confirmBox.type === 'approve'" class="detail-block">
            <span class="detail-label">将执行命令</span>
            <pre class="detail-pre">{{ confirmBox.item.rendered_command || '-' }}</pre>
          </div>
          <div class="confirm-actions">
            <button class="btn btn-outline" :disabled="approvingId !== null" @click="closeConfirm">取消</button>
            <button
              v-if="confirmBox.type === 'approve'"
              class="btn btn-primary"
              :disabled="approvingId !== null"
              @click="doApprove(confirmBox.item)"
            >
              {{ approvingId === confirmBox.item.id ? '执行中…' : '确认批准' }}
            </button>
            <button
              v-else
              class="btn btn-danger"
              :disabled="approvingId !== null"
              @click="doReject(confirmBox.item)"
            >
              {{ approvingId === confirmBox.item.id ? '处理中…' : '确认驳回' }}
            </button>
          </div>
        </div>
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

/* 审批详情弹窗 */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 24px;
}
.modal-panel {
  background: var(--color-card);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  width: min(720px, 100%);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border-light);
}
.modal-head h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.modal-body {
  padding: 16px 20px;
  overflow-y: auto;
}
.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px 20px;
  margin-bottom: 14px;
}
.detail-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.detail-label {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.detail-value {
  font-size: 13px;
  word-break: break-all;
}
.detail-block {
  margin-bottom: 14px;
}
.detail-pre {
  margin: 4px 0 0;
  padding: 10px 12px;
  background: var(--color-border-light);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 220px;
  overflow-y: auto;
}

/* 审批/驳回确认弹窗 */
.confirm-panel {
  width: min(520px, 100%);
}
.confirm-text {
  margin: 0 0 14px;
  font-size: 14px;
  line-height: 1.6;
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
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

/* 日志清理向导 */
.wizard-result { margin-top: 12px; border: 1px solid var(--color-border-light); border-radius: var(--radius-sm); overflow: hidden; }
.wizard-body { padding: 14px; display: flex; flex-direction: column; gap: 14px; }
.disk-cards { display: flex; gap: 10px; flex-wrap: wrap; }
.disk-card { flex: 1; min-width: 160px; border-radius: var(--radius-sm); padding: 12px; display: flex; flex-direction: column; gap: 2px; }
.disk-danger { background: color-mix(in srgb, var(--color-danger) 10%, transparent); border: 1px solid color-mix(in srgb, var(--color-danger) 35%, transparent); }
.disk-warning { background: color-mix(in srgb, var(--color-warning) 10%, transparent); border: 1px solid color-mix(in srgb, var(--color-warning) 35%, transparent); }
.disk-success { background: color-mix(in srgb, var(--color-success) 10%, transparent); border: 1px solid color-mix(in srgb, var(--color-success) 35%, transparent); }
.disk-mount { font-weight: 600; font-size: 13px; }
.disk-pct { font-size: 22px; font-weight: 700; }
.disk-detail { font-size: 12px; color: var(--color-text-secondary); }
.file-section { display: flex; flex-direction: column; gap: 8px; }
.file-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.file-tab { padding: 4px 10px; border: 1px solid var(--color-border-light); border-radius: var(--radius-sm); font-size: 12px; background: transparent; cursor: pointer; color: var(--color-text-secondary); }
.file-tab.active { background: var(--color-primary); color: #fff; border-color: var(--color-primary); }
.file-list { display: flex; flex-direction: column; max-height: 260px; overflow-y: auto; border: 1px solid var(--color-border-light); border-radius: var(--radius-sm); }
.file-row { display: flex; align-items: center; gap: 8px; padding: 6px 10px; font-size: 13px; }
.file-row + .file-row { border-top: 1px solid var(--color-border-light); }
.file-path { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: 'SF Mono', Menlo, monospace; font-size: 12px; }
.file-size { color: var(--color-text-secondary); font-size: 12px; white-space: nowrap; }
.residue-row { display: flex; align-items: center; gap: 8px; font-size: 13px; flex-wrap: wrap; }
.residue-label { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.residue-desc { color: var(--color-text-secondary); font-size: 12px; }
.journal-size { width: 70px; padding: 4px 6px; border: 1px solid var(--color-border-light); border-radius: var(--radius-sm); font-size: 12px; }
.wizard-submit { margin-top: 14px; display: flex; flex-direction: column; gap: 12px; }
.selected-panel { border: 1px solid var(--color-border-light); border-radius: var(--radius-sm); overflow: hidden; }
.selected-head { padding: 8px 14px; background: var(--color-border-light); }
.selected-title { font-size: 13px; font-weight: 600; }
.selected-list { display: flex; flex-direction: column; }
.selected-row { display: flex; align-items: center; gap: 8px; padding: 6px 14px; font-size: 12px; }
.selected-row + .selected-row { border-top: 1px solid var(--color-border-light); }
.selected-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: 'SF Mono', Menlo, monospace; }
.wizard-preview { display: flex; flex-direction: column; gap: 4px; }
.submit-summary { display: flex; gap: 10px; flex-wrap: wrap; font-size: 13px; }
.submit-stat { font-weight: 600; }
.submit-stat.danger { color: var(--color-danger); }
.rejected-panel { border: 1px solid rgba(239, 68, 68, 0.35); border-radius: var(--radius-sm); padding: 10px; display: flex; flex-direction: column; gap: 6px; }
.rejected-row { font-size: 12px; word-break: break-all; }
/* 快捷清理 / 高级模式 折叠面板 */
.collapse-row { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.collapse-panel { border: 1px dashed var(--color-border-light); border-radius: var(--radius-sm); padding: 12px; margin-bottom: 12px; }
.category-desc { margin: 8px 0 0; font-size: 12px; color: var(--color-text-secondary); }
</style>
