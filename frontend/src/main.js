import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './styles/main.css'

// 提前应用主题，避免页面闪烁
const savedTheme = localStorage.getItem('tickai-theme') || 'light'
document.documentElement.setAttribute('data-theme', savedTheme)

// 创建 Vue 应用，挂载 Pinia 与路由
const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
