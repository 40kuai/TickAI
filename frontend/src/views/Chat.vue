<script setup>
import { ref, reactive, computed, onMounted, nextTick, watch } from 'vue'
import { marked } from 'marked'
import api from '@/api'

// 配置 Markdown 渲染
marked.setOptions({ breaks: true, gfm: true })

const conversations = ref([])
const currentId = ref(null)
const messages = ref([])
const inputMsg = ref('')
const sending = ref(false)
const loadingList = ref(false)
const loadingDetail = ref(false)
const errorMsg = ref('')

// 工具调用折叠状态
const expandedTools = ref({})

// 对话区引用
const chatScrollRef = ref(null)

const currentConv = computed(
  () => conversations.value.find((c) => c.id === currentId.value) || null
)

// 加载对话列表
async function loadConversations() {
  loadingList.value = true
  try {
    const res = await api.get('/chat/conversations')
    conversations.value = res.data || []
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '加载对话列表失败'
  } finally {
    loadingList.value = false
  }
}

// 选择对话
async function selectConversation(id) {
  currentId.value = id
  messages.value = []
  loadingDetail.value = true
  errorMsg.value = ''
  try {
    const res = await api.get(`/chat/conversations/${id}`)
    const data = res.data || {}
    let msgs = data.messages_json
    if (typeof msgs === 'string') {
      try { msgs = JSON.parse(msgs) } catch { msgs = [] }
    }
    if (!Array.isArray(msgs)) {
      msgs = data.messages || []
    }
    messages.value = normalizeMessages(msgs)
    await nextTick()
    scrollToBottom()
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '加载对话详情失败'
  } finally {
    loadingDetail.value = false
  }
}

// 规整消息结构
function normalizeMessages(list) {
  return list
    .filter((m) => m.role !== 'system' && m.role !== 'tool')
    .map((m) => ({
      role: m.role === 'assistant' ? 'assistant' : 'user',
      content: m.content || '',
      tool_calls: extractToolCalls(m),
      streaming: false,
    }))
}

// 提取工具调用信息
function extractToolCalls(m) {
  if (!m.tool_calls) return []
  return m.tool_calls.map((tc) => {
    const fn = tc.function || tc
    let args = fn.arguments
    if (typeof args === 'string') {
      try { args = JSON.parse(args) } catch { /* keep string */ }
    }
    return {
      name: fn.name || tc.name || 'tool',
      args: args || {},
      id: tc.id,
    }
  })
}

// 新建对话
function newConversation() {
  currentId.value = null
  messages.value = []
  errorMsg.value = ''
}

// 删除对话
async function deleteConversation(id, e) {
  e.stopPropagation()
  if (!confirm('确定删除该对话吗？')) return
  try {
    await api.delete(`/chat/conversations/${id}`)
    if (currentId.value === id) newConversation()
    await loadConversations()
  } catch (err) {
    alert(err.response?.data?.detail || '删除失败')
  }
}

// 渲染 Markdown
function renderMarkdown(content) {
  if (!content) return ''
  try {
    return marked(content)
  } catch {
    return content
  }
}

// 发送消息（SSE 流式）
async function sendMessage() {
  const text = inputMsg.value.trim()
  if (!text || sending.value) return
  sending.value = true
  errorMsg.value = ''

  // 添加用户消息
  messages.value.push({ role: 'user', content: text, tool_calls: [], streaming: false })
  inputMsg.value = ''
  await nextTick()
  scrollToBottom()

  // 添加 AI 占位消息（流式填充）
  const aiMsg = reactive({
    role: 'assistant',
    content: '',
    tool_calls: [],
    streaming: true,
    status: '正在思考…',
  })
  messages.value.push(aiMsg)
  await nextTick()
  scrollToBottom()

  try {
    // 用 fetch 消费 SSE
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({
        message: text,
        conversation_id: currentId.value || undefined,
      }),
    })

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}))
      throw new Error(errData.detail || `HTTP ${res.status}`)
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // 解析 SSE 事件（以 \n\n 分隔）
      const parts = buffer.split('\n\n')
      buffer = parts.pop() // 保留不完整的部分

      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data: ')) continue
        const data = line.slice(6)
        if (data === '[DONE]') continue

        try {
          const evt = JSON.parse(data)
          handleSSEEvent(evt, aiMsg)
        } catch {
          // 忽略解析错误
        }
        await nextTick()
        scrollToBottom()
      }
    }

    aiMsg.streaming = false
    // 刷新侧边列表
    loadConversations()
  } catch (err) {
    errorMsg.value = err.message || '发送失败，请重试'
    // 移除空的 AI 占位消息
    if (!aiMsg.content && !aiMsg.tool_calls.length) {
      messages.value = messages.value.filter((m) => m !== aiMsg)
    } else {
      aiMsg.streaming = false
    }
  } finally {
    sending.value = false
  }
}

