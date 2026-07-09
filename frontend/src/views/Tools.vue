<script setup>
import { ref, reactive, onMounted } from 'vue'
import api from '@/api'

const tools = ref([])
const loading = ref(false)
const errorMsg = ref('')

// 展开的工具卡片（按名称）
const expanded = ref({})
// 测试运行参数与结果
const runState = reactive({})

// 加载工具列表
async function loadTools() {
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await api.get('/tools')
    tools.value = res.data || []
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '加载工具列表失败'
  } finally {
    loading.value = false
  }
}

// 展开/收起
function toggle(tool) {
  const name = tool.name
  expanded.value[name] = !expanded.value[name]
  if (expanded.value[name] && !runState[name]) {
    // 初始化参数输入：根据 parameters schema 生成空对象
    const params = tool.parameters || {}
    const props = params.properties || params || {}
    const init = {}
    Object.keys(props).forEach((k) => {
      init[k] = ''
    })
    runState[name] = {
      args: init,
      running: false,
      result: null,
      error: ''
    }
  }
}

// 运行工具
async function runTool(tool) {
  const name = tool.name
  const state = runState[name]
  if (!state || state.running) return

  // 转换参数类型：尝试将纯数字字符串转数字
  const args = {}
  const props = (tool.parameters?.properties) || {}
  Object.entries(state.args).forEach(([k, v]) => {
    if (v === '' || v === null || v === undefined) return
    const type = props[k]?.type
    if (type === 'number' || type === 'integer') {
      const n = Number(v)
      args[k] = isNaN(n) ? v : n
    } else if (type === 'boolean') {
      args[k] = v === 'true' || v === true
    } else {
      // 尝试解析 JSON
      if (typeof v === 'string' && (v.startsWith('{') || v.startsWith('['))) {
        try {
          args[k] = JSON.parse(v)
          return
        } catch {
          /* 保持字符串 */
        }
      }
      args[k] = v
    }
  })

  state.running = true
  state.result = null
  state.error = ''
  try {
    const res = await api.post(`/tools/${name}/run`, { args })
    state.result = res.data
  } catch (err) {
    state.error =
      err.response?.data?.detail ||
      err.response?.data?.message ||
      '运行失败'
  } finally {
    state.running = false
  }
}

// 获取参数 schema 的字段列表
function paramFields(tool) {
  const params = tool.parameters || {}
  const props = params.properties || {}
  const required = params.required || []
  return Object.entries(props).map(([k, v]) => ({
    key: k,
    type: v.type || 'any',
    desc: v.description || '',
    required: required.includes(k)
  }))
}

onMounted(loadTools)
</script>

<template>
  <div class="tools-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">工具浏览</h2>
        <p class="page-sub">查看可用工具及其参数，并可展开测试运行</p>
      </div>
      <button class="btn btn-outline" @click="loadTools">刷新</button>
    </div>

    <div v-if="loading" class="state-tip card">加载中…</div>
    <div v-else-if="errorMsg" class="state-tip error card">{{ errorMsg }}</div>
    <div v-else-if="!tools.length" class="state-tip card">暂无可用工具</div>

    <div v-else class="tool-grid">
      <div v-for="tool in tools" :key="tool.name" class="tool-card">
        <div class="tool-head" @click="toggle(tool)">
          <div class="tool-head-main">
            <span class="tool-name">{{ tool.name }}</span>
            <p class="tool-desc">{{ tool.description || '暂无描述' }}</p>
          </div>
          <span class="tool-arrow">{{ expanded[tool.name] ? '▾' : '▸' }}</span>
        </div>

        <!-- 参数 schema -->
        <div v-if="expanded[tool.name]" class="tool-body">
          <div class="section-title">参数</div>
          <div v-if="!paramFields(tool).length" class="empty-line">该工具无需参数</div>
          <div v-else class="param-list">
            <div v-for="p in paramFields(tool)" :key="p.key" class="param-row">
              <div class="param-key">
                <code>{{ p.key }}</code>
                <span class="badge badge-gray">{{ p.type }}</span>
                <span v-if="p.required" class="badge badge-danger">必填</span>
              </div>
              <div class="param-desc">{{ p.desc }}</div>
              <input
                v-model="runState[tool.name].args[p.key]"
                class="form-input param-input"
                :placeholder="`输入 ${p.key}`"
              />
            </div>
          </div>

          <div class="tool-actions">
            <button
              class="btn btn-primary btn-sm"
              :disabled="runState[tool.name].running"
              @click="runTool(tool)"
            >
              {{ runState[tool.name].running ? '运行中…' : '测试运行' }}
            </button>
          </div>

          <div v-if="runState[tool.name].error" class="error-tip">
            {{ runState[tool.name].error }}
          </div>
          <div v-if="runState[tool.name].result" class="result-block">
            <div class="section-title">运行结果</div>
            <pre class="result-pre">{{ JSON.stringify(runState[tool.name].result, null, 2) }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tools-page {
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
.state-tip {
  padding: 30px;
  text-align: center;
  color: var(--color-text-secondary);
}
.state-tip.error {
  color: var(--color-danger);
}

.tool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: var(--spacing-md);
}
.tool-card {
  background: #fff;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.tool-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px;
  cursor: pointer;
  transition: var(--transition);
}
.tool-head:hover {
  background: var(--color-border-light);
}
.tool-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-primary);
}
.tool-desc {
  margin-top: 4px;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.5;
}
.tool-arrow {
  color: var(--color-text-light);
  flex-shrink: 0;
}

.tool-body {
  padding: 0 18px 18px;
  border-top: 1px solid var(--color-border-light);
}
.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 14px 0 8px;
}
.empty-line {
  font-size: 13px;
  color: var(--color-text-light);
  padding: 8px 0;
}
.param-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.param-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.param-key {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.param-key code {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 13px;
  color: var(--color-text);
  background: var(--color-border-light);
  padding: 1px 6px;
  border-radius: 4px;
}
.param-desc {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.param-input {
  padding: 7px 10px;
  font-size: 13px;
}
.tool-actions {
  margin-top: 14px;
}
.error-tip {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: rgba(239, 68, 68, 0.08);
  color: var(--color-danger);
  font-size: 13px;
}
.result-block {
  margin-top: 14px;
}
.result-pre {
  background: #0f172a;
  color: #e2e8f0;
  padding: 12px;
  border-radius: var(--radius-sm);
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 280px;
  overflow-y: auto;
}
</style>
