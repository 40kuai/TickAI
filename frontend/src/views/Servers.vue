<script setup>
import { ref, reactive, onMounted } from 'vue'
import api from '@/api'

const servers = ref([])
const loading = ref(false)
const errorMsg = ref('')

// 添加服务器表单
const showAdd = ref(false)
const form = reactive({
  name: '',
  host: '',
  port: 22,
  username: '',
  password: '',
  tags: '',
  notes: ''
})
const adding = ref(false)
const addError = ref('')

// 操作结果弹窗
const resultModal = reactive({
  show: false,
  title: '',
  loading: false,
  serverName: '',
  data: null,
  error: ''
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

// 提交添加
async function handleAdd() {
  addError.value = ''
  if (!form.name || !form.host || !form.username) {
    addError.value = '请填写名称、主机和用户名'
    return
  }
  adding.value = true
  try {
    const payload = {
      name: form.name,
      host: form.host,
      port: Number(form.port) || 22,
      username: form.username,
      password: form.password,
      tags: form.tags
        ? form.tags.split(',').map((t) => t.trim()).filter(Boolean)
        : [],
      notes: form.notes
    }
    await api.post('/servers', payload)
    // 重置表单并刷新列表
    Object.assign(form, {
      name: '', host: '', port: 22, username: '', password: '', tags: '', notes: ''
    })
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
    resultModal.error =
      err.response?.data?.detail || err.response?.data?.message || '操作失败'
  } finally {
    resultModal.loading = false
  }
}

function checkDisk(server) {
  runAction(server, 'check_disk', '磁盘检查')
}
function checkResources(server) {
  runAction(server, 'check_resources', '资源检查')
}
function listServices(server) {
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

// 关闭弹窗
function closeResult() {
  resultModal.show = false
}

onMounted(loadServers)
</script>

<template>
  <div class="servers-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">服务器管理</h2>
        <p class="page-sub">管理 SSH 服务器并执行磁盘、资源、服务检查</p>
      </div>
      <button class="btn btn-primary" @click="showAdd = !showAdd">
        {{ showAdd ? '收起' : '+ 添加服务器' }}
      </button>
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
          <label class="form-label">端口</label>
          <input v-model.number="form.port" class="form-input" type="number" placeholder="22" />
        </div>
        <div class="form-group">
          <label class="form-label">用户名 *</label>
          <input v-model="form.username" class="form-input" placeholder="例如：root" />
        </div>
        <div class="form-group">
          <label class="form-label">密码</label>
          <input v-model="form.password" class="form-input" type="password" placeholder="SSH 密码" />
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

    <!-- 服务器列表 -->
    <div class="card">
      <div v-if="loading" class="state-tip">加载中…</div>
      <div v-else-if="errorMsg" class="state-tip error">{{ errorMsg }}</div>
      <div v-else-if="!servers.length" class="state-tip">暂无服务器，点击右上角添加</div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>名称</th>
              <th>主机</th>
              <th>端口</th>
              <th>用户</th>
              <th>标签</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in servers" :key="s.id">
              <td class="cell-name">{{ s.name }}</td>
              <td>{{ s.host }}</td>
              <td>{{ s.port }}</td>
              <td>{{ s.user || s.username }}</td>
              <td>
                <span
                  v-for="t in s.tags || []"
                  :key="t"
                  class="badge badge-gray tag"
                >{{ t }}</span>
                <span v-if="!s.tags || !s.tags.length" class="text-light">-</span>
              </td>
              <td>
                <span
                  class="badge"
                  :class="s.is_active ? 'badge-success' : 'badge-gray'"
                >
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
.servers-page {
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
.add-form .form-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--spacing-md);
}
.add-form .full {
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
.tag {
  margin-right: 4px;
}
.action-btns {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

/* 弹窗 */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: 20px;
}
.modal {
  width: 100%;
  max-width: 640px;
  max-height: 80vh;
  background: #fff;
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
}
.modal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 18px 22px;
  border-bottom: 1px solid var(--color-border-light);
}
.modal-title {
  font-size: 16px;
  font-weight: 600;
}
.modal-sub {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 2px;
}
.modal-close {
  font-size: 24px;
  line-height: 1;
  color: var(--color-text-secondary);
  padding: 0 6px;
}
.modal-close:hover {
  color: var(--color-text);
}
.modal-body {
  padding: 18px 22px;
  overflow-y: auto;
}
.result-pre {
  background: #0f172a;
  color: #e2e8f0;
  padding: 16px;
  border-radius: var(--radius-sm);
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 640px) {
  .add-form .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
