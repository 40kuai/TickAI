<script setup>
import { ref, onMounted } from 'vue'
import { marked } from 'marked'
import api from '@/api'

const skills = ref([])
const loading = ref(false)
const errorMsg = ref('')

// 详情弹窗状态
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const detailError = ref('')

// P1: 执行反馈 + 进化历史
const feedbackList = ref([])
const versionsList = ref([])
const feedbackNotes = ref({})
const feedbackBusy = ref({})
const feedbackMsg = ref('')

// 决策状态映射
const DECISION_LABELS = {
  pending: '待标注',
  accepted: '已接受',
  rejected: '已拒绝',
}
const REASON_LABELS = {
  initial: '初始版本',
  manual: '手动修改',
  auto_evolve: '自动进化',
}

// 触发器标签映射
const TRIGGER_LABELS = {
  scheduled_daily: '定时巡检',
  scheduled_weekly: '定时巡检',
  user_initiated: '用户触发',
}

function triggerLabel(t) {
  return TRIGGER_LABELS[t] || t || '-'
}

// 严重级别标签映射
const SEVERITY_LABELS = {
  info: '信息',
  warning: '警告',
  critical: '严重',
}

function severityLabel(sev) {
  return SEVERITY_LABELS[sev] || sev || '-'
}

function severityBadge(sev) {
  const s = String(sev || '').toLowerCase()
  if (s === 'warning') return 'badge-warning'
  if (s === 'critical') return 'badge-critical'
  if (s === 'info') return 'badge-info'
  return 'badge-gray'
}

