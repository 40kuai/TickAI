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

import json
import logging
from typing import Any, Callable, Dict, Optional

from . import actions, config

logger = logging.getLogger(__name__)

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


def _judge_payload(action_name: str, target: Dict[str, Any],
                   context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action_name": action_name,
        "target": target,
        "scene": context.get("scene"),
        "triggered_by": context.get("triggered_by"),
        "impact": context.get("impact"),
        "metric": context.get("metric"),
        "server_name": context.get("server_name"),
    }


_JUDGE_SYSTEM_PROMPT = """你是运维安全审批评估器, 评估服务器上一个写操作是否需要人工审批。
安全原则:
- 只有影响极小、可快速重建、无业务影响的操作才能 auto。
- 不确定、影响面未知、不可逆且影响大 → approval。
- docker prune、批量删除等不可逆批量操作已由规则层强制审批, 你不必评估这类。
输入 JSON 含: action_name / target / impact(文件数、大小MB, 可为空) / metric(磁盘水位%, 仅供参考) / triggered_by。
输出严格 JSON: {"risk_score": 1-5, "recommendation": "auto"|"approval"|"reject", "reasons": ["理由1", "理由2"]}
规则: risk_score>=4 必须 approval; auto 仅限 risk_score<=2 且影响面明确且极小。"""


def _parse_judgement(content: str) -> Dict[str, Any]:
    """解析 LLM 返回的 JSON(兼容 ```json 围栏), 非法值抛 ValueError(fail-closed)。

    任何解析/校验失败向上抛, 由 decide() 捕获后默认审批, 绝不默认放行。
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    data = json.loads(text)
    rec = str(data.get("recommendation", "approval"))
    if rec not in ("auto", "approval", "reject"):
        raise ValueError(f"非法 recommendation: {rec!r}")
    try:
        score = int(data.get("risk_score", 5))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非法 risk_score: {data.get('risk_score')!r}") from exc
    if not 1 <= score <= 5:
        raise ValueError(f"risk_score 越界: {score}")
    reasons = data.get("reasons", [])
    if not isinstance(reasons, list):
        reasons = []
    return {"risk_score": score, "recommendation": rec,
            "reasons": [str(r) for r in reasons]}


def _default_judge(payload: Dict[str, Any]) -> Dict[str, Any]:
    """默认 AI 判定: TokenHub LLM 非流式调用 + 结构化解析。

    调用失败/解析失败会向上抛, 由 decide() 捕获后 fail-closed 默认审批。
    """
    from hermes.config import settings as config
    from hermes.core import llm_client

    api_key = config.LLM_API_KEY()
    if not api_key:
        raise RuntimeError("TOKENHUB_API_KEY 未配置, AI 风险评估不可用")
    client = llm_client.TokenHubClient(
        api_key=api_key,
        model=config.LLM_MODEL(),
        base_url=config.LLM_BASE_URL(),
        timeout=30.0,
        max_retries=1,
    )
    try:
        resp = client.chat([
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ])
        content = resp["choices"][0]["message"]["content"]
        return _parse_judgement(content)
    finally:
        client.close()


def _coerce_score(value: Any) -> int:
    """AI 输出的 risk_score 保守化: 非法/非整数/越界一律按 5(fail-closed 方向)。

    防畸形输出(如 'high'、2.9)被 int() 截断后误放行 auto——
    例如 int(2.9)=2 会通过 score<=2 门槛, 必须整体拒绝。
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 5
    if not f.is_integer() or not 1 <= f <= 5:
        return 5
    return int(f)


def _merge(judgement: Optional[Dict[str, Any]], impact: Any, reasons: list) -> str:
    """安全合并: 在规则放行区间内合并 AI 建议。auto 需满足三重条件。

    非法输入(非 dict/score 畸形)在此不抛——score 由 _coerce_score 保守化,
    其余按缺省 approval 处理, 保证 fail-closed 不变量不被畸形输出击穿。
    """
    judgement = judgement or {}
    rec = str(judgement.get("recommendation", "approval"))
    score = _coerce_score(judgement.get("risk_score", 5))
    if rec == "reject":
        ai_reasons = judgement.get("reasons") or []
        if not isinstance(ai_reasons, list):
            ai_reasons = [str(ai_reasons)]
        reasons.append(f"AI 评估拒绝(risk={score}): {'; '.join(ai_reasons)}")
        return "reject"
    if rec == "auto" and score <= 2 and bool(impact):
        reasons.append(f"AI 评估自主执行(risk={score})且影响面已确认")
        return "auto"
    reasons.append(f"AI 评估建议 {rec}(risk={score})")
    return "approval"


def decide(
    server_id: int,  # 统一出口签名预留: 供审计/流水追踪(当前未消费, 见 _judge_payload)
    action_name: str,
    target: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    judge_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """统一审批出口。返回 {decision, severity, reasons, ai_judgement}。

    decision: auto(自主执行) | approval(生成审批单) | reject(拒绝执行)。
    severity: auto→low, approval/reject→high(兼容前端 low/high badge)。
    judge_fn: 可注入的 AI 判定函数(测试用); 缺省 _default_judge。
    """
    context = context or {}
    hard = hard_rule(action_name, target)
    if hard["decision"] != "allow":
        return {"decision": hard["decision"], "severity": "high",
                "reasons": hard["reasons"], "ai_judgement": None}

    reasons = list(hard["reasons"])
    impact = context.get("impact")
    judge = judge_fn if judge_fn is not None else _default_judge
    try:
        judgement = judge(_judge_payload(action_name, target, context))
        # 合并也纳入 try: 畸形 judge 输出(非 dict/score 非法)一律 fail-closed,
        # 不变量: 任何异常都不允许逃逸 decide 破坏"默认审批"语义。
        decision = _merge(judgement, impact, reasons)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI 风险评估不可用(%s), fail-closed 默认审批: %s",
                       type(exc).__name__, exc)
        reasons.append("AI 风险评估不可用, 按人工审批处理(fail-closed)")
        return {"decision": "approval", "severity": "high",
                "reasons": reasons, "ai_judgement": None}

    severity = "low" if decision == "auto" else "high"
    return {"decision": decision, "severity": severity,
            "reasons": reasons, "ai_judgement": judgement}