// 处理 SSE 事件
function handleSSEEvent(evt, aiMsg) {
  switch (evt.type) {
    case 'round_start':
      if (!aiMsg.content) aiMsg.status = '正在思考…'
      break
    case 'chunk':
      aiMsg.content += evt.content || ''
      aiMsg.status = ''
      break
    case 'tool_call_start':
      aiMsg.tool_calls.push({
        name: evt.name,
        args: evt.args || {},
        id: evt.id,
        running: true,
      })
      aiMsg.status = `正在调用工具：${evt.name}…`
      break
    case 'tool_call_end':
      {
        const tc = aiMsg.tool_calls.find((t) => t.running && t.name === evt.name)
        if (tc) {
          tc.running = false
          tc.result = evt.result_preview || ''
          tc.elapsed_ms = evt.elapsed_ms
        }
        const stillRunning = aiMsg.tool_calls.some((t) => t.running)
        if (!stillRunning && !aiMsg.content) {
          aiMsg.status = '正在整理结果…'
        }
      }
      break
    case 'done':
      aiMsg.content = evt.reply || aiMsg.content
      aiMsg.streaming = false
      aiMsg.status = ''
      if (evt.conversation_id) {
        currentId.value = evt.conversation_id
      }
      // 合并 tool_calls 诊断信息
      if (evt.tool_calls && evt.tool_calls.length) {
        aiMsg.tool_calls = evt.tool_calls.map((tc) => ({
          name: tc.name,
          args: tc.args || {},
          result: tc.result_preview || '',
          elapsed_ms: tc.elapsed_ms,
        }))
      }
      break
    case 'error':
      errorMsg.value = evt.message || 'AI 处理出错'
      aiMsg.streaming = false
      break
  }
}

