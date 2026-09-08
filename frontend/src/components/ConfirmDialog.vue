<script setup>
// 通用确认弹窗：替代原生 confirm()，避免浏览器拦截对话框导致操作无响应。
defineProps({
  show: { type: Boolean, default: false },
  title: { type: String, default: '确认操作' },
  message: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  danger: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['confirm', 'cancel'])
</script>

<template>
  <div v-if="show" class="confirm-mask" @click.self="emit('cancel')">
    <div class="confirm-panel">
      <div class="confirm-head">
        <h3>{{ title }}</h3>
        <button class="btn btn-secondary btn-sm" @click="emit('cancel')">关闭</button>
      </div>
      <div class="confirm-body">
        <p class="confirm-text">{{ message }}</p>
        <div class="confirm-actions">
          <button class="btn btn-outline" :disabled="loading" @click="emit('cancel')">取消</button>
          <button
            class="btn"
            :class="danger ? 'btn-danger' : 'btn-primary'"
            :disabled="loading"
            @click="emit('confirm')"
          >
            {{ loading ? '处理中…' : confirmText }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.confirm-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 24px;
}
.confirm-panel {
  background: var(--color-card);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  width: min(460px, 100%);
}
.confirm-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--color-border-light);
}
.confirm-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.confirm-body {
  padding: 16px 20px;
}
.confirm-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-all;
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
</style>
