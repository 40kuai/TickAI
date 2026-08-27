# Feishu Bot — 飞书机器人远程运维接入

**Date:** 2026-08-20
**Status:** Draft (user approved approach, drafting spec)
**Scope:** 新增飞书机器人接入。复用现有 hermes 对话引擎与运维工具,不改动现有 Web 端功能。

## Goal

让用户通过与飞书机器人对话,远程调用 TickAI 的运维能力(磁盘检查、资源检查、服务列表等),无需打开 Web 界面。

## Non-Goal

- 不改动现有 Web 端(Vue)功能
- 不改动现有 `hermes.agents.chat` 对话引擎(只复用)
- 不引入 Celery/Redis 等重依赖
- 不做多租户/企业级 OAuth 授权(白名单制)

## 决策记录(用户确认)

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | 接入方式 | 企业自建应用(机器人+事件订阅) | 符合给定 App ID/Secret |
| 2 | 对话入口 | @机器人(单聊+群聊) | 覆盖面广 |
| 3 | 身份认证 | 飞书 OpenID 白名单 | 简单可控,适合内部运维 |
| 4 | 回调方式 | 飞书长连接(WebSocket) | 无需公网回调地址,部署省心 |
| 5 | 对话引擎 | 复用 `hermes.agents.chat.chat()` | 工具循环/历史/持久化全部复用 |
| 6 | 并发策略 | 线程模型异步队列(`queue.Queue`) | 长对话不阻塞其他用户。`chat()` 为阻塞式,故用**线程**而非 asyncio(原草案写 `asyncio.Queue` 已按实施修正为 `queue.Queue`) |

## Architecture (in plain words)

飞书消息经长连接进入,校验白名单后进入异步队列,后台任务复用现有 `chat()` 引擎执行 LLM 工具调用循环,完成后通过飞书消息 API 回复用户。

```
飞书客户端 (单聊/群聊 @机器人)
        │  消息
        ▼
飞书开放平台 ──WebSocket 长连接──► [feishu_ws] 长连接客户端
                                        │ 事件回调
                                        ▼
                              [feishu_handler] 消息处理
                                        │ 白名单校验
                                        ▼
                              [feishu_bot] 异步分发
                                        │ 入队 + 回"处理中"
                                        ▼
                              [feishu_tasks] 后台任务
                                        │ chat()
                                        ▼
                              hermes.agents.chat.chat() ← LLM 工具循环
                                        │ 结果
                                        ▼
                              飞书消息 API → 回复用户
```

## New Module Layout

```
hermes/feishu/                   # 飞书接入(新模块)
├── __init__.py
├── ws.py                        # 长连接客户端(启动/重连/事件回调注册)
├── handler.py                   # 事件处理:解析消息、白名单校验
├── bot.py                       # 消息分发:异步队列、会话映射、回复入口
└── tasks.py                     # 后台任务:调用 chat()、发送回复

api/main.py                      # 修改:启动时按配置拉起 feishu 长连接
hermes/config/settings.py        # 修改:新增 FEISHU_* 配置读取
.env.example                     # 修改:新增飞书配置模板
requirements.txt                 # 修改:新增 lark-oapi 依赖
```

## Component Design

### 1. 配置(`hermes/config/settings.py` 新增)

```python
# 飞书 — 运行时读取
def FEISHU_APP_ID() -> str:
    return get("FEISHU_APP_ID", "")

def FEISHU_APP_SECRET() -> str:
    return get("FEISHU_APP_SECRET", "")

def FEISHU_OPENID_WHITELIST() -> list:
    raw = get("FEISHU_OPENID_WHITELIST", "")
    return [x.strip() for x in raw.split(",") if x.strip()]

def FEISHU_ENABLED() -> bool:
    return bool(FEISHU_APP_ID() and FEISHU_APP_SECRET())
```

### 2. 长连接(`ws.py`)

- 使用官方 SDK `lark_oapi.ws.Client` 建立 WebSocket 长连接
- 注册 `im.message.receive_v1` 事件处理器
- 断线自动重连(SDK 内置)
- 启动失败/异常时记录日志,不阻塞主进程

### 3. 事件处理(`handler.py`)

