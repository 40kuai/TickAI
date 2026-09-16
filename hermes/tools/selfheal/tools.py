"""run_selfheal 工具 — 让对话型 LLM(Web/飞书)能触发受控自愈闭环(KR2 Task 8)。

流程(确定性, 不依赖 LLM 自由发挥): 探测 → 影响面采集 → 统一审批出口(规则硬约束 + AI
软判定) → auto 直执 / approval 挂审批单 / reject 拒绝 → 验证恢复。写命令只来自模板
白名单(hermes.selfheal.actions.render_command)。

与直接调 orchestrator 的区别: 本工具带参数白名单校验(server_id 为 int / scene 枚举),
并以 triggered_by="dialog" 落审计, 便于区分人工/对话触发来源。
"""
from __future__ import annotations

from typing import Any, Dict

from hermes.selfheal import orchestrator
from hermes.tools.registry import registry, tool_error, tool_result

# 合法场景白名单 — 从 orchestrator.SCENE_ACTION 派生, 避免与编排器分叉
_VALID_SCENES = tuple(orchestrator.SCENE_ACTION)


def _selfheal_schema() -> Dict[str, Any]:
    return {
        "name": "run_selfheal",
        "description": (
            "触发一次受控自愈闭环: 探测 → 影响面采集 → 统一审批出口(AI按操作影响风险判定, "
            "规则硬约束兜底) → auto 直执/approval 挂审批单/reject 拒绝 → 验证恢复。"
            "写命令只来自模板白名单。"
            "场景: process_restart(重启服务)、disk_clean(磁盘清理)、cache_clean(缓存清理)、"
            "log_cleanup_script(日志清理, 写操作, 按风险审批, 缺省跑全部4类)。"
            "高危操作(docker-prune等)不会直接执行, 而是生成审批单等待人工批准。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server_id": {
                    "type": "integer",
                    "description": "目标服务器 ID(必填, 来自 list_servers)。",
                },
                "scene": {
                    "type": "string",
                    "enum": list(_VALID_SCENES),
                    "description": (
                        "自愈场景。process_restart: 重启指定服务; "
                        "disk_clean: 清理磁盘(需 mount+path); "
                        "cache_clean: 清理缓存(需 mode); "
                        "log_cleanup_script: 固定脚本清理日志(需 mount, category 可选缺省全部)。"
                    ),
                },
                "target": {
                    "type": "object",
                    "description": (
                        "场景目标参数。process_restart 需 service(服务名); "
                        "disk_clean 需 mount(挂载点)+path(日志文件路径, 必须在白名单内); "
                        "cache_clean 需 mode(清理模式); "
                        "log_cleanup_script 需 mount(挂载点), category 可选"
                        "(system/service/docker-log/docker-prune, 缺省全部4类)。"
                    ),
                },
            },
            "required": ["server_id", "scene", "target"],
        },
    }


def run_selfheal_handler(args: Dict[str, Any], **kwargs: Any) -> str:
    """执行一次受控自愈闭环并返回结构化结果(剔除 rendered_command)。"""
    args = args or {}
    server_id = args.get("server_id")
    scene = args.get("scene")
    target = args.get("target")

    if isinstance(server_id, bool) or not isinstance(server_id, int) or server_id <= 0:
        return tool_error(f"server_id 必须是正整数, 收到: {server_id!r}")
    if scene not in _VALID_SCENES:
        return tool_error(f"非法 scene: {scene!r}, 合法: {list(_VALID_SCENES)}")
    if "target" not in args:
        return tool_error("缺少 target 参数(场景目标)")
    if not isinstance(target, dict):
        return tool_error(f"target 必须是对象, 收到: {type(target).__name__}")

    try:
        result = orchestrator.run_selfheal(
            server_id=server_id,
            scene=scene,
            target=target,
            triggered_by="dialog",
        )
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"自愈闭环执行失败: {exc}")

    if not isinstance(result, dict):
        return tool_error(f"自愈闭环返回异常: {result!r}")
    # rendered_command 是内部命令渲染结果, 不回传给 LLM(避免暴露写命令细节)
    result.pop("rendered_command", None)
    return tool_result(**result)


registry.register(
    name="run_selfheal",
    toolset="system",
    schema=_selfheal_schema(),
    handler=run_selfheal_handler,
    check_fn=lambda: True,
    emoji="🩺",
    # 唯一受控写入口: 管理页标注写/高危, 对话读全开过滤时永不按只读暴露
    read_only=False,
    risk="high",
)
