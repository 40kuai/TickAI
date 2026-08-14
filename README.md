# 🎫 TickAI

> AI-Powered Ops Ticket Platform - AI 驱动的智能运维工单平台

让 AI 接管繁琐运维，你专注于真正重要的事。

---

## ✨ 特性

| 模块 | 功能 |
|------|------|
| 🖥️ **服务器管理** | SSH 连接、磁盘检查、资源监控、服务状态 |
| 🔑 **SSH 凭据管理** | 可复用凭据（密码/密钥）、绑定到服务器、默认凭据 |
| 🔄 **外部主机同步** | 从外部 API 拉取主机列表，字段映射 + 分页 + 认证 |
| ☸️ **Kubernetes 集成** | 节点、Pod、Deployment、Service 检查，事件分析 |
| � **Prometheus 监控** | 通过 ARMS Prometheus 查询实时指标（即时/范围查询） |
| 🧠 **AI 技能系统** | 技能库 + 关键词自动触发 + 定时巡检 + 技能演化 |
| �🔍 **LDAP 查询** | 用户信息查询（支持 username/email/uid，账户状态解析） |
| 💬 **AI 对话引擎** | 自然语言提问，自动调用工具回答，多轮会话 |
| � **审计日志** | 完整的操作记录和运行统计 |
| 🔒 **安全认证** | JWT + HttpOnly Cookie，bcrypt 密码哈希 |

---

## 🏗️ 架构

```
TickAI/
├── api/                        # FastAPI 后端
│   ├── main.py                # 应用入口 + 静态文件托管
│   ├── deps.py                # JWT 认证依赖
│   ├── auth_routes.py         # 登录/登出/用户信息
│   ├── server_routes.py       # 服务器管理 + 外部主机同步
│   ├── ssh_credential_routes.py  # SSH 凭据 CRUD + 绑定
│   ├── chat_routes.py         # LLM 对话 API（含 SSE 流式）
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
│   ├── agents/                # AI Agent（chat、技能运行/演化）
│   ├── config/                # 配置管理（.env 加载）
│   ├── core/                  # LLM 客户端 + 工具调用
│   ├── data/                  # 数据层 (SQLAlchemy)
│   ├── i18n/                  # 国际化
│   ├── skills/                # 技能库 + 加载器 + 定时调度
│   └── tools/                 # 工具注册表（SSH/K8s/LDAP/DB/Prometheus）
├── alembic/                    # 数据库迁移
├── data/                       # 运行时数据（SQLite DB）
├── Dockerfile                  # 多阶段构建（Node + Python）
├── docker-compose.yml          # 容器编排
├── .env.example                # 配置模板
├── start.sh                    # 启动脚本
├── stop.sh                     # 停止脚本
└── restart.sh                  # 重启脚本
```

**技术栈**：
- 前端：Vue 3 + Vite + Vue Router + Pinia + Axios + marked
- 后端：FastAPI + Uvicorn + JWT (python-jose)
- 数据：SQLAlchemy + SQLite + Alembic
- SSH：paramiko ｜ LDAP：ldap3 ｜ 网络：httpx / requests

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
- **SSH 凭据管理**：创建密码/密钥凭据，绑定到服务器
- **外部同步**：配置 API 地址、认证方式、字段映射，一键拉取主机

### 3. 💬 AI 对话
- 自然语言提问，AI 自动调用工具
- 多轮对话，会话持久化（可新建/切换/删除会话）
- 支持 SSE 流式输出
- 关键词自动触发技能（如"nfc 异常"触发监控诊断）

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
| `check_disk_usage` | 磁盘使用率检查（只允许 df 命令） |
| `check_resources` | 系统资源（CPU、内存、进程） |
| `list_services` | systemd 服务列表与状态 |
| `check_resources_on_server` | 指定服务器资源检查 |
| `list_services_on_server` | 指定服务器服务列表 |

### Kubernetes 工具
| 工具 | 功能 |
|------|------|
| `check_k8s_nodes` | 节点状态检查 |
| `check_k8s_pods` | Pod 状态与事件 |
| `check_k8s_events` | 集群事件分析（Warning 优先） |
| `check_k8s_deployments` | Deployment 检查 |
| `check_k8s_services` | Service 检查 |
| `list_k8s_contexts` | Kubeconfig 上下文 |

### 数据库工具
| 工具 | 功能 |
|------|------|
| `list_servers` | 查询已注册服务器（密码脱敏） |
| `query_runs` | 查询历史运行记录（按状态/触发源/时间筛选） |

