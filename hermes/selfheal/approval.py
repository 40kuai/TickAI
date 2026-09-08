"""统一审批出口：所有写操作在此决策 allow / approval / reject。

auto 为上层(decide/AI 合并)后的最终决策, 本模块仅规则层。
双轨制(安全侧合并, 取更严格):
- 规则硬约束: 基于操作影响风险静态表 ACTION_RISK + 白名单渲染校验, 不可协商。
  基础风险 high(不可逆批量/未知动作) → 强制审批; 白名单外 → 拒绝。
- AI 软判定: 仅在规则放行区间(allow)内生效, 结合影响面评估 risk_score/recommendation。
- 合并: 规则 approval 时 AI 无权放行; 规则 allow 时 AI 的 reject 生效;
  auto 仅当 AI 建议 auto 且 risk_score<=2 且影响面已确认。
- fail-closed: LLM 失败/影响面缺失 → 默认审批, 绝不默认放行。

触发条件(磁盘水位等必要性)与审批条件(操作影响风险)彻底解耦——
磁盘多满决定"要不要清", 操作影响风险决定"要不要人批"。
"""
from __future__ import annotations

from typing import Any, Dict

from . import actions, config

# 操作影响风险静态表(审批判定依据, "做什么"由表定; "动多大"由 AI 估)。
# base: low|medium|high —— high 为规则硬约束, 强制人工审批, AI 无权放行。
# reversible: 数据是否可恢复; rebuild_cost: 重建/恢复成本。
ACTION_RISK: Dict[str, Dict[str, str]] = {
    "restart_service":     {"base": "low",    "reversible": "true",  "rebuild_cost": "low"},
    "clean_cache":         {"base": "low",    "reversible": "true",  "rebuild_cost": "low"},
    "truncate_log":        {"base": "low",    "reversible": "false", "rebuild_cost": "low"},
    "journal_vacuum":      {"base": "low",    "reversible": "false", "rebuild_cost": "low"},
    "docker_log_truncate": {"base": "medium", "reversible": "false", "rebuild_cost": "low"},
    "run_cleanup_script":  {"base": "medium", "reversible": "false", "rebuild_cost": "medium"},
}

# 脚本类动作按 category 细化: docker-prune 不可逆批量 → high
_HIGH_RISK_CATEGORIES = ("docker-prune",)


def _cleanup_risk_with_note(category: str) -> tuple[str, bool]:
    """脚本类 category → (风险级, 是否 docker-prune 不可逆清理)。

    唯一判定源: category 为 docker-prune, 或 category=all 且白名单含 docker-prune
    → (high, True); 其余 (medium, False)。供风险分级与 reason 标注共用。
    """
    cat = (category or "").strip() or "all"
    is_prune_note = cat in _HIGH_RISK_CATEGORIES or (
        cat == "all" and "docker-prune" in config.LOG_CLEANUP_CATEGORIES
    )
    return ("high" if is_prune_note else "medium"), is_prune_note


def _base_risk(action_name: str, target: Dict[str, Any]) -> str:
    if action_name == "run_cleanup_script":
        return _cleanup_risk_with_note(str((target or {}).get("category", "")))[0]
    risk = ACTION_RISK.get(action_name)
    if risk is None:
        return "high"  # 未知动作保守 high(随后 render 校验会 reject)
    return risk["base"]


def hard_rule(action_name: str, target: Dict[str, Any]) -> Dict[str, Any]:
    """规则硬约束: 返回 {decision: approval|reject|allow, reasons[]}。
    - 未知动作/白名单渲染失败 → reject
    - 基础风险 high → approval(强制审批, AI 无权放行)
    - 其余 → allow(进入 AI 软判定区间)
    """
    try:
        actions.render_command(action_name, target)
    except ValueError as exc:
        return {"decision": "reject", "reasons": [f"白名单校验拒绝: {exc}"]}
    risk = _base_risk(action_name, target)
    if risk == "high":
        extra = ACTION_RISK.get(action_name, {})
        reason = (f"操作影响风险=high(不可逆/批量), 强制人工审批"
                  f"[reversible={extra.get('reversible', '?')}, "
                  f"rebuild_cost={extra.get('rebuild_cost', '?')}]")
        # 脚本类: 判定源与风险分级一致(_cleanup_risk_with_note) → 标注不可逆清理
        if action_name == "run_cleanup_script":
            _, is_prune_note = _cleanup_risk_with_note(
                str(target.get("category", ""))
            )
            if is_prune_note:
                reason += "; 包含 docker-prune 不可逆清理"
        return {"decision": "approval", "reasons": [reason]}
    return {"decision": "allow",
            "reasons": [f"操作影响风险={risk}, 进入 AI 评估区间"]}
