<script setup>
import { ref, reactive, onMounted } from 'vue'
import api from '@/api'

const servers = ref([])
const loading = ref(false)
const errorMsg = ref('')

// ====== SSH 凭据管理 ======
const credentials = ref([])
const showCreds = ref(false) // 凭据管理卡片折叠状态
const credForm = reactive({
  name: '', port: 22, username: '', auth_type: 'password', password: '', key_content: '', description: ''
})
const addingCred = ref(false)
const credError = ref('')

// 编辑凭据弹窗
const showEditCred = ref(false)
const editCredForm = reactive({
  id: null, name: '', port: 22, username: '', auth_type: 'password', password: '', key_content: '', description: ''
})
const savingCred = ref(false)
const editCredError = ref('')

// 添加服务器表单
const showAdd = ref(false)
const form = reactive({
  name: '', host: '', tags: '', notes: '', ssh_credential_id: null
})
const adding = ref(false)
const addError = ref('')

// 同步状态
const syncing = ref(false)
const syncResult = ref(null)

// 同步配置弹窗
const showSyncConfig = ref(false)
const syncConfig = reactive({
  api_url: '',
  auth_type: 'none',
  auth_username: '',
  auth_password: '',
  api_token: '',
  response_path: '',
  timeout: 30,
  field_mapping: {
    name: 'name', host: 'host', tags: 'tags', notes: 'notes',
  },
  enabled: false,
})
const savingConfig = ref(false)
const testingConfig = ref(false)
const testResult = ref(null)
const configError = ref('')

// 操作结果弹窗
const resultModal = reactive({
  show: false, title: '', loading: false,
  serverName: '', data: null, error: ''
})

// 加载服务器列表
async function loadServers() {
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await api.get('/servers')
    servers.value = res.data.servers || []
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '加载服务器列表失败'
  } finally {
    loading.value = false
  }
}

// 加载 SSH 凭据列表
async function loadCredentials() {
  try {
    const res = await api.get('/ssh-credentials')
    credentials.value = res.data.credentials || []
  } catch {
    credentials.value = []
  }
}

// 添加 SSH 凭据
async function addCredential() {
  credError.value = ''
  if (!credForm.name || !credForm.username) {
    credError.value = '请填写名称和用户名'
    return
  }
  addingCred.value = true
  try {
    await api.post('/ssh-credentials', {
      name: credForm.name,
      port: Number(credForm.port) || 22,
      username: credForm.username,
      auth_type: credForm.auth_type,
      password: credForm.password,
      key_content: credForm.key_content,
      description: credForm.description,
    })
    Object.assign(credForm, { name: '', port: 22, username: '', auth_type: 'password', password: '', key_content: '', description: '' })
    await loadCredentials()
  } catch (err) {
    credError.value = err.response?.data?.detail || '添加凭据失败'
  } finally {
    addingCred.value = false
  }
}

// 打开编辑凭据弹窗
function openEditCred(cred) {
  editCredForm.id = cred.id
  editCredForm.name = cred.name
  editCredForm.port = cred.port
  editCredForm.username = cred.username
  editCredForm.auth_type = cred.auth_type || 'password'
  editCredForm.password = '' // 密码不回显，留空表示不修改
  editCredForm.key_content = '' // 密钥不回显，留空表示不修改
  editCredForm.description = cred.description || ''
  editCredError.value = ''
  showEditCred.value = true
}

// 保存编辑凭据
async function saveEditCred() {
  editCredError.value = ''
  if (!editCredForm.name || !editCredForm.username) {
    editCredError.value = '请填写名称和用户名'
    return
  }
  savingCred.value = true
  try {
    const payload = {
      name: editCredForm.name,
      port: Number(editCredForm.port) || 22,
      username: editCredForm.username,
      auth_type: editCredForm.auth_type,
      description: editCredForm.description,
    }
    // 仅在填写了密码时才提交密码
    if (editCredForm.password) {
      payload.password = editCredForm.password
    }
    // 仅在填写了密钥时才提交密钥
    if (editCredForm.key_content) {
      payload.key_content = editCredForm.key_content
    }
    await api.put(`/ssh-credentials/${editCredForm.id}`, payload)
    showEditCred.value = false
    await loadCredentials()
    await loadServers() // 凭据名可能变化，刷新服务器列表
  } catch (err) {
    editCredError.value = err.response?.data?.detail || '保存失败'
  } finally {
    savingCred.value = false
  }
}