### Prometheus 监控工具
| 工具 | 功能 |
|------|------|
| `prometheus_instant_query` | 即时查询，返回当前时间点指标值 |
| `prometheus_range_query` | 范围查询，返回一段时间内的指标序列 |

> 认证方式：Bearer Token（`.env` 中 `PROMETHEUS_URL` / `PROMETHEUS_TOKEN`）

### LDAP 工具
| 工具 | 功能 |
|------|------|
| `ldap_search_user` | 用户信息查询（sAMAccountName → mail 自动匹配） |

---

## 🧠 技能系统

技能是 Markdown 文件（含 YAML frontmatter），存放在 `hermes/skills/library/`。LLM 在对话中检测到关键词时自动加载对应技能，按技能步骤分析。

| 技能 | 描述 | 触发方式 |
|------|------|----------|
| **detect_oom_killed** | 查找被 OOMKiller 杀掉的 Pod，定位内存配置问题并推荐修复 | 定时（每日） |
| **diagnose_prometheus_anomaly** | 通过 ARMS Prometheus 指标定位业务异常（服务请求/数据库/JVM/系统 4 大维度） | 用户触发（含"nfc"/"异常"等关键词） |

**技能文件结构**（示例 `detect_oom_killed.md`）：
```markdown
---
name: detect_oom_killed
description: Find pods killed by OOMKiller
trigger: scheduled_daily   # scheduled_daily | user_initiated
severity: warning
---
# 正文 — 给 LLM 的分析指令
```

- **加载器**：`hermes/skills/loader.py` 负责解析/加载/保存技能
- **调度器**：`hermes/skills/scheduler.py` 定时运行 `scheduled_daily` 技能
- **演化**：`hermes/agents/skill_evolver.py` 根据 `SkillOutcome` 反馈优化技能
- **版本管理**：每次保存记录 `SkillVersion`（内容 + diff + 原因）

---

## 🗄️ 数据模型

| 表 | 说明 |
|----|------|
| `servers` | 服务器（标签、备注、绑定 SSH 凭据） |
| `ssh_credentials` | 可复用 SSH 凭据（密码/密钥，默认标记） |
| `run_history` | 工具运行审计（命令、状态、耗时、输出） |
| `llm_conversations` | AI 对话会话（消息 JSON 持久化） |
| `skill_outcomes` | 技能执行结果 + 用户反馈闭环 |
| `skill_versions` | 技能版本历史（内容 + diff） |
| `users` | 系统用户（bcrypt 哈希） |
| `user_sessions` | 登录会话（JWT 吊销支持） |
| `sync_config` | 外部主机同步配置（单行表） |

数据库迁移使用 Alembic（`alembic/versions/`）。

---

## 🔐 安全设计

| 层面 | 措施 |
|------|------|
| **认证** | JWT + HttpOnly Cookie，SameSite 防护 |
| **密码** | bcrypt 哈希（自动迁移旧 SHA-256） |
| **工具安全** | 所有工具严格只读（SSH 命令白名单、K8s 仅 kubectl get） |
| **凭据保护** | SSH 密码/密钥脱敏，绑定管理不暴露给 LLM |
| **操作审计** | 所有操作 100% 记录（含 LLM 触发的工具调用） |
| **AI 身份** | System Prompt 注入，防止身份漂移 |
| **配置分离** | 全部通过 `.env` 管理，无硬编码敏感信息 |

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

### 添加新技能

```markdown
# hermes/skills/library/my_skill.md
---
name: my_skill
description: 技能描述
trigger: user_initiated
severity: info
---
# 给 LLM 的分析指令（含 PromQL 模板等）
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

## ⚙️ 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `TOKENHUB_API_KEY` | ✅ | LLM API Key |
| `TOKENHUB_MODEL` | ✅ | LLM 模型名 |
| `TOKENHUB_BASE_URL` | ✅ | LLM API 地址 |
| `OPS_DB_PATH` | ✅ | SQLite 数据库路径 |
| `ADMIN_INITIAL_PASSWORD` | ✅ | 初始管理员密码 |
| `JWT_SECRET` | 推荐 | JWT 签名密钥（稳定则会话不失效） |
| `CORS_ORIGINS` | 可选 | 允许的跨域来源（逗号分隔） |
| `PROMETHEUS_URL` | 可选 | ARMS Prometheus 查询地址 |
| `PROMETHEUS_TOKEN` | 可选 | Prometheus Bearer Token |
| `LDAP_SERVER` 等 | 可选 | LDAP 连接配置 |

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
