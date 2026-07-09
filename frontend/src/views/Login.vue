<script setup>
import { ref, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const form = reactive({
  username: '',
  password: ''
})
const errorMsg = ref('')
const loading = ref(false)

// 登录提交
async function handleSubmit() {
  errorMsg.value = ''
  if (!form.username || !form.password) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    // 登录成功跳转：优先跳到 redirect 参数，否则首页
    const redirect = route.query.redirect
    router.replace(typeof redirect === 'string' ? redirect : '/')
  } catch (err) {
    errorMsg.value =
      err.response?.data?.detail ||
      err.response?.data?.message ||
      '登录失败，请检查用户名和密码'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <div class="brand-icon">◆</div>
        <h1 class="login-title">运维管理平台</h1>
        <p class="login-subtitle">登录以开始管理你的服务器</p>
      </div>

      <form class="login-form" @submit.prevent="handleSubmit">
        <div class="form-group">
          <label class="form-label">用户名</label>
          <input
            v-model.trim="form.username"
            class="form-input"
            type="text"
            placeholder="请输入用户名"
            autocomplete="username"
          />
        </div>
        <div class="form-group">
          <label class="form-label">密码</label>
          <input
            v-model="form.password"
            class="form-input"
            type="password"
            placeholder="请输入密码"
            autocomplete="current-password"
          />
        </div>

        <div v-if="errorMsg" class="error-tip">
          {{ errorMsg }}
        </div>

        <button class="btn btn-primary submit-btn" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--gradient-primary);
}
.login-card {
  width: 100%;
  max-width: 400px;
  background: #fff;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 40px 36px;
}
.login-header {
  text-align: center;
  margin-bottom: 32px;
}
.brand-icon {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  border-radius: var(--radius-md);
  background: var(--gradient-primary);
  color: #fff;
  font-size: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-md);
}
.login-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--color-text);
}
.login-subtitle {
  margin-top: 6px;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.error-tip {
  margin-bottom: 16px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: rgba(239, 68, 68, 0.08);
  color: var(--color-danger);
  font-size: 13px;
}
.submit-btn {
  width: 100%;
  padding: 12px;
  font-size: 15px;
  font-weight: 600;
}
</style>