// 删除 SSH 凭据（关联服务器将自动解绑）
async function deleteCredential(cred) {
  const tip = cred.server_count > 0
    ? `确定删除凭据「${cred.name}」吗？该凭据关联了 ${cred.server_count} 台服务器，删除后将自动解绑。`
    : `确定删除凭据「${cred.name}」吗？`
  if (!confirm(tip)) return
  try {
    await api.delete(`/ssh-credentials/${cred.id}`)
    await loadCredentials()
    await loadServers() // 刷新服务器列表以反映解绑状态
  } catch (err) {
    alert(err.response?.data?.detail || '删除失败')
  }
}

// 设置默认 SSH 凭据
async function setDefaultCredential(id) {
  try {
    await api.put(`/ssh-credentials/${id}/default`)
    await loadCredentials()
  } catch (e) {
    alert('设置默认凭据失败: ' + (e.response?.data?.detail || e.message))
  }
}

// 绑定 / 解绑凭据到服务器
async function bindCredential(server, credId) {
  try {
    const id = credId ? Number(credId) : null
    await api.put(`/ssh-credentials/bind/${server.id}`, { ssh_credential_id: id })
    await loadServers()
  } catch (err) {
    alert(err.response?.data?.detail || '绑定失败')
    await loadServers() // 出错时刷新以恢复正确状态
  }
}

// 提交添加
async function handleAdd() {
  addError.value = ''
  if (!form.name || !form.host) {
    addError.value = '请填写名称和主机 IP'
    return
  }
  adding.value = true
  try {
    const payload = {
      name: form.name, host: form.host,
      tags: form.tags || '',
      notes: form.notes,
    }
    // 可选：关联 SSH 凭据
    if (form.ssh_credential_id) {
      payload.ssh_credential_id = Number(form.ssh_credential_id)
    }
    await api.post('/servers', payload)
    Object.assign(form, { name: '', host: '', tags: '', notes: '', ssh_credential_id: null })
    showAdd.value = false
    await loadServers()
  } catch (err) {
    addError.value = err.response?.data?.detail || '添加失败'
  } finally {
    adding.value = false
  }
}

// 删除服务器
async function handleDelete(server) {
  if (!confirm(`确定删除服务器「${server.name}」吗？`)) return
  try {
    await api.delete(`/servers/${server.id}`)
    await loadServers()
  } catch (err) {
    alert(err.response?.data?.detail || '删除失败')
  }
}

// 通用操作调用
async function runAction(server, action, title) {
  resultModal.show = true
  resultModal.title = title
  resultModal.serverName = server.name
  resultModal.loading = true
  resultModal.data = null
  resultModal.error = ''
  try {
    const res = await api.post(`/servers/${server.id}/${action}`)
    resultModal.data = res.data
  } catch (err) {
    resultModal.error = err.response?.data?.detail || err.response?.data?.message || '操作失败'
  } finally {
    resultModal.loading = false
  }
}

// 检查服务器是否已绑定 SSH 凭据，未绑定则提示
function ensureCredBound(server) {
  if (!server.ssh_credential_id) {
    alert('该服务器未绑定 SSH 凭据，请先在「SSH 凭据」列选择并绑定')
    return false
  }
  return true
}

function checkDisk(server) {
  if (!ensureCredBound(server)) return
  runAction(server, 'check_disk', '磁盘检查')
}
function checkResources(server) {
  if (!ensureCredBound(server)) return
  runAction(server, 'check_resources', '资源检查')
}
function listServices(server) {
  if (!ensureCredBound(server)) return
  runAction(server, 'list_services', '服务列表')
}

// 压力等级徽章
function pressureBadge(level) {
  const l = String(level || '').toLowerCase()
  if (l === 'high' || l === 'critical') return 'badge-danger'
  if (l === 'medium' || l === 'warning') return 'badge-warning'
  if (l === 'low' || l === 'normal') return 'badge-success'
  return 'badge-gray'
}