// 滚动到底部
function scrollToBottom() {
  const el = chatScrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

// 切换工具调用展开
function toggleTools(idx) {
  expandedTools.value[idx] = !expandedTools.value[idx]
}

// 格式化时间
function formatTime(t) {
  if (!t) return ''
  try {
    return new Date(t).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return t
  }
}

// 回车发送
function onEnter(e) {
  if (e.shiftKey) return
  e.preventDefault()
  sendMessage()
}

// 监听消息变化自动滚动
watch(messages, () => {
  nextTick(scrollToBottom)
}, { deep: true })

onMounted(loadConversations)
</script>

<template>
  <div class="chat-page">
    <!-- 左侧对话列表 -->
    <aside class="conv-panel">
      <div class="conv-head">
        <span class="conv-title">对话列表</span>
        <button class="btn btn-primary btn-sm" @click="newConversation">+ 新建</button>
      </div>
      <div class="conv-list">
        <div v-if="loadingList" class="state-tip">加载中…</div>
        <div v-else-if="!conversations.length" class="state-tip">暂无对话</div>
        <a
          v-for="c in conversations"
          :key="c.id"
          class="conv-item"
          :class="{ active: currentId === c.id }"
          @click="selectConversation(c.id)"
        >
          <div class="conv-item-main">
            <div class="conv-item-title">{{ c.title || '未命名对话' }}</div>
            <div class="conv-item-meta">{{ formatTime(c.updated_at) }}</div>
          </div>
          <div class="conv-item-side">
            <span v-if="c.total_runs" class="badge badge-gray">{{ c.total_runs }}</span>
            <button class="del-btn" title="删除" @click="deleteConversation(c.id, $event)">×</button>
          </div>
        </a>
      </div>
    </aside>

    <!-- 右侧对话区 -->
    <section class="chat-main">
      <div class="chat-header">
        <span class="chat-title">
          {{ currentConv?.title || (currentId ? '对话' : '新对话') }}
        </span>
      </div>

      <div ref="chatScrollRef" class="chat-body">
        <div v-if="loadingDetail" class="state-tip">加载中…</div>
        <div v-else-if="errorMsg" class="state-tip error">{{ errorMsg }}</div>
        <div v-else-if="!messages.length" class="empty-chat">
          <div class="empty-icon">✦</div>
          <p>开始与 AI 对话，输入你的运维问题</p>
        </div>

        <template v-else>
          <div
            v-for="(m, i) in messages"
            :key="i"
            class="msg-row"
            :class="m.role === 'user' ? 'row-user' : 'row-ai'"
          >
            <div class="msg-avatar" :class="m.role === 'user' ? 'avatar-user' : 'avatar-ai'">
              {{ m.role === 'user' ? '我' : 'AI' }}
            </div>
            <div class="msg-content">
              <!-- 工具调用（折叠） -->
              <div v-if="m.tool_calls && m.tool_calls.length" class="tool-block">
                <button class="tool-toggle" @click="toggleTools(i)">
                  <span class="tool-icon">⚒</span>
                  工具调用（{{ m.tool_calls.length }}）
                  <span v-if="m.tool_calls.some(t => t.running)" class="tool-running">执行中…</span>
                  <span class="tool-arrow">{{ expandedTools[i] ? '▾' : '▸' }}</span>
                </button>
                <div v-if="expandedTools[i]" class="tool-list">
                  <div v-for="(tc, j) in m.tool_calls" :key="j" class="tool-item">
                    <div class="tool-item-head">
                      <span class="badge" :class="tc.running ? 'badge-warning' : 'badge-info'">
                        {{ tc.name }}
                      </span>
                      <span v-if="tc.elapsed_ms" class="tool-time">{{ tc.elapsed_ms }}ms</span>
                      <span v-if="tc.running" class="tool-spinner">⏳</span>
                    </div>
                    <pre class="tool-args">{{ JSON.stringify(tc.args, null, 2) }}</pre>
                    <pre v-if="tc.result" class="tool-result">{{ tc.result }}</pre>
                  </div>
                </div>
              </div>
              <!-- 文本内容（Markdown 渲染） -->
              <div v-if="m.content || !m.streaming" class="msg-bubble" :class="m.role === 'user' ? 'bubble-user' : 'bubble-ai'">
                <div v-if="m.role === 'user'" class="msg-text">{{ m.content }}</div>
                <div v-else class="msg-markdown" v-html="renderMarkdown(m.content)"></div>
              </div>
              <!-- 等待状态提示 -->
              <div v-if="m.streaming && m.status && !m.content" class="msg-bubble bubble-ai status-bubble">
                <span class="status-icon">
                  <span class="dot"></span><span class="dot"></span><span class="dot"></span>
                </span>
                <span class="status-text">{{ m.status }}</span>
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- 输入区 -->
      <div class="chat-input">
        <textarea
          v-model="inputMsg"
          class="input-area"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          rows="2"
          @keydown.enter="onEnter"
        ></textarea>
        <button class="btn btn-primary send-btn" :disabled="sending || !inputMsg.trim()" @click="sendMessage">
          {{ sending ? '发送中…' : '发送' }}
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  height: 100%;
  min-height: calc(100vh - 100px);
  gap: var(--spacing-md);
}

/* 左侧对话列表 */
.conv-panel {
  width: 260px;
  flex-shrink: 0;
  background: #fff;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.conv-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-border-light);
}
.conv-title { font-weight: 600; }
.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.conv-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: var(--transition);
  gap: 8px;
}
.conv-item:hover { background: var(--color-border-light); }
.conv-item.active { background: rgba(102, 126, 234, 0.1); }
.conv-item-main { flex: 1; min-width: 0; }
.conv-item-title {
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-item-meta {
  font-size: 11px;
  color: var(--color-text-light);
  margin-top: 2px;
}
.conv-item-side {
  display: flex;
  align-items: center;
  gap: 6px;
}
.del-btn {
  font-size: 16px;
  color: var(--color-text-light);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}
.del-btn:hover {
  color: var(--color-danger);
  background: rgba(239, 68, 68, 0.1);
}

/* 右侧对话区 */
.chat-main {
  flex: 1;
  min-width: 0;
  background: #fff;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.chat-header {
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border-light);
}
.chat-title { font-weight: 600; }
.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
.state-tip {
  padding: 30px 0;
  text-align: center;
  color: var(--color-text-secondary);
}
.state-tip.error { color: var(--color-danger); }
.empty-chat {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 60px 0;
}
.empty-icon {
  font-size: 40px;
  margin-bottom: 12px;
  color: var(--color-primary);
}

