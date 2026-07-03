# 🎫 TickAI

> AI-Powered Ops Ticket Platform — AI 驱动的智能运维工单平台

让 AI 接管繁琐运维，你专注于真正重要的事。

---

## ✨ 特性

| 模块 | 功能 |
|------|------|
| 🖥️ **服务器管理** | SSH 连接、磁盘检查、资源监控、服务状态 |
| ☸️ **Kubernetes 集成** | 节点、Pod、Deployment、Service 检查，事件分析 |
| 🔍 **LDAP 查询** | 用户信息查询（支持 username/email/uid） |
| 💬 **AI 对话引擎** | 自然语言提问，自动调用工具回答 |
| 🎫 **工单驱动** | 所有操作以工单形式管理，全程可追溯 |
| 📚 **技能系统** | 可扩展的 LLM 技能库，自动优化 |
| 📊 **审计日志** | 完整的操作记录和运行统计 |
| 🌐 **多语言支持** | 中文/英文切换 |

---

## 🏗️ 架构

```
TickAI/
├── hermes/                  # 核心业务逻辑（保留 Hermes 作为代码命名空间）
│   ├── agents/             # AI Agent
│   │   ├── chat.py        # 对话引擎（带 System Prompt 身份锁定）
│   │   ├── skill_runner.py # 技能执行器
│   │   └── skill_evolver.py # 技能自演化
│   ├── config/             # 配置管理
│   ├── core/               # 核心模块
│   │   └── llm.py         # TokenHub LLM 客户端
│   ├── data/               # 数据层
│   ├── i18n/               # 国际化
│   ├── skills/             # 技能库
│   └── tools/              # 工具注册表（SSH/K8s/LDAP）
├── ui/                     # Streamlit Web UI
│   ├── app.py             # 首页（平台概览）
│   ├── common.py          # 通用组件
│   └── pages/             # 5 个功能页面
│       ├── 1_Servers.py   # 服务器管理
│       ├── 2_History.py   # 审计日志
│       ├── 3_Ask_LLM.py   # AI 对话 / 工单提交
│       ├── 4_Tools.py     # 工具浏览
│       └── 5_Skills.py    # 技能管理
├── data/                   # 运行时数据（SQLite DB）
├── .env.example           # 配置模板
├── .env                   # 实际配置（不提交 Git）
├── start.sh               # 启动脚本
├── stop.sh                # 停止脚本
├── restart.sh             # 重启脚本
└── README.md              # 本文档
```

---

## 🚀 快速开始

### 1. 环境配置

```bash
# 克隆项目
cd ai

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 从模板创建
cp .env.example .env

# 编辑配置
vim .env
```

**关键配置项**：

| 配置项 | 说明 | 必填 |
|--------|------|------|
| `TOKENHUB_API_KEY` | TokenHub API Key | ✅ |
| `TOKENHUB_MODEL` | 默认 `deepseek-v4-flash` | ✅ |
| `OPS_DB_PATH` | 数据库路径，默认 `data/opsticket.db` | ✅ |
| `LDAP_*` | LDAP 相关配置 | ⭕ 可选 |

### 3. 启动服务

```bash
# 方式一：使用脚本（推荐）
./start.sh

# 方式二：手动启动
.venv/bin/python -m streamlit run ui/app.py --server.port 8502
```

访问 **http://localhost:8502** 开始使用！

---

## 📄 页面功能

### 1. 🏠 首页（Dashboard）
- 平台介绍与核心价值展示
- 快速统计：服务器数、运行次数、成功率
- 最近运行记录
- 快速开始指南

### 2. 🖥️ Servers（服务器管理）
- 添加/删除服务器主机
- 快捷按钮：磁盘检查、资源监控、服务状态
- SSH 连接测试

### 3. 📜 History（审计日志）
- 所有工具运行记录
- 按服务器、状态、触发者筛选
- 查看完整的运行参数和返回结果

### 4. 💬 Ask LLM（AI 工单）
- 自然语言提问运维问题
- AI 自动调用工具分析
- 支持上下文多轮对话
- 对话历史可追溯

### 5. 🛠️ Tools（工具浏览）
- 查看所有已注册工具
- 查看 Tool Schema
- 在线测试执行

### 6. 📚 Skills（技能系统）
- 查看预制运维分析技能
- 运行技能执行复杂分析
- 技能自动优化建议

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
| `ldap_search_user` | 用户信息查询（username/email/uid） |

---

## 📚 技能库

| 技能 | 功能 |
|------|------|
| `detect_oom_killed` | OOM Killed 检测与根因分析 |
| *更多技能持续添加中...* | |

---

## 🔐 安全设计

| 层面 | 措施 |
|------|------|
| **工具安全** | 所有工具严格只读，不修改目标系统 |
| **密码存储** | SSH 密码加密存储（AES） |
| **权限控制** | LDAP 查询遵循最小权限原则 |
| **操作审计** | 所有操作 100% 记录，可追溯 |
| **AI 身份锁定** | System Prompt 注入身份，防止 Claude 等模型冒充 |

---

## 🤖 AI 身份保护

TickAI 内置身份保护机制，防止 LLM 在对话中随机声称自己是别的模型：

```python
# System Prompt（每次对话自动注入）
"You are TickAI, an intelligent operations ticket platform.
Always respond in the user's language.
Never claim to be Claude, ChatGPT, or any other AI model - you are TickAI."
```

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

# 3. 在 hermes/agents/chat.py 导入模块
from hermes.tools.mytool import *  # noqa
```

### 添加新技能

```markdown
<!-- hermes/skills/library/my_skill.md -->
# 技能名称

## 场景描述
适用场景说明...

## 检查步骤
1. ...
2. ...

## 输出格式
JSON 格式...
```

---

## 📊 默认账号

| 用户名 | 密码 |
|--------|------|
| `admin` | `admin123` |

首次登录后请及时修改密码。

---

## 📄 脚本说明

| 脚本 | 功能 |
|------|------|
| `start.sh` | 后台启动服务，端口 8502 |
| `stop.sh` | 停止后台服务 |
| `restart.sh` | 重启服务 |

---

## License

MIT © TickAI Team