function closeResult() { resultModal.show = false }

// ====== 同步配置 ======

// 打开同步配置弹窗
async function openSyncConfig() {
  showSyncConfig.value = true
  configError.value = ''
  testResult.value = null
  // 确保凭据列表已加载（供默认凭据选择器使用）
  if (!credentials.value.length) {
    await loadCredentials()
  }
  // 加载已保存的配置
  try {
    const res = await api.get('/servers/sync/config')
    const d = res.data
    syncConfig.api_url = d.api_url || ''
    syncConfig.auth_type = d.auth_type || 'none'
    syncConfig.auth_username = d.auth_username || ''
    syncConfig.auth_password = d.auth_password || ''
    syncConfig.api_token = d.api_token || ''
    syncConfig.response_path = d.response_path || ''
    syncConfig.timeout = d.timeout || 30
    syncConfig.enabled = d.enabled || false
    if (d.field_mapping) {
      Object.assign(syncConfig.field_mapping, d.field_mapping)
    }
  } catch { /* 首次无配置，用默认值 */ }
}

// 保存配置
async function saveSyncConfig() {
  savingConfig.value = true
  configError.value = ''
  try {
    await api.put('/servers/sync/config', {
      api_url: syncConfig.api_url,
      auth_type: syncConfig.auth_type,
      auth_username: syncConfig.auth_username,
      auth_password: syncConfig.auth_password,
      api_token: syncConfig.api_token,
      response_path: syncConfig.response_path,
      timeout: Number(syncConfig.timeout) || 30,
      field_mapping: { ...syncConfig.field_mapping },
      enabled: syncConfig.enabled,
    })
    showSyncConfig.value = false
  } catch (err) {
    configError.value = err.response?.data?.detail || '保存失败'
  } finally {
    savingConfig.value = false
  }
}

// 测试连接
async function testSync() {
  testingConfig.value = true
  testResult.value = null
  configError.value = ''
  try {
    const res = await api.post('/servers/sync/test', {
      api_url: syncConfig.api_url,
      auth_type: syncConfig.auth_type,
      auth_username: syncConfig.auth_username,
      auth_password: syncConfig.auth_password,
      api_token: syncConfig.api_token,
      response_path: syncConfig.response_path,
      timeout: Number(syncConfig.timeout) || 30,
      field_mapping: { ...syncConfig.field_mapping },
    })
    testResult.value = res.data
  } catch (err) {
    testResult.value = { success: false, error: err.response?.data?.detail || '请求失败' }
  } finally {
    testingConfig.value = false
  }
}

// 立即同步
async function handleSync() {
  syncing.value = true
  syncResult.value = null
  try {
    const res = await api.post('/servers/sync')
    syncResult.value = res.data
    await loadServers()
  } catch (err) {
    syncResult.value = { error: err.response?.data?.detail || '同步失败' }
  } finally {
    syncing.value = false
  }
}

// 字段映射表（内部字段 -> 外部字段名）
const mappingFields = [
  { key: 'name', label: '名称' },
  { key: 'host', label: '主机 IP' },
  { key: 'tags', label: '标签' },
  { key: 'notes', label: '备注' },
]

onMounted(() => {
  loadServers()
  loadCredentials()
})
</script>

