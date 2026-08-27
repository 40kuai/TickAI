"""Prometheus 查询工具 — 通过 ARMS Prometheus HTTP API 查询监控指标。

可调用工具：

  1. prometheus_service_health — 唯一 Prometheus 入口，两种模式：
     - 服务健康快照（默认）：传 service（如 nfc-.*），一次调用并行查询
       服务请求/数据库/JVM/系统四类核心指标，返回结构化健康快照；
     - 任意指标查询：传 query（PromQL），执行即时查询（最近 10 分钟）。

所有 Prometheus 监控相关查询统一走此工具，避免多次调用消耗工具轮次。

工具只做原始 PromQL 查询，不涉及阈值判断（阈值判断在技能层完成）。
认证方式：Bearer Token（从 .env 中 PROMETHEUS_URL / PROMETHEUS_TOKEN 读取）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, List
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
# 工具: prometheus_service_health — 聚合查询（唯一 Prometheus 入口）
# ============================================================

# 各维度使用的 PromQL（service 过滤由 handler 注入）。用一次工具调用覆盖全部维度，
# 避免 LLM 反复调用导致轮次耗尽。
_DIMENSION_QUERIES: dict[str, List[str]] = {
    "request": [
        # 服务错误请求占比
        ("error_rate", 'sum(sum_over_time_lorc(arms_app_requests_error_count_ign_destid_endpoint_parent_ppid_prpc_rpc{{service=~"{svc}",callKind=~"http|rpc|consumer|custom_entry|server"}}[1m])) by(service)'),
        # 慢请求数
        ("slow_count", 'sum(arms_app_requests_slow_count_ign_destid_endpoint_parent_ppid_prpc_rpc{{service=~"{svc}"}}) by(service)'),
        # HTTP 200 占比
        ("http_200_ratio", 'sum(arms_requests_by_status_count_ign_rpc{{service=~"{svc}",status="200"}}) by(service) / sum(arms_requests_by_status_count_ign_rpc{{service=~"{svc}"}}) by(service)'),
    ],
    "database": [
        # 数据库请求耗时
        ("db_seconds", 'avg(arms_db_requests_seconds_ign_rpc{{service=~"{svc}"}}) by(service, callKind, endpoint, destId)'),
        # SQL 错误占比
        ("sql_error_rate", 'sum(arms_sql_requests_error_count_ign_rpc{{service=~"{svc}"}}) by(service) / sum(arms_sql_requests_count_ign_rpc{{service=~"{svc}"}}) by(service)'),
        # 慢 SQL 数量
        ("sql_slow_count", 'sum(arms_sql_requests_slow_count_ign_rpc{{service=~"{svc}"}}) by(service, endpoint, destId)'),
    ],
    "jvm": [
        # 异常请求数（代表代码异常）
        ("exception_count", 'sum(arms_exception_requests_count_ign_destid_endpoint_rpc{{service=~"{svc}"}}) by(service, endpoint)'),
    ],
    "system": [
        # CPU 空闲占比
        ("cpu_idle", 'avg(arms_system_cpu_idle{{service=~"{svc}"}}) by(service)'),
        # IO Wait
        ("cpu_io_wait", 'avg(arms_system_cpu_io_wait{{service=~"{svc}"}}) by(service)'),
        # 网络入口错误
        ("net_in_errs", 'sum(arms_system_net_in_errs{{service=~"{svc}"}}) by(service)'),
        # 网络出口错误
        ("net_out_errs", 'sum(arms_system_net_out_errs{{service=~"{svc}"}}) by(service)'),
    ],
}

_SERVICE_HEALTH_SCHEMA = {
    "name": "prometheus_service_health",
    "description": (
        "Prometheus/ARMS 监控查询的唯一入口。支持两种模式：\n"
        "1) 服务健康快照（默认）：传入 service（如 'nfc-.*' 或 'nfc-finance'），"
        "一次调用返回该服务的服务请求、数据库、JVM、系统四类核心监控指标，用于快速巡检服务健康状态；\n"
        "2) 任意指标查询：传入 query（PromQL），执行任意监控指标查询（即时或最近 10 分钟范围）。\n"
        "所有 Prometheus 监控相关查询都应使用本工具，避免多次调用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "服务过滤正则，如 'nfc-.*' 或 'nfc-finance'。用于服务健康快照模式。",
            },
            "dimensions": {
                "type": "array",
                "items": {"type": "string", "enum": ["request", "database", "jvm", "system"]},
                "description": "快照模式要检查的维度，不传则全部。",
            },
            "query": {
                "type": "string",
                "description": "任意 PromQL 查询语句（可选）。传入时执行该查询（最近 10 分钟范围），"
                               "用于查看具体指标趋势或深入定位。",
            },
            "start": {
                "type": "string",
                "description": "开始时间（Unix 时间戳）。不传则默认 10 分钟前。",
            },
            "end": {
                "type": "string",
                "description": "结束时间（Unix 时间戳）。不传则默认当前时间。",
            },
        },
        "required": [],
    },
}


def _query_instant_aggregate(promql: str) -> list[dict]:
    """执行一条即时聚合查询，返回简化结果（空结果/失败时返回 []）。"""
    try:
        result = _call_prometheus("api/v1/query", {"query": promql})
    except RuntimeError as e:
        logger.warning("prometheus 聚合查询失败: %s", e)
        return []
    if result.get("status") != "success":
        return []
    return _simplify_instant_result(result)


def _service_health_handler(args: dict, **kwargs: Any) -> str:
    args = args or {}
    import time as _time
    now = int(_time.time())
    start = args.get("start") or str(now - 600)  # 默认 10 分钟前
    end = args.get("end") or str(now)

    # 模式 2: 任意 PromQL 查询
    query = (args.get("query") or "").strip()
    if query:
        try:
            result = _call_prometheus("api/v1/query", {"query": query, "time": end})
        except RuntimeError as e:
            return tool_error(str(e))
        if result.get("status") != "success":
            return tool_error(f"Prometheus 查询失败: {result.get('error', 'unknown error')}")
        simplified = _simplify_instant_result(result)
        return tool_result(
            result_type="promql_query",
            raw_query=query,
            result_count=len(simplified),
            results=simplified[:20],
            truncated=len(simplified) > 20,
        )

    # 模式 1: 服务健康快照
    service = args.get("service", "").strip()
    if not service:
        return tool_error("请提供 service（服务快照）或 query（任意查询）参数")

    requested = (args.get("dimensions") or ["request", "database", "jvm", "system"])
    # 规范化用户传入的维度名
    requested = [d for d in requested if d in _DIMENSION_QUERIES]

    dimensions: dict[str, dict] = {}
    for dim in requested:
        dim_results: dict[str, Any] = {}
        for metric_name, promql_template in _DIMENSION_QUERIES[dim]:
            promql = promql_template.format(svc=service)
            rows = _query_instant_aggregate(promql)
            dim_results[metric_name] = {
                "query": promql,
                "result_count": len(rows),
                "results": rows[:20],  # 截断避免上下文过大
                "truncated": len(rows) > 20,
            }
        dimensions[dim] = dim_results

    return tool_result(
        result_type="service_health",
        service=service,
        time_range={"start": start, "end": end},
        dimensions=dimensions,
        hint=(
            "结果为空时表示该维度当前无数据（可能服务未上报或指标不存在），"
            "不要误判为正常。"
        ),
    )


# ============================================================
# 注册
# ============================================================

registry.register(
    name="prometheus_service_health",
    schema=_SERVICE_HEALTH_SCHEMA,
    handler=_service_health_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="🩺",
)