- 解析 `im.message.receive_v1` 事件:
  - `sender.sender_id.open_id` → 发送者
  - `message.chat_id` / `message.chat_type` → 单聊/群聊
  - `message.message_type` → text(仅处理文本)
  - `message.content` → 文本内容
- 白名单校验:open_id 不在 `FEISHU_OPENID_WHITELIST` 直接忽略(不回复,不暴露)
- 群聊:仅当消息 @机器人 才处理(通过 `message.mentions` 判断)

### 4. 消息分发(`bot.py`)

- 线程安全 `queue.Queue` 任务队列(`chat()` 为阻塞式,故用线程模型)
- 收到合法消息:
  1. 立即回复「正在处理,请稍候…」(失败不阻断入队,有异常兜底)
  2. 消息入队
- 会话映射:内存 dict `open_id -> conversation_id`(线程安全 Lock),保持多轮上下文
- 群聊回复:`im.message.reply` 回复到原消息(不刷屏)

### 5. 后台任务(`tasks.py`)

- worker 在**独立 daemon 线程**中阻塞消费队列(chat() 为阻塞式,不适合线程池)
- 调用 `hermes.agents.chat.chat()`(复用工具循环与历史持久化)
- 通过 `im.message.create`(单聊)或 `im.message.reply`(群聊)发送 `reply`
- 发送失败重试 2 次(`_send_with_retry`),记录日志

### 6. 启动接入(`api/main.py`)

- FastAPI startup 事件中**惰性导入** `hermes.feishu.ws` 并调用 `start_feishu_bot()`,若 `FEISHU_ENABLED()` 则启动长连接后台任务(lark-oapi 未安装也不阻塞 app 启动)
- `start_feishu_bot()` 有幂等保护(重复调用不重复建连接)
- shutdown 事件调用 `stop_feishu_bot()`(SDK 无优雅关闭接口,daemon 线程随进程退出)

## Data Flow

1. 用户 @机器人 发消息
2. 长连接收到 `im.message.receive_v1`
3. `handler` 解析 open_id、chat_type、文本
4. 白名单校验通过
5. `bot` 立即回复「正在处理」,消息入队
6. `tasks` worker 调 `chat()` 执行工具循环
7. 完成后用消息 API 回复用户

## Error Handling

| 场景 | 处理 |
|---|---|
| 白名单外用户 | 忽略,不回复 |
| 非文本消息(图片/文件) | 忽略或提示仅支持文本 |
| 群聊未 @机器人 | 不处理 |
| LLM 未配置 | `chat()` 抛 RuntimeError,回复错误信息 |
| 工具调用失败 | `chat()` 已返回错误 JSON,直接转发 |
| 长连接断线 | SDK 自动重连,记录日志 |
| 发送消息失败 | 重试 2 次,记录日志 |

## Security Notes

- App Secret 只存 `.env`,绝不硬编码
- 白名单在配置层强制,防止未授权运维操作
- 消息内容与工具执行结果通过既有 `chat()` 链路持久化,可审计

## Testing

- `tests/feishu/test_handler.py`:
  - 文本消息解析(text/群聊@/非@)
  - 白名单放行/拒绝
  - 非文本消息处理
- `tests/feishu/test_bot.py`:
  - 队列入队/出队
  - `chat()` 被 mock 调用
  - 会话映射保持
  - 即时回复异常兜底(失败仍入队)、open_id 判空
- `tests/feishu/test_tasks.py`:
  - worker 处理一条消息并调用发送
  - 发送失败重试
- `tests/feishu/test_ws.py`:
  - 事件注册、未启用跳过、启用拉起线程
  - `_send_with_retry` 重试 2 次 / 成功立即返回
  - `start_feishu_bot` 幂等保护、stop 重置全局
- 配置缺失时 `FEISHU_ENABLED()` 为 False,不启动长连接(降级)

> 实施状态(2026-08-20):`tests/feishu/` 共 28 个测试全部通过。项目存在 65 个既有测试失败(Server 模型缺 username 字段等历史遗留),与本功能无关。

## Open Questions (待确认)

1. 是否需要记录飞书消息与 Web 端历史的关联?(当前设计:飞书对话复用 `chat()`,会自动写入 `Conversation` 表,可在 Web 历史页看到)
2. 群聊中是否需要限制只响应指定关键词?(当前:@机器人 即响应)