<template>
  <div class="servers-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">服务器管理</h2>
        <p class="page-sub">管理 SSH 服务器并执行磁盘、资源、服务检查</p>
      </div>
      <div class="page-head-actions">
        <button class="btn btn-outline" @click="openSyncConfig">⚙ 同步配置</button>
        <button class="btn btn-outline" :disabled="syncing" @click="handleSync">
          {{ syncing ? '同步中…' : '↻ 立即同步' }}
        </button>
        <button class="btn btn-primary" @click="showAdd = !showAdd">
          {{ showAdd ? '收起' : '+ 添加服务器' }}
        </button>
      </div>
    </div>

    <!-- 添加表单 -->
    <div v-if="showAdd" class="card add-form">
      <h3 class="card-title">添加服务器</h3>
      <div class="form-grid">
        <div class="form-group">
          <label class="form-label">名称 *</label>
          <input v-model="form.name" class="form-input" placeholder="例如：web-prod-01" />
        </div>
        <div class="form-group">
          <label class="form-label">主机 IP *</label>
          <input v-model="form.host" class="form-input" placeholder="例如：10.0.0.1" />
        </div>
        <div class="form-group">
          <label class="form-label">关联 SSH 凭据（可选）</label>
          <select v-model="form.ssh_credential_id" class="form-input">
            <option :value="null">不关联</option>
            <option v-for="c in credentials" :key="c.id" :value="c.id">
              {{ c.name }}（{{ c.username }}@:{{ c.port }}）
            </option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">标签（逗号分隔）</label>
          <input v-model="form.tags" class="form-input" placeholder="例如：生产,核心" />
        </div>
        <div class="form-group full">
          <label class="form-label">备注</label>
          <textarea v-model="form.notes" class="form-textarea" placeholder="可选"></textarea>
        </div>
      </div>
      <div v-if="addError" class="error-tip">{{ addError }}</div>
      <div class="form-actions">
        <button class="btn btn-ghost" @click="showAdd = false">取消</button>
        <button class="btn btn-primary" :disabled="adding" @click="handleAdd">
          {{ adding ? '添加中…' : '保存' }}
        </button>
      </div>
    </div>

    <!-- 同步结果 -->
    <div v-if="syncResult" class="card sync-result" :class="{ 'sync-error': syncResult.error }">
      <div class="sync-result-head">
        <span v-if="syncResult.error" class="sync-icon">✕</span>
        <span v-else class="sync-icon">✓</span>
        <span v-if="syncResult.error" class="sync-title">同步失败</span>
        <span v-else class="sync-title">同步完成</span>
        <button class="sync-close" @click="syncResult = null">×</button>
      </div>
      <div v-if="syncResult.error" class="sync-detail">{{ syncResult.error }}</div>
      <div v-else class="sync-detail">
        共 {{ syncResult.total }} 台主机，新增 {{ syncResult.added }} 台，更新 {{ syncResult.updated }} 台，跳过 {{ syncResult.skipped }} 台
      </div>
      <div v-if="syncResult.errors && syncResult.errors.length" class="sync-errors">
        <div v-for="(e, i) in syncResult.errors" :key="i">{{ e }}</div>
      </div>
    </div>

    <!-- SSH 凭据管理 -->
    <div class="card cred-card">
      <div class="cred-head" @click="showCreds = !showCreds">
        <div class="cred-head-info">
          <h3 class="card-title" style="margin-bottom:0;">SSH 凭据管理</h3>
          <span class="cred-count">共 {{ credentials.length }} 个凭据</span>
        </div>
        <button class="btn btn-ghost btn-sm" @click.stop="showCreds = !showCreds">
          {{ showCreds ? '收起 ▲' : '展开 ▼' }}
        </button>
      </div>

      <div v-if="showCreds" class="cred-body">
        <!-- 添加凭据表单 -->
        <div class="cred-add">
          <div class="form-grid">
            <div class="form-group">
              <label class="form-label">名称 *</label>
              <input v-model="credForm.name" class="form-input" placeholder="例如：生产默认" />
            </div>
            <div class="form-group">
              <label class="form-label">端口</label>
              <input v-model.number="credForm.port" class="form-input" type="number" placeholder="22" />
            </div>
            <div class="form-group">
              <label class="form-label">用户名 *</label>
              <input v-model="credForm.username" class="form-input" placeholder="例如：root" />
            </div>
            <div class="form-group">
              <label class="form-label">认证方式</label>
              <select v-model="credForm.auth_type" class="form-input">
                <option value="password">密码</option>
                <option value="key">密钥</option>
              </select>
            </div>
            <!-- 密码认证：显示密码输入框 -->
            <div v-if="credForm.auth_type === 'password'" class="form-group">
              <label class="form-label">密码</label>
              <input v-model="credForm.password" class="form-input" type="password" placeholder="SSH 密码" />
            </div>
            <!-- 密钥认证：显示密钥内容输入框 -->
            <div v-else class="form-group full">
              <label class="form-label">密钥内容</label>
              <textarea v-model="credForm.key_content" class="form-textarea" rows="5" placeholder="粘贴 PEM 格式私钥内容"></textarea>
            </div>
            <div class="form-group full">
              <label class="form-label">描述</label>
              <input v-model="credForm.description" class="form-input" placeholder="可选" />
            </div>
          </div>
          <div v-if="credError" class="error-tip">{{ credError }}</div>
          <div class="form-actions">
            <button class="btn btn-primary" :disabled="addingCred" @click="addCredential">
              {{ addingCred ? '添加中…' : '+ 添加凭据' }}
            </button>
          </div>
        </div>

        <!-- 凭据列表 -->
        <div v-if="!credentials.length" class="state-tip">暂无凭据，请在上方添加</div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>名称</th><th>端口</th><th>用户名</th><th>认证方式</th>
                <th>描述</th><th>关联服务器</th><th>默认</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in credentials" :key="c.id">
                <td class="cell-name">{{ c.name }}</td>
                <td>{{ c.port }}</td>
                <td>{{ c.username }}</td>
                <td>
                  <span class="badge" :class="c.auth_type === 'key' ? 'badge-warning' : 'badge-gray'">
                    {{ c.auth_type === 'key' ? '密钥' : '密码' }}
                  </span>
                </td>
                <td>{{ c.description || '-' }}</td>
                <td>
                  <span class="badge badge-gray">{{ c.server_count ?? 0 }}</span>
                </td>
                <td>
                  <span v-if="c.is_default" class="badge badge-success">默认</span>
                  <span v-else class="text-light">-</span>
                </td>
                <td>
                  <div class="action-btns">
                    <button v-if="!c.is_default" class="btn btn-outline btn-sm" @click="setDefaultCredential(c.id)">设为默认</button>
                    <button class="btn btn-outline btn-sm" @click="openEditCred(c)">编辑</button>
                    <button class="btn btn-danger btn-sm" @click="deleteCredential(c)">删除</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 服务器列表 -->
    <div class="card">
      <div v-if="loading" class="state-tip">加载中…</div>
      <div v-else-if="errorMsg" class="state-tip error">{{ errorMsg }}</div>
      <div v-else-if="!servers.length" class="state-tip">暂无服务器，点击右上角添加</div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>名称</th><th>主机</th>
              <th>标签</th><th>SSH 凭据</th><th>状态</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in servers" :key="s.id">
              <td class="cell-name">{{ s.name }}</td>
              <td>{{ s.host }}</td>
              <td>
                <span v-for="t in s.tags || []" :key="t" class="badge badge-gray tag">{{ t }}</span>
                <span v-if="!s.tags || !s.tags.length" class="text-light">-</span>
              </td>
              <td>
                <select
                  class="form-input bind-select"
                  :class="{ 'bind-select-unbound': !s.ssh_credential_id }"
                  :value="s.ssh_credential_id || ''"
                  :title="s.ssh_credential_name || '使用默认'"
                  @change="bindCredential(s, $event.target.value)"
                >
                  <option value="">使用默认</option>
                  <option v-for="c in credentials" :key="c.id" :value="c.id">
                    {{ c.name }}
                  </option>
                </select>
                <span v-if="!s.ssh_credential_id" class="default-cred-hint">(使用默认)</span>
              </td>
              <td>
                <span class="badge" :class="s.is_active ? 'badge-success' : 'badge-gray'">
                  {{ s.is_active ? '在线' : '离线' }}
                </span>
              </td>
              <td>
                <div class="action-btns">
                  <button class="btn btn-outline btn-sm" @click="checkDisk(s)">磁盘</button>
                  <button class="btn btn-outline btn-sm" @click="checkResources(s)">资源</button>
                  <button class="btn btn-outline btn-sm" @click="listServices(s)">服务</button>
                  <button class="btn btn-danger btn-sm" @click="handleDelete(s)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 同步配置弹窗 -->
    <div v-if="showSyncConfig" class="modal-mask" @click.self="showSyncConfig = false">
      <div class="modal sync-modal">
        <div class="modal-head">
          <div>
            <h3 class="modal-title">主机同步配置</h3>
            <p class="modal-sub">从外部 API 实时获取主机列表</p>
          </div>
          <button class="modal-close" @click="showSyncConfig = false">×</button>
        </div>
        <div class="modal-body">
          <!-- 连接配置 -->
          <div class="config-section">
            <h4 class="config-section-title">连接配置</h4>
            <div class="form-grid">
              <div class="form-group full">
                <label class="form-label">API 地址 *</label>
                <input v-model="syncConfig.api_url" class="form-input" placeholder="https://cmdb.example.com/api/hosts" />
              </div>
              <div class="form-group">
                <label class="form-label">认证方式</label>
                <select v-model="syncConfig.auth_type" class="form-input">
                  <option value="none">无认证</option>
                  <option value="basic">Basic Auth（用户名密码）</option>
                  <option value="bearer">Bearer Token</option>
                </select>
              </div>
              <div class="form-group">
                <label class="form-label">响应路径（可选）</label>
                <input v-model="syncConfig.response_path" class="form-input" placeholder="data.list" />
                <small class="form-hint">用点号分隔，从嵌套 JSON 提取主机列表</small>
              </div>
              <!-- Basic Auth -->
              <template v-if="syncConfig.auth_type === 'basic'">
                <div class="form-group">
                  <label class="form-label">用户名</label>
                  <input v-model="syncConfig.auth_username" class="form-input" placeholder="API 用户名" />
                </div>
                <div class="form-group">
                  <label class="form-label">密码</label>
                  <input v-model="syncConfig.auth_password" class="form-input" type="password" placeholder="API 密码" />
                </div>
              </template>
              <!-- Bearer Token -->
              <template v-if="syncConfig.auth_type === 'bearer'">
                <div class="form-group full">
                  <label class="form-label">Bearer Token</label>
                  <input v-model="syncConfig.api_token" class="form-input" type="password" placeholder="认证 Token" />
                </div>
              </template>
              <div class="form-group">
                <label class="form-label">超时（秒）</label>
                <input v-model.number="syncConfig.timeout" class="form-input" type="number" placeholder="30" />
              </div>
            </div>
          </div>

          <!-- SSH 默认配置 -->
          <!-- 字段映射 -->
          <div class="config-section">
            <h4 class="config-section-title">字段映射</h4>
            <p class="config-section-hint">将外部 API 的字段名映射到本平台字段</p>
            <table class="table mapping-table">
              <thead>
                <tr><th>本平台字段</th><th>外部 API 字段名</th></tr>
              </thead>
              <tbody>
                <tr v-for="f in mappingFields" :key="f.key">
                  <td>{{ f.label }} <code>{{ f.key }}</code></td>
                  <td>
                    <input v-model="syncConfig.field_mapping[f.key]" class="form-input mapping-input" :placeholder="f.key" />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 测试结果 -->
          <div v-if="testResult" class="config-section">
            <h4 class="config-section-title">测试结果</h4>
            <div v-if="!testResult.success" class="error-tip">{{ testResult.error }}</div>
            <div v-else>
              <div class="test-success">
                ✓ 连接成功，共获取到 {{ testResult.total }} 台主机
              </div>
              <div v-if="testResult.preview && testResult.preview.length" class="preview-table">
                <table class="table">
                  <thead>
                    <tr><th>名称</th><th>主机</th><th>标签</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="(p, i) in testResult.preview" :key="i">
                      <td>{{ p.name }}</td><td>{{ p.host }}</td>
                      <td>{{ p.tags }}</td>
                    </tr>
                  </tbody>
                </table>
                <p v-if="testResult.total > 10" class="text-light" style="font-size:12px;margin-top:4px;">
                  仅显示前 10 条，共 {{ testResult.total }} 条
                </p>
              </div>
            </div>
          </div>

          <div v-if="configError" class="error-tip">{{ configError }}</div>
        </div>
        <div class="modal-foot">
          <button class="btn btn-outline" :disabled="testingConfig" @click="testSync">
            {{ testingConfig ? '测试中…' : '🔍 测试连接' }}
          </button>
          <div class="modal-foot-right">
            <button class="btn btn-ghost" @click="showSyncConfig = false">取消</button>
            <button class="btn btn-primary" :disabled="savingConfig" @click="saveSyncConfig">
              {{ savingConfig ? '保存中…' : '保存配置' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 编辑凭据弹窗 -->
    <div v-if="showEditCred" class="modal-mask" @click.self="showEditCred = false">
      <div class="modal">
        <div class="modal-head">
          <div>
            <h3 class="modal-title">编辑 SSH 凭据</h3>
            <p class="modal-sub">修改凭据信息</p>
          </div>
          <button class="modal-close" @click="showEditCred = false">×</button>
        </div>
        <div class="modal-body">
          <div class="form-grid">
            <div class="form-group">
              <label class="form-label">名称 *</label>
              <input v-model="editCredForm.name" class="form-input" />
            </div>
            <div class="form-group">
              <label class="form-label">端口</label>
              <input v-model.number="editCredForm.port" class="form-input" type="number" />
            </div>
            <div class="form-group">
              <label class="form-label">用户名 *</label>
              <input v-model="editCredForm.username" class="form-input" />
            </div>
            <div class="form-group">
              <label class="form-label">认证方式</label>
              <select v-model="editCredForm.auth_type" class="form-input">
                <option value="password">密码</option>
                <option value="key">密钥</option>
              </select>
            </div>
            <!-- 密码认证：显示密码输入框 -->
            <div v-if="editCredForm.auth_type === 'password'" class="form-group">
              <label class="form-label">密码</label>
              <input v-model="editCredForm.password" class="form-input" type="password" placeholder="留空则不修改" />
            </div>
            <!-- 密钥认证：显示密钥内容输入框 -->
            <div v-else class="form-group full">
              <label class="form-label">密钥内容</label>
              <textarea v-model="editCredForm.key_content" class="form-textarea" rows="5" placeholder="留空则不修改，粘贴 PEM 格式私钥内容"></textarea>
            </div>
            <div class="form-group full">
              <label class="form-label">描述</label>
              <input v-model="editCredForm.description" class="form-input" />
            </div>
          </div>
          <div v-if="editCredError" class="error-tip">{{ editCredError }}</div>
        </div>
        <div class="modal-foot">
          <span></span>
          <div class="modal-foot-right">
            <button class="btn btn-ghost" @click="showEditCred = false">取消</button>
            <button class="btn btn-primary" :disabled="savingCred" @click="saveEditCred">
              {{ savingCred ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 操作结果弹窗 -->
    <div v-if="resultModal.show" class="modal-mask" @click.self="closeResult">
      <div class="modal">
        <div class="modal-head">
          <div>
            <h3 class="modal-title">{{ resultModal.title }}</h3>
            <p class="modal-sub">服务器：{{ resultModal.serverName }}</p>
          </div>
          <button class="modal-close" @click="closeResult">×</button>
        </div>
        <div class="modal-body">
          <div v-if="resultModal.loading" class="state-tip">正在检查，请稍候…</div>
          <div v-else-if="resultModal.error" class="state-tip error">{{ resultModal.error }}</div>
          <pre v-else class="result-pre">{{ JSON.stringify(resultModal.data, null, 2) }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.servers-page { display: flex; flex-direction: column; gap: var(--spacing-lg); }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.page-head-actions { display: flex; gap: 10px; flex-shrink: 0; }
.page-title { font-size: 20px; font-weight: 600; }
.page-sub { margin-top: 4px; font-size: 13px; color: var(--color-text-secondary); }
.card-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; }

/* 表单网格（通用） */
.form-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--spacing-md); }
.form-grid .full { grid-column: 1 / -1; }
.add-form .form-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--spacing-md); }
.add-form .full { grid-column: 1 / -1; }
.form-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 8px; }
.error-tip { margin: 8px 0; padding: 10px 12px; border-radius: var(--radius-sm); background: rgba(239, 68, 68, 0.08); color: var(--color-danger); font-size: 13px; }
.state-tip { padding: 30px 0; text-align: center; color: var(--color-text-secondary); }
.state-tip.error { color: var(--color-danger); }
.table-wrap { overflow-x: auto; }
.cell-name { font-weight: 600; }
.tag { margin-right: 4px; }
.action-btns { display: flex; gap: 6px; flex-wrap: wrap; }

