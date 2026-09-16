"""Nightingale (N9E) 告警查询工具 — 查询历史/活跃告警(严格只读)。

可调用工具(每个工具覆盖一个查询场景):

  1. nightingale_history_alerts — 查询最近 N 分钟的历史告警(含已恢复)，
     支持按规则名/级别过滤。对应 n9e `alert-his-events/list`。
  2. nightingale_active_alerts — 查询当前活跃(未恢复)告警，
     支持按规则名/级别过滤。对应 n9e `alert-cur-events/list`。

只读保证:工具仅使用 Nightingale GET API(X-User-Token 认证)，绝不发起
任何 POST/PUT/DELETE 请求(不创建/修改/删除告警、屏蔽等)。

认证方式:X-User-Token 请求头，从 .env 读取 NIGHTINGALE_URL / NIGHTINGALE_TOKEN。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from hermes.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# 默认/最大返回条数
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50


# ============================================================
# 配置读取
# ============================================================

def _get_config() -> tuple[str, str]:
    """从 .env 读取 Nightingale 连接配置。"""
    from hermes.config.settings import get as _get
    url = _get("NIGHTINGALE_URL", "").strip()
    token = _get("NIGHTINGALE_TOKEN", "").strip()
    if not url:
        raise RuntimeError("NIGHTINGALE_URL 未配置，请在 .env 中设置")
    if not token:
        raise RuntimeError("NIGHTINGALE_TOKEN 未配置，请在 .env 中设置")
    return url, token


# ============================================================
# HTTP 调用(仅 GET,只读)
# ============================================================

def _call_nightingale(endpoint: str, params: Dict[str, Any]) -> dict:
    """调用 Nightingale GET API，返回解析后的 JSON 字典。

    Args:
        endpoint: API 路径，如 "alert-his-events/list"。
        params: 查询参数。

    Returns:
        Nightingale 返回的 JSON 响应体(含 dat/err/request_id)。

    Raises:
        RuntimeError: 网络错误、非 200 状态码、JSON 解析失败、业务 err 非空。
    """
    url, token = _get_config()
    query_string = urlencode(
        [(k, v) for k, v in params.items() if v is not None],
        doseq=True,
    )
    full_url = f"{url.rstrip('/')}/api/n9e/{endpoint.lstrip('/')}"
    if query_string:
        full_url = f"{full_url}?{query_string}"

    req = Request(
        full_url,
        headers={
            "X-User-Token": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Nightingale 认证失败(401)：请检查 NIGHTINGALE_TOKEN 是否有效") from e
        if e.code == 404:
            raise RuntimeError("Nightingale 返回 404：接口或资源不存在") from e
        raise RuntimeError(f"Nightingale API 请求失败: HTTP {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"Nightingale API 请求失败: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Nightingale 返回非 JSON 响应: {e}") from e

    err = data.get("err")
    if err:
        raise RuntimeError(f"Nightingale 返回错误: {err}")
    return data


# ============================================================
# 结果标准化
# ============================================================

_SEVERITY_NAMES = {
    1: "S1-CRITICAL",
    2: "S2-WARNING",
    3: "S3-INFO",
}


def _severity_name(severity: int) -> str:
    """n9e 级别 1/2/3 → S1-CRITICAL / S2-WARNING / S3-INFO。未知原样返回 S<值>。"""
    return _SEVERITY_NAMES.get(severity, f"S{severity}")


def _format_timestamp(ts: int) -> str:
    """秒时间戳 → 'YYYY-MM-DD HH:MM:SS'。0/空返回空串。"""
    if not ts:
        return ""
    try:
        import datetime
        return datetime.datetime.fromtimestamp(
            int(ts), tz=datetime.timezone.utc
        ).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, TypeError):
        return ""


def _find_event_service(raw: dict) -> Optional[str]:
    """从事件 tags/tags_map 中提取 service 标签值。"""
    tags_map = raw.get("tags_map") or {}
    service = tags_map.get("service")
    if service:
        return service
    for tag in raw.get("tags") or []:
        if tag.startswith("service="):
            return tag.split("=", 1)[1]
    return None


def _parse_event(raw: dict) -> dict:
    """把 n9e 告警事件标准化为工具输出结构。"""
    return {
        "id": raw.get("id"),
        "rule_name": raw.get("rule_name", ""),
        "rule_id": raw.get("rule_id"),
        "group_name": raw.get("group_name", ""),
        "severity": _severity_name(raw.get("severity")),
        "service": _find_event_service(raw),
        "target_ident": raw.get("target_ident") or "",
        "is_recovered": bool(raw.get("is_recovered")),
        "trigger_time": _format_timestamp(raw.get("trigger_time")),
        "first_trigger_time": _format_timestamp(raw.get("first_trigger_time")),
        "recover_time": _format_timestamp(raw.get("recover_time")),
        "last_eval_time": _format_timestamp(raw.get("last_eval_time")),
        "trigger_value": raw.get("trigger_value") or "",
        "prom_ql": raw.get("prom_ql") or "",
        "tags": raw.get("tags") or [],
    }


# ============================================================
# 工具 1: nightingale_history_alerts — 最近 N 分钟历史告警
# ============================================================

_HISTORY_ALERTS_SCHEMA = {
    "name": "nightingale_history_alerts",
    "description": (
        "查询 Nightingale 最近一段时间的历史告警(含已恢复的告警事件，用于回溯/审计)。"
        "默认查最近 10 分钟，可传 minutes 指定时间范围，也可按规则名/级别过滤。\n"
        "返回每条告警的规则名、级别(S1/S2/S3)、业务组、关联服务、触发/恢复时间、"
        "触发值、PromQL。严格只读，仅查询。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "minutes": {
                "type": "integer",
                "description": "查询最近 N 分钟内的告警。默认 10 分钟。",
                "default": 10,
            },
            "rule_name": {
                "type": "string",
                "description": "按规则名模糊过滤(如 '耗时')。可选。",
            },
            "severity": {
                "type": "integer",
                "description": "按告警级别过滤：1=S1 严重，2=S2 警告，3=S3 提示。可选。",
            },
            "limit": {
                "type": "integer",
                "description": "返回条数。默认 10，最大 50。",
                "default": 10,
            },
        },
        "required": [],
    },
}


def _history_alerts_handler(args: dict, **kwargs: Any) -> str:
    args = args or {}
    try:
        minutes = int(args.get("minutes", 10))
    except (ValueError, TypeError):
        minutes = 10
    minutes = max(1, min(minutes, 24 * 60))

    try:
        limit = int(args.get("limit", _DEFAULT_LIMIT))
    except (ValueError, TypeError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    now = int(time.time())
    stime = now - minutes * 60

    params = {
        "p": 1,
        "limit": limit,
        "stime": stime,
        "etime": now,
    }
    rule_name = (args.get("rule_name") or "").strip()
    if rule_name:
        params["rule_name"] = rule_name
    severity = args.get("severity")
    if severity is not None:
        try:
            params["severity"] = int(severity)
        except (ValueError, TypeError):
            pass

    try:
        data = _call_nightingale("alert-his-events/list", params)
    except RuntimeError as e:
        return tool_error(str(e))

    events = [_parse_event(e) for e in data.get("dat", {}).get("list", [])]
    total = data.get("dat", {}).get("total", len(events))
    return tool_result(
        result_type="alert_history",
        minutes=minutes,
        stime=stime,
        etime=now,
        count=len(events),
        total=total,
        events=events,
        hint=(
            "最近 %d 分钟内没有历史告警。可增大 minutes 扩大时间范围。" % minutes
            if not events else ""
        ),
    )


# ============================================================
# 工具 2: nightingale_active_alerts — 当前活跃(未恢复)告警
# ============================================================

_ACTIVE_ALERTS_SCHEMA = {
    "name": "nightingale_active_alerts",
    "description": (
        "查询 Nightingale 当前活跃(未恢复)告警，用于值班排障与快速定位现网问题。"
        "返回每条告警的规则名、级别(S1/S2/S3)、业务组、关联服务、首次触发时间、"
        "触发值、PromQL。严格只读，仅查询。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "rule_name": {
                "type": "string",
                "description": "按规则名模糊过滤(如 'CPU')。可选。",
            },
            "severity": {
                "type": "integer",
                "description": "按告警级别过滤：1=S1 严重，2=S2 警告，3=S3 提示。可选。",
            },
            "limit": {
                "type": "integer",
                "description": "返回条数。默认 10，最大 50。",
                "default": 10,
            },
        },
        "required": [],
    },
}


def _active_alerts_handler(args: dict, **kwargs: Any) -> str:
    args = args or {}
    try:
        limit = int(args.get("limit", _DEFAULT_LIMIT))
    except (ValueError, TypeError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    params = {
        "p": 1,
        "limit": limit,
        "my_groups": "false",  # false = 查全部业务组(n9e 按字符串解析)
    }
    rule_name = (args.get("rule_name") or "").strip()
    if rule_name:
        params["rule_name"] = rule_name
    severity = args.get("severity")
    if severity is not None:
        try:
            params["severity"] = int(severity)
        except (ValueError, TypeError):
            pass

    try:
        data = _call_nightingale("alert-cur-events/list", params)
    except RuntimeError as e:
        return tool_error(str(e))

    events = [_parse_event(e) for e in data.get("dat", {}).get("list", [])]
    total = data.get("dat", {}).get("total", len(events))
    return tool_result(
        result_type="active_alerts",
        count=len(events),
        total=total,
        events=events,
        hint=(
            "当前没有活跃告警。"
            if not events else
            "活跃告警为当前未恢复的事件；已恢复的事件请用 nightingale_history_alerts 查询。"
        ),
    )


# ============================================================
# 注册
# ============================================================

registry.register(
    name="nightingale_history_alerts",
    schema=_HISTORY_ALERTS_SCHEMA,
    handler=_history_alerts_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="🔔",
)

registry.register(
    name="nightingale_active_alerts",
    schema=_ACTIVE_ALERTS_SCHEMA,
    handler=_active_alerts_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="🚨",
)
