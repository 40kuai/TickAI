# 🎫 TickAI

> AI-Powered Ops Ticket Platform - AI 驱动的智能运维工单平台

让 AI 接管繁琐运维，你专注于真正重要的事。

---

## ✨ 特性

| 模块 | 功能 |
|------|------|
| 🖥️ **服务器管理** | SSH 连接、磁盘检查、资源监控、服务状态 |
| ☸️ **Kubernetes 集成** | 节点、Pod、Deployment、Service 检查，事件分析 |
| 🔍 **LDAP 查询** | 用户信息查询（支持 username/email/uid） |
| 💬 **AI 对话引擎** | 自然语言提问，自动调用工具回答 |
| 📊 **审计日志** | 完整的操作记录和运行统计 |
| 🔒 **安全认证** | JWT + HttpOnly Cookie，bcrypt 密码哈希 |

---

## 🏗️ 架构

```
TickAI/
├── api/                        # FastAPI 后端
│   ├── main.py                # 应用入口 + 静态文件托管
│   ├── deps.py                # JWT 认证依赖
│   ├── auth_routes.py         # 登录/登出/用户信息
│   ├── server_routes.py       # 服务器管理 API
│   ├── chat_routes.py         # LLM 对话 API
│   ├── tool_routes.py         # 工具浏览/执行 API
│   └── history_routes.py      # 历史记录 API
├── frontend/                   # Vue 3 前端
│   └── src/
│       ├── views/             # 6 个页面
│       ├── stores/            # Pinia 状态管理
│       ├── router/            # Vue Router + 路由守卫
│       ├── api/               # Axios 实例
│       ├── layouts/           # 主布局
│       └── styles/            # 全局样式
├── hermes/                     # 核心业务逻辑
│   ├── agents/                # AI Agent
│   ├── config/                # 配置管理
│   ├── core/                  # LLM 客户端 + 工具调用
│   ├── data/                  # 数据层 (SQLAlchemy)
│   ├── i18n/                  # 国际化
│   ├── skills/                # 技能库
│   └── tools/                 # 工具注册表（SSH/K8s/LDAP）
├── data/                       # 运行时数据（SQLite DB）
├── Dockerfile                  # 多阶段构建（Node + Python）
├── docker-compose.yml          # 容器编排
├── .env.example                # 配置模板
├── start.sh                    # 启动脚本
├── stop.sh                     # 停止脚本
└── restart.sh                  # 重启脚本
```

**技术栈**：
- 前端：Vue 3 + Vite + Vue Router + Pinia + Axios
- 后端：FastAPI + Uvicorn + JWT (python-jose)
- 数据：SQLAlchemy + SQLite
- 认证：HttpOnly Cookie + JWT + bcrypt

---

## 🚀 快速开始

### 方式一：本地开发（前后端分离）

#### 1. 环境配置

```bash
cd ai

# Python 后端
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Vue 前端
cd frontend
npm install
cd ..
```

#### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入 TOKENHUB_API_KEY 等配置
```

#### 3. 启动服务

```bash
# 一键启动前后端
./start.sh

# 或分别启动：
# 后端 (端口 8000)
.venv/bin/uvicorn api.main:app --reload --port 8000

# 前端 (端口 5173)
cd frontend && npm run dev
```

访问：
- **前端 UI**：http://localhost:5173
- **后端 API**：http://localhost:8000
- **API 文档**：http://localhost:8000/docs

### 方式二：Docker 部署

```bash
# 配置环境变量
cp .env.example .env
# 编辑 .env

# 构建并启动
docker compose up -d --build

# 查看日志
docker logs -f tickai

# 停止
docker compose down
```

访问：http://localhost:8000

> Docker 模式下 Vue 构建产物由 FastAPI 托管，只需访问 8000 端口。

---

## 📄 页面功能

### 1. 🏠 首页（Dashboard）
- 运行统计：总次数、成功/失败数、成功率
- 最近运行记录

### 2. 🖥️ 服务器管理
- 添加/删除服务器
- 快捷操作：磁盘检查、资源监控、服务状态

### 3. 💬 AI 对话
- 自然语言提问，AI 自动调用工具
- 多轮对话，历史可追溯
- 工具调用过程可折叠查看

### 4. 📜 历史记录
- 所有工具运行记录
- 按服务器、状态筛选
- 分页浏览

### 5. 🛠️ 工具浏览
- 查看所有已注册工具及参数 Schema
- 在线测试执行

---

## 🔧 内置工具库

### SSH 工具
| 工具 | 功能 |
|------|------|
| `check_disk_usage` | 磁盘使用率检查 |
| `check_resources` | 系统资源（CPU、内存、进程） |
| `list_services` | systemd 服务列表与状态 |
| `check_resources_on_server` | 指定服务器资源检查 |
| `list_services_on_server` | 指定服务器服务列表 |

### Kubernetes 工具
| 工具 | 功能 |
|------|------|
| `check_k8s_nodes` | 节点状态检查 |
| `check_k8s_pods` | Pod 状态与事件 |
| `check_k8s_events` | 集群事件分析 |
| `check_k8s_deployments` | Deployment 检查 |
| `check_k8s_services` | Service 检查 |
| `list_k8s_contexts` | Kubeconfig 上下文 |

### LDAP 工具
| 工具 | 功能 |
|------|------|
| `ldap_search_user` | 用户信息查询 |

---

## 🔐 安全设计

| 层面 | 措施 |
|------|------|
| **认证** | JWT + HttpOnly Cookie，SameSite 防护 |
| **密码** | bcrypt 哈希（自动迁移旧 SHA-256） |
| **工具安全** | 所有工具严格只读 |
| **操作审计** | 所有操作 100% 记录 |
| **AI 身份** | System Prompt 注入，防止身份漂移 |
| **默认凭据** | 无硬编码，首次启动随机生成或环境变量配置 |

---

## 📝 开发指南

### 添加新工具

```python
# 1. 在 hermes/tools/<toolset>/ 创建模块
# 2. 使用 registry 注册
from hermes.tools.registry import register

@register
def my_new_tool(param: str) -> dict:
    """工具描述"""
    return {"result": "..."}

# 3. 在 hermes/tools/__init__.py 导入模块（自动发现）
```

### 添加新 API 端点

```python
# api/my_routes.py
from fastapi import APIRouter, Depends
from api.deps import get_current_user

router = APIRouter(prefix="/api/my", tags=["my"])

@router.get("/")
async def list_items(user=Depends(get_current_user)):
    return {"items": []}

# 在 api/main.py 中注册
# app.include_router(my_routes.router)
```

### 添加新页面

```vue
<!-- frontend/src/views/MyPage.vue -->
<script setup>
import { ref } from 'vue'
</script>

<template>
  <div class="page">
    <h2>我的页面</h2>
  </div>
</template>
```

```js
// frontend/src/router/index.js 添加路由
{ path: '/my-page', component: () => import('@/views/MyPage.vue') }
```

---

## 📄 脚本说明

| 脚本 | 功能 |
|------|------|
| `start.sh` | 启动后端 (8000) + 前端 (5173) |
| `stop.sh` | 停止所有服务 |
| `restart.sh` | 重启服务 |

---

## License

MIT © TickAI Team