/* 消息行 */
.msg-row {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
}
.row-user { flex-direction: row-reverse; }
.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.avatar-user {
  background: var(--color-primary);
  color: #fff;
}
.avatar-ai {
  background: var(--gradient-primary);
  color: #fff;
}
.msg-content {
  max-width: 75%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.row-user .msg-content { align-items: flex-end; }

/* 工具调用块 */
.tool-block {
  width: 100%;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-border-light);
  overflow: hidden;
}
.tool-toggle {
  width: 100%;
  text-align: left;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--color-text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.tool-toggle:hover { background: var(--color-border); }
.tool-icon { color: var(--color-primary); }
.tool-running {
  color: var(--color-warning, #f59e0b);
  font-size: 11px;
}
.tool-arrow { margin-left: auto; }
.tool-list { padding: 8px 12px 12px; }
.tool-item { margin-bottom: 10px; }
.tool-item:last-child { margin-bottom: 0; }
.tool-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.tool-time {
  font-size: 11px;
  color: var(--color-text-light);
}
.tool-spinner {
  font-size: 12px;
}
.tool-args,
.tool-result {
  margin-top: 6px;
  background: #0f172a;
  color: #e2e8f0;
  padding: 10px;
  border-radius: var(--radius-sm);
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.badge-warning {
  background: rgba(245, 158, 11, 0.15);
  color: #d97706;
}

/* 气泡 */
.msg-bubble {
  padding: 10px 14px;
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}
.bubble-user {
  background: var(--gradient-primary);
  color: #fff;
  border-bottom-right-radius: 4px;
}
.bubble-ai {
  background: var(--color-border-light);
  color: var(--color-text);
  border-bottom-left-radius: 4px;
}
.msg-text { white-space: pre-wrap; }

/* Markdown 渲染样式 */
.msg-markdown :deep(h1),
.msg-markdown :deep(h2),
.msg-markdown :deep(h3) {
  margin: 8px 0 4px;
  font-size: 15px;
}
.msg-markdown :deep(p) { margin: 4px 0; }
.msg-markdown :deep(ul),
.msg-markdown :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.msg-markdown :deep(li) { margin: 2px 0; }
.msg-markdown :deep(code) {
  background: rgba(0,0,0,0.08);
  padding: 2px 5px;
  border-radius: 3px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
}
.msg-markdown :deep(pre) {
  background: #0f172a;
  color: #e2e8f0;
  padding: 10px 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 6px 0;
}
.msg-markdown :deep(pre code) {
  background: none;
  padding: 0;
  font-size: 12px;
}
.msg-markdown :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 6px 0;
  font-size: 13px;
}
.msg-markdown :deep(th),
.msg-markdown :deep(td) {
  border: 1px solid var(--color-border);
  padding: 6px 10px;
  text-align: left;
}
.msg-markdown :deep(th) {
  background: var(--color-border-light);
  font-weight: 600;
}
.msg-markdown :deep(blockquote) {
  border-left: 3px solid var(--color-primary);
  margin: 6px 0;
  padding: 4px 12px;
  color: var(--color-text-secondary);
}
.msg-markdown :deep(a) {
  color: var(--color-primary);
  text-decoration: none;
}
.msg-markdown :deep(a:hover) { text-decoration: underline; }

/* 等待状态提示 */
.status-bubble {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
}
.status-icon {
  display: inline-flex;
  gap: 3px;
}
.status-icon .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-light);
  animation: pulse 1.4s infinite;
}
.status-icon .dot:nth-child(2) { animation-delay: 0.2s; }
.status-icon .dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes pulse {
  0%, 60%, 100% { opacity: 0.3; transform: scale(0.8); }
  30% { opacity: 1; transform: scale(1); }
}
.status-text {
  font-size: 13px;
  color: var(--color-text-secondary);
}

/* 输入区 */
.chat-input {
  display: flex;
  gap: 10px;
  padding: 14px 18px;
  border-top: 1px solid var(--color-border-light);
}
.input-area {
  flex: 1;
  resize: none;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  outline: none;
  font-size: 14px;
  transition: var(--transition);
}
.input-area:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.15);
}
.send-btn {
  align-self: flex-end;
  padding: 10px 22px;
}

/* 响应式 */
@media (max-width: 768px) {
  .chat-page { flex-direction: column; }
  .conv-panel { width: 100%; max-height: 180px; }
  .msg-content { max-width: 85%; }
}
</style>
