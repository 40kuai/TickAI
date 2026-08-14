"""Prometheus 查询工具 — 通过 ARMS Prometheus HTTP API 查询监控指标。

两个 LLM 可调用工具：

  1. prometheus_instant_query — 即时查询，返回当前时间点的指标值
  2. prometheus_range_query — 范围查询，返回一段时间内的指标序列

工具只做原始 PromQL 查询，不涉及阈值判断（阈值判断在技能层完成）。
认证方式：Bearer Token（从 .env 中 PROMETHEUS_URL / PROMETHEUS_TOKEN 读取）。
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from hermes.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


# ============================================================
# 配置读取
# ============================================================

def _get_config() -> tuple[str, str]:
    """从 .env 读取 Prometheus 连接配置。"""
    from hermes.config.settings import get as _get
    url = _get("PROMETHEUS_URL", "").strip()
    token = _get("PROMETHEUS_TOKEN", "").strip()
    if not url:
        raise RuntimeError("PROMETHEUS_URL 未配置，请在 .env 中设置")
    if not token:
        raise RuntimeError("PROMETHEUS_TOKEN 未配置，请在 .env 中设置")
    return url, token


# ============================================================
# HTTP 调用
# ============================================================

def _call_prometheus(endpoint: str, params: dict) -> dict:
    """调用 Prometheus HTTP API，返回解析后的 JSON 字典。

    Args:
        endpoint: API 路径，如 "api/v1/query"
        params: 查询参数，如 {"query": "up", "time": "..."}

    Returns:
        Prometheus API 返回的 JSON 响应体

    Raises:
        RuntimeError: 网络错误、非 200 状态码、JSON 解析失败
    """
    url, token = _get_config()
    query_string = urlencode(
        [(k, v) for k, v in params.items() if v is not None],
        doseq=True,
    )
    full_url = f"{url.rstrip('/')}/{endpoint}?{query_string}"

    req = Request(
        full_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except URLError as e:
        raise RuntimeError(f"Prometheus API 请求失败: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Prometheus 返回非 JSON 响应: {e}") from e


# ============================================================
# 结果标准化
# ============================================================

def _simplify_instant_result(result: dict) -> list[dict]:
    """将 Prometheus 即时查询结果简化成 LLM 易读的格式。"""
    data = result.get("data", {})
    items = data.get("result", [])
    out = []
    for item in items:
        metric = item.get("metric", {})
        value = item.get("value", [])
        out.append({
            "metric": metric,
            "value": float(value[1]) if len(value) > 1 else None,
        })
    return out


def _simplify_range_result(result: dict) -> list[dict]:
    """将 Prometheus 范围查询结果简化成 LLM 易读的格式。"""
    data = result.get("data", {})
    items = data.get("result", [])
    out = []
    for item in items:
        metric = item.get("metric", {})
        values = item.get("values", [])
        out.append({
            "metric": metric,
            "values": [[float(v[1]) for v in values]],  # 展平为数值列表
            "sample_count": len(values),
        })
    return out


# ============================================================
# 工具 1: prometheus_instant_query
# ============================================================

_INSTANT_QUERY_SCHEMA = {
    "name": "prometheus_instant_query",
    "description": "执行 Prometheus 即时查询，返回当前时间点的指标值。"
                   "用于检查指标当前是否超过阈值、查看最新值。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "PromQL 查询语句。例如："
                               "'avg(arms_db_requests_seconds_ign_rpc{service=~\"nfc-.*\"}) by(service)'。"
                               "注意：PromQL 中的引号需要用反斜杠转义。",
            },
            "time": {
                "type": "string",
                "description": "查询时间点（Unix 时间戳）。不传则查当前时间。",
            },
        },
        "required": ["query"],
    },
}


def _instant_query_handler(args: dict, **kwargs: Any) -> str:
    query = args.get("query", "")
    if not query:
        return tool_error("query 参数是必填的")

    params: dict[str, str] = {"query": query}
    if args.get("time"):
        params["time"] = str(args["time"])

    try:
        result = _call_prometheus("api/v1/query", params)
    except RuntimeError as e:
        return tool_error(str(e))

    if result.get("status") != "success":
        err_msg = result.get("error", "unknown error")
        return tool_error(f"Prometheus 查询失败: {err_msg}")

    simplified = _simplify_instant_result(result)

    # 如果结果超过 20 条，截断以避免 LLM 上下文过大
    display = simplified[:20]
    truncated = len(simplified) > 20

    return tool_result(
        result_type="instant",
        result_count=len(simplified),
        results=display,
        truncated=truncated,
        raw_query=query,
    )


# ============================================================
# 工具 2: prometheus_range_query
# ============================================================

_RANGE_QUERY_SCHEMA = {
    "name": "prometheus_range_query",
    "description": "执行 Prometheus 范围查询，返回一段时间内的指标序列。"
                   "用于观察指标趋势、查看历史波动、计算变化率。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "PromQL 查询语句。例如："
                               "'avg(irate(arms_jvm_gc_total{service=~\"nfc-.*\",gen=\"young\"}[1m])) by(service)'。",
            },
            "start": {
                "type": "string",
                "description": "开始时间（Unix 时间戳）。不传则默认 1 小时前。",
            },
            "end": {
                "type": "string",
                "description": "结束时间（Unix 时间戳）。不传则默认当前时间。",
            },
            "step": {
                "type": "string",
                "description": "查询步长（秒）。不传则自动根据时间范围选择。",
            },
        },
        "required": ["query"],
    },
}


def _range_query_handler(args: dict, **kwargs: Any) -> str:
    import time as _time

    query = args.get("query", "")
    if not query:
        return tool_error("query 参数是必填的")

    now = int(_time.time())
    params: dict[str, str] = {
        "query": query,
        "start": args.get("start", str(now - 3600)),  # 默认 1 小时前
        "end": args.get("end", str(now)),
    }
    if args.get("step"):
        params["step"] = str(args["step"])

    try:
        result = _call_prometheus("api/v1/query_range", params)
    except RuntimeError as e:
        return tool_error(str(e))

    if result.get("status") != "success":
        err_msg = result.get("error", "unknown error")
        return tool_error(f"Prometheus 查询失败: {err_msg}")

    simplified = _simplify_range_result(result)

    display = simplified[:20]
    truncated = len(simplified) > 20

    return tool_result(
        result_type="range",
        result_count=len(simplified),
        results=display,
        truncated=truncated,
        raw_query=query,
        time_range={"start": params["start"], "end": params["end"]},
    )


# ============================================================
# 注册
# ============================================================

registry.register(
    name="prometheus_instant_query",
    schema=_INSTANT_QUERY_SCHEMA,
    handler=_instant_query_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="📊",
)

registry.register(
    name="prometheus_range_query",
    schema=_RANGE_QUERY_SCHEMA,
    handler=_range_query_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="📈",
)