/* SSH 凭据管理卡片 */
.cred-card { padding: 16px 18px; }
.cred-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; cursor: pointer; }
.cred-head-info { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.cred-count { font-size: 12px; color: var(--color-text-secondary); }
.cred-body { margin-top: 16px; }
.cred-add { padding: 14px; border: 1px dashed var(--color-border-light); border-radius: var(--radius-sm); margin-bottom: 16px; background: rgba(99, 102, 241, 0.03); }
.bind-select { padding: 5px 8px; font-size: 13px; min-width: 130px; max-width: 180px; border-radius: var(--radius-sm); }
.bind-select-unbound { color: var(--color-text-secondary); }
.default-cred-hint { display: block; margin-top: 2px; font-size: 11px; color: var(--color-text-light); }

/* 同步结果 */
.sync-result { padding: 14px 18px; border-left: 4px solid #10b981; }
.sync-result.sync-error { border-left-color: var(--color-danger); }
.sync-result-head { display: flex; align-items: center; gap: 8px; }
.sync-icon { font-size: 16px; font-weight: 600; }
.sync-result:not(.sync-error) .sync-icon { color: #10b981; }
.sync-result.sync-error .sync-icon { color: var(--color-danger); }
.sync-title { font-weight: 600; font-size: 15px; }
.sync-close { margin-left: auto; font-size: 18px; color: var(--color-text-light); padding: 0 4px; }
.sync-close:hover { color: var(--color-text); }
.sync-detail { margin-top: 4px; font-size: 13px; color: var(--color-text-secondary); }
.sync-errors { margin-top: 6px; font-size: 12px; color: var(--color-danger); }

/* 同步配置弹窗 */
.sync-modal { max-width: 720px; }
.modal-body { padding: 18px 22px; overflow-y: auto; }
.config-section { margin-bottom: 20px; }
.config-section-title { font-size: 14px; font-weight: 600; margin-bottom: 10px; color: var(--color-text); }
.config-section-hint { font-size: 12px; color: var(--color-text-secondary); margin-bottom: 8px; }
.form-hint { font-size: 11px; color: var(--color-text-light); margin-top: 3px; display: block; }
.mapping-table { margin-top: 8px; }
.mapping-table code { font-size: 11px; color: var(--color-text-light); }
.mapping-input { padding: 6px 10px; font-size: 13px; }
.test-success { padding: 10px 12px; border-radius: var(--radius-sm); background: rgba(16, 185, 129, 0.08); color: #059669; font-size: 13px; margin-bottom: 8px; }
.preview-table { margin-top: 8px; }
.modal-foot { display: flex; align-items: center; justify-content: space-between; padding: 14px 22px; border-top: 1px solid var(--color-border-light); gap: 12px; }
.modal-foot-right { display: flex; gap: 8px; }

/* 弹窗通用 */
.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.5); display: flex; align-items: center; justify-content: center; z-index: 100; padding: 20px; }
.modal { width: 100%; max-width: 640px; max-height: 85vh; background: #fff; border-radius: var(--radius-md); display: flex; flex-direction: column; box-shadow: var(--shadow-lg); }
.modal-head { display: flex; align-items: flex-start; justify-content: space-between; padding: 18px 22px; border-bottom: 1px solid var(--color-border-light); }
.modal-title { font-size: 16px; font-weight: 600; }
.modal-sub { font-size: 12px; color: var(--color-text-secondary); margin-top: 2px; }
.modal-close { font-size: 24px; line-height: 1; color: var(--color-text-secondary); padding: 0 6px; }
.modal-close:hover { color: var(--color-text); }
.result-pre { background: #0f172a; color: #e2e8f0; padding: 16px; border-radius: var(--radius-sm); font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 12px; line-height: 1.6; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }

@media (max-width: 640px) {
  .form-grid { grid-template-columns: 1fr; }
  .add-form .form-grid { grid-template-columns: 1fr; }
  .page-head-actions { flex-wrap: wrap; }
  .bind-select { min-width: 0; max-width: none; }
}
</style>
