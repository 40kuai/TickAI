"""run_skill 工具 — 让对话型 LLM(Web/飞书)能触发 skill 分析流程。

skill 是 hermes/skills/library/ 下的 .md 文件(如 detect_oom_killed.md),
由 SkillRunner 独立执行(K8s 只读工具 + LLM 分析),结果写入 SkillOutcome 表。

注册为对话工具后,用户在对话里说"跑一下 xxx skill",LLM 就能调用本工具,
触发 skill 执行并把分析结论返回给用户。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from hermes.tools.registry import registry, tool_error, tool_result
from hermes.skills.loader import list_skills
from hermes.data import db
from hermes.data.models import SkillOutcome


def _available_skill_names() -> List[str]:
    """返回 skill 库中所有 skill 名(供 schema 枚举与校验)。"""
    return [s["name"] for s in list_skills()]


def _skill_schema() -> Dict[str, Any]:
    names = _available_skill_names()
    enum = names if names else ["detect_oom_killed"]
    # 为 LLM 提供「skill 名 → 用途」映射,帮助其准确选择与用户意图匹配的 skill。
    # 描述来自 skill 文件的 frontmatter description。
    skill_map = {
        s["name"]: s["description"].replace("\n", " ").strip()
        for s in list_skills()
    }
    skill_descriptions = "；".join(
        f"{n}({skill_map.get(n, '')})" for n in names
    ) or "detect_oom_killed(OOMKilled 检测)"
    return {
        "name": "run_skill",
        "description": (
            "运行一个运维分析 skill(读集群只读信息并产出结论)。"
            "根据用户意图选择最匹配的 skill。可用 skill 及其用途: "
            + skill_descriptions
            + "。用户查询 nfc 服务状态/健康/监控/异常/巡检时,"
            "必须选择 diagnose_prometheus_anomaly。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "enum": enum,
                    "description": (
                        "要运行的 skill 名称。各 skill 用途: "
                        + skill_descriptions
                        + "。nfc 服务健康/监控巡检 → diagnose_prometheus_anomaly;"
                        " 被 OOMKilled 杀掉的 Pod 检测 → detect_oom_killed。"
                    ),
                },
                "cluster_context": {
                    "type": "string",
                    "description": "集群上下文(可选)。不传则用默认上下文。",
                },
            },
            "required": ["skill_name"],
        },
    }


def _fetch_findings(outcome_id: int) -> str:
    """从 SkillOutcome 表读回 skill 分析结论文本。"""
    with db.session_scope() as s:
        row = s.get(SkillOutcome, outcome_id)
        if row is None:
            return f"(skill 已执行, outcome_id={outcome_id}, 但无法读取结果)"
        return row.findings_summary or json.dumps(
            row.findings_json or [], ensure_ascii=False
        )


def run_skill_handler(args: Dict[str, Any], **kwargs: Any) -> str:
    """执行 skill 并把分析结论返回给 LLM。"""
    skill_name = (args or {}).get("skill_name")
    if not skill_name:
        return tool_error("缺少 skill_name 参数")
    if skill_name not in _available_skill_names():
        return tool_error(
            f"未知 skill: {skill_name!r}, 可用: {_available_skill_names()}"
        )

    cluster_context = (args or {}).get("cluster_context") or ""
    try:
        from hermes.agents.skill_runner import run_skill as _do_run_skill
        outcome_id = _do_run_skill(
            skill_name,
            cluster_context=cluster_context,
            triggered_by="dialog_tool",
        )
        findings = _fetch_findings(outcome_id)
        return tool_result(
            skill=skill_name,
            outcome_id=outcome_id,
            summary=findings,
        )
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"skill 执行失败: {exc}")


def run_skill_available() -> bool:
    """skill 库非空即可用。"""
    return bool(_available_skill_names())


registry.register(
    name="run_skill",
    toolset="default",
    schema=_skill_schema(),
    handler=run_skill_handler,
    check_fn=run_skill_available,
    emoji="🧠",
    max_result_size_chars=12000,
)