// 加载技能列表
async function loadSkills() {
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await api.get('/skills')
    const data = res.data || {}
    skills.value = data.skills || []
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '加载技能列表失败'
  } finally {
    loading.value = false
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

// 打开详情
async function openDetail(skill) {
  detail.value = null
  detailError.value = ''
  feedbackList.value = []
  versionsList.value = []
  feedbackNotes.value = {}
  feedbackMsg.value = ''
  detailVisible.value = true
  detailLoading.value = true
  try {
    const name = encodeURIComponent(skill.name)
    const [d, fb, vs] = await Promise.all([
      api.get(`/skills/${name}`),
      api.get(`/skills/${name}/feedback`),
      api.get(`/skills/${name}/versions`),
    ])
    detail.value = d.data
    feedbackList.value = fb.data?.feedback || []
    versionsList.value = vs.data?.versions || []
  } catch (err) {
    detailError.value = err.response?.data?.detail || '加载技能详情失败'
  } finally {
    detailLoading.value = false
  }
}

// 标注反馈（接受/拒绝）
async function markFeedback(outcome, decision) {
  const id = outcome.id
  if (feedbackBusy.value[id]) return
  feedbackBusy.value[id] = true
  feedbackMsg.value = ''
  try {
    await api.post(`/skills/${encodeURIComponent(detail.value.name)}/feedback/${id}`, {
      decision,
      notes: feedbackNotes.value[id] || '',
    })
    // 刷新反馈列表
    const fb = await api.get(`/skills/${encodeURIComponent(detail.value.name)}/feedback`)
    feedbackList.value = fb.data?.feedback || []
    feedbackMsg.value = `已标注为「${DECISION_LABELS[decision]}」`
  } catch (err) {
    feedbackMsg.value = err.response?.data?.detail || '标注失败'
  } finally {
    feedbackBusy.value[id] = false
  }
}

function closeDetail() {
  detailVisible.value = false
  detail.value = null
  detailError.value = ''
}

onMounted(loadSkills)
</script>

<template>
  <div class="skills-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">Skills 技能</h2>
        <p class="page-sub">查看当前可用的 AI 运维分析技能</p>
      </div>
      <button class="btn btn-outline" @click="loadSkills">刷新</button>
    </div>

    <div v-if="loading" class="state-tip card">加载中…</div>
    <div v-else-if="errorMsg" class="state-tip error card">{{ errorMsg }}</div>
    <div v-else-if="!skills.length" class="state-tip card">暂无可用技能</div>

    <div v-else class="skill-grid">
      <div v-for="skill in skills" :key="skill.name" class="skill-card">
        <div class="skill-head">
          <span class="skill-name">{{ skill.name }}</span>
          <div class="skill-badges">
            <span class="badge badge-gray">{{ triggerLabel(skill.trigger) }}</span>
            <span class="badge" :class="severityBadge(skill.severity)">{{ severityLabel(skill.severity) }}</span>
          </div>
        </div>
        <p class="skill-desc">{{ skill.description || '暂无描述' }}</p>
        <div class="skill-path" :title="skill.path">{{ skill.path }}</div>
        <div class="skill-actions">
          <button class="btn btn-outline btn-sm" @click="openDetail(skill)">查看详情</button>
        </div>
      </div>
    </div>

    <!-- 详情弹窗 -->
    <div v-if="detailVisible" class="modal-overlay" @click.self="closeDetail">
      <div class="modal-box detail-modal">
        <div class="modal-header">
          <h3>{{ detail?.name || '技能详情' }}</h3>
          <button class="modal-close" @click="closeDetail">×</button>
        </div>
        <div class="modal-body">
          <div v-if="detailLoading" class="state-tip">加载中…</div>
          <div v-else-if="detailError" class="state-tip error">{{ detailError }}</div>
          <div v-else-if="detail" class="detail-content">
            <div class="detail-meta">
              <span class="badge badge-gray">{{ triggerLabel(detail.trigger) }}</span>
              <span class="badge" :class="severityBadge(detail.severity)">{{ severityLabel(detail.severity) }}</span>
            </div>
            <p class="detail-desc">{{ detail.description || '暂无描述' }}</p>
            <div class="detail-body markdown-body" v-html="renderMarkdown(detail.body)"></div>

            <!-- P1: 执行反馈标注（进化学习信号） -->
            <div class="detail-section">
              <div class="section-title">执行反馈 <span class="section-count">({{ feedbackList.length }})</span></div>
              <div v-if="feedbackMsg" class="feedback-msg">{{ feedbackMsg }}</div>
              <div v-if="!feedbackList.length" class="empty-line">暂无执行记录，技能运行后将在此标注反馈</div>
              <div v-else class="feedback-list">
                <div v-for="fb in feedbackList" :key="fb.id" class="feedback-item">
                  <div class="feedback-head">
                    <span class="feedback-time">{{ fb.run_at?.slice(0, 16).replace('T', ' ') || '-' }}</span>
                    <span class="badge badge-gray">v{{ fb.skill_version }}</span>
                    <span class="badge" :class="fb.user_decision === 'accepted' ? 'badge-info' : (fb.user_decision === 'rejected' ? 'badge-critical' : 'badge-gray')">
                      {{ DECISION_LABELS[fb.user_decision] || fb.user_decision }}
                    </span>
                  </div>
                  <p class="feedback-summary">{{ fb.findings_summary || '（无摘要）' }}</p>
                  <div class="feedback-actions">
                    <input
                      v-model="feedbackNotes[fb.id]"
                      class="form-input feedback-note"
                      placeholder="备注（可选）"
                    />
                    <button
                      class="btn btn-sm btn-primary"
                      :disabled="feedbackBusy[fb.id]"
                      @click="markFeedback(fb, 'accepted')"
                    >接受</button>
                    <button
                      class="btn btn-sm btn-outline"
                      :disabled="feedbackBusy[fb.id]"
                      @click="markFeedback(fb, 'rejected')"
                    >拒绝</button>
                  </div>
                </div>
              </div>
            </div>

            <!-- P1: 进化历史 -->
            <div class="detail-section">
              <div class="section-title">进化历史 <span class="section-count">({{ versionsList.length }})</span></div>
              <div v-if="!versionsList.length" class="empty-line">暂无版本记录</div>
              <div v-else class="version-list">
                <div v-for="v in versionsList" :key="v.version" class="version-item">
                  <div class="version-head">
                    <span class="version-no">v{{ v.version }}</span>
                    <span class="badge badge-gray">{{ REASON_LABELS[v.reason] || v.reason }}</span>
                    <span class="version-time">{{ v.created_at?.slice(0, 16).replace('T', ' ') || '-' }}</span>
                  </div>
                  <pre v-if="v.diff" class="version-diff">{{ v.diff }}</pre>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.skills-page {
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

.skill-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--spacing-md);
}
.skill-card {
  background: var(--color-card);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.skill-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.skill-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-primary);
  word-break: break-all;
}
.skill-badges {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.skill-desc {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}
.skill-path {
  font-size: 11px;
  color: var(--color-text-light);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-actions {
  margin-top: auto;
  padding-top: 10px;
}

/* 严重级别徽章 */
.badge-warning {
  background: rgba(245, 158, 11, 0.12);
  color: #d97706;
}
.badge-critical {
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
}
.badge-info {
  background: rgba(14, 165, 233, 0.12);
  color: #0284c7;
}

/* 详情弹窗 */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal-box {
  background: var(--color-card, #fff);
  border-radius: var(--radius-md, 10px);
  box-shadow: var(--shadow-lg, 0 10px 30px rgba(0,0,0,0.25));
  width: 100%;
  max-width: 760px;
  max-height: 82vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.detail-modal {
  width: 760px;
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border, #eee);
  flex-shrink: 0;
}
.modal-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.modal-close {
  border: none;
  background: none;
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
  color: var(--color-text-secondary);
  padding: 0 4px;
}
.modal-close:hover {
  color: var(--color-danger);
}
.modal-body {
  padding: 20px;
  overflow-y: auto;
}
.detail-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.detail-meta {
  display: flex;
  gap: 6px;
}
.detail-desc {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}
.detail-body {
  border-top: 1px solid var(--color-border, #eee);
  padding-top: 14px;
}
.detail-section {
  border-top: 1px solid var(--color-border, #eee);
  padding-top: 14px;
}
.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 8px;
}
.section-count {
  font-weight: 400;
}
.empty-line {
  font-size: 12px;
  color: var(--color-text-light);
  padding: 6px 0;
}
.feedback-msg {
  font-size: 12px;
  color: var(--color-primary);
  margin-bottom: 6px;
}
.feedback-list,
.version-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.feedback-item,
.version-item {
  border: 1px solid var(--color-border-light, #e5e7eb);
  border-radius: var(--radius-sm, 6px);
  padding: 10px 12px;
}
.feedback-head,
.version-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.feedback-time,
.version-time {
  font-size: 11px;
  color: var(--color-text-light);
}
.feedback-summary {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--color-text);
  line-height: 1.5;
}
.feedback-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  align-items: center;
}
.feedback-note {
  flex: 1;
  min-width: 120px;
  padding: 5px 8px;
  font-size: 12px;
}
.version-no {
  font-weight: 600;
  font-size: 13px;
  color: var(--color-primary);
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.version-diff {
  margin-top: 6px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.5;
  background: var(--color-bg-code, #f8fafc);
  border-radius: 4px;
  padding: 8px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 120px;
  overflow-y: auto;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 12px 0 6px;
}
.markdown-body :deep(p) {
  margin: 6px 0;
  line-height: 1.7;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
}
.markdown-body :deep(li) {
  margin: 3px 0;
}
.markdown-body :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 12px;
}
.markdown-body :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  overflow-x: auto;
  margin: 8px 0;
}
.markdown-body :deep(pre code) {
  background: none;
  color: inherit;
  padding: 0;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  width: 100%;
  font-size: 13px;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--color-border, #e5e7eb);
  padding: 6px 10px;
  text-align: left;
}
.markdown-body :deep(th) {
  background: rgba(0, 0, 0, 0.03);
  font-weight: 600;
}
.markdown-body :deep(blockquote) {
  margin: 8px 0;
  padding-left: 12px;
  border-left: 3px solid var(--color-border, #ddd);
  color: var(--color-text-secondary);
}
</style>
