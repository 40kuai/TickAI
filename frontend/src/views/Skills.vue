<script setup>
import { ref, onMounted } from 'vue'
import api from '@/api'

const skills = ref([])
const loading = ref(false)
const errorMsg = ref('')

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
</style>
