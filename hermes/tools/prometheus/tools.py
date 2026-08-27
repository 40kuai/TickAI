"""Prometheus 查询工具 — 通过 ARMS Prometheus HTTP API 查询监控指标。

可调用工具（每个工具覆盖一个查询场景，单次调用拿全所需指标，避免多轮调用）：

  1. prometheus_service_discovery — 服务发现：查询某个前缀（如 nfc-.*）下
     实际存在的服务名列表，供后续健康检查使用（对应 skill 的 Step 0）。
  2. prometheus_service_health — 服务健康快照：一次调用并行查询
     服务请求 / 数据库 / SQL / JVM / 系统 五个维度的全部核心指标，
     返回结构化健康快照，用于快速巡检服务健康状态。
  3. prometheus_metric_query — 任意指标深度查询：传入 PromQL 执行
     即时或范围查询（默认最近 10 分钟），用于快照发现异常后深入定位。

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


# ============================================================
# 工具 1: prometheus_service_discovery — 服务发现
# ============================================================

_SERVICE_DISCOVERY_SCHEMA = {
    "name": "prometheus_service_discovery",
    "description": (
        "发现某个前缀下的实际服务名列表（如 'nfc-.*'）。"
        "优先通过数据库请求耗时指标查询各服务的 service 标签，若为空则用 CPU 空闲指标兜底。"
        "返回该前缀下所有实际存在的服务名。\n"
        "在健康巡检前先调用本工具获取服务列表，再对每个服务做健康快照。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "service_prefix": {
                "type": "string",
                "description": "服务名前缀正则，如 'nfc-.*'。必填。",
            },
        },
        "required": ["service_prefix"],
    },
}


def _service_discovery_handler(args: dict, **kwargs: Any) -> str:
    args = args or {}
    prefix = (args.get("service_prefix") or "").strip()
    if not prefix:
        return tool_error("请提供 service_prefix（服务名前缀正则）")

    services: list[str] = []
    used_metric = "arms_db_requests_seconds_ign_rpc"

    # 主查询：数据库请求耗时指标（能反映大多数活跃服务）
    db_promql = 'avg(arms_db_requests_seconds_ign_rpc{{service=~"{svc}"}}) by(service)'.format(
        svc=prefix
    )
    rows = _query_instant_aggregate(db_promql)
    services = sorted({
        str(r["metric"].get("service")) for r in rows if r["metric"].get("service")
    })

    # 兜底：如果主查询为空，用 CPU 空闲指标
    if not services:
        used_metric = "arms_system_cpu_idle"
        cpu_promql = 'avg(arms_system_cpu_idle{{service=~"{svc}"}}) by(service)'.format(
            svc=prefix
        )
        rows = _query_instant_aggregate(cpu_promql)
        services = sorted({
            str(r["metric"].get("service")) for r in rows if r["metric"].get("service")
        })

    return tool_result(
        result_type="service_discovery",
        service_prefix=prefix,
        matched_metric=used_metric,
        count=len(services),
        services=services,
        hint=(
            "服务列表为空表示该前缀下当前没有上报数据的服务。"
            "注意：只有系统指标（CPU）而无数据库指标的服务可能不会出现在主查询结果中。"
        ),
    )


# ============================================================
# 工具 2: prometheus_service_health — 服务健康快照（覆盖全部白名单指标）
# ============================================================

# 各维度使用的 PromQL（service 过滤由 handler 注入）。
# 覆盖 skill 白名单中的全部指标（含 GC、db/sql 请求量/耗时、异常数等），
# 一次调用即可拿全五维核心指标，避免 LLM 反复调用导致轮次耗尽。
_DIMENSION_QUERIES: dict[str, List[tuple[str, str]]] = {
    "request": [
        # 服务请求数
        ("request_count", 'sum(arms_app_requests_count_ign_destid_endpoint_parent_ppid_prpc_rpc{{service=~"{svc}"}}) by(service)'),
        # 服务错误请求数
        ("error_count", 'sum(arms_app_requests_error_count_ign_destid_endpoint_parent_ppid_prpc_rpc{{service=~"{svc}"}}) by(service)'),
        # 服务慢请求数
        ("slow_count", 'sum(arms_app_requests_slow_count_ign_destid_endpoint_parent_ppid_prpc_rpc{{service=~"{svc}"}}) by(service)'),
        # 服务请求耗时
        ("request_seconds", 'avg(arms_app_requests_seconds_ign_destid_endpoint_parent_ppid_prpc_rpc{{service=~"{svc}"}}) by(service)'),
        # HTTP 200 占比
        ("http_200_ratio", 'sum(arms_requests_by_status_count_ign_rpc{{service=~"{svc}",status="200"}}) by(service) / sum(arms_requests_by_status_count_ign_rpc{{service=~"{svc}"}}) by(service)'),
    ],
    "database": [
        # 数据库请求数
        ("db_count", 'sum(arms_db_requests_count_ign_rpc{{service=~"{svc}"}}) by(service)'),
        # 数据库错误请求数
        ("db_error_count", 'sum(arms_db_requests_error_count_ign_rpc{{service=~"{svc}"}}) by(service)'),
        # 数据库慢请求数
        ("db_slow_count", 'sum(arms_db_requests_slow_count_ign_rpc{{service=~"{svc}"}}) by(service)'),
        # 数据库请求耗时
        ("db_seconds", 'avg(arms_db_requests_seconds_ign_rpc{{service=~"{svc}"}}) by(service, callKind, endpoint, destId)'),
    ],
    "sql": [
        # SQL 请求数
        ("sql_count", 'sum(arms_sql_requests_count_ign_rpc{{service=~"{svc}"}}) by(service)'),
        # SQL 错误请求数
        ("sql_error_count", 'sum(arms_sql_requests_error_count_ign_rpc{{service=~"{svc}"}}) by(service)'),
        # SQL 慢请求数
        ("sql_slow_count", 'sum(arms_sql_requests_slow_count_ign_rpc{{service=~"{svc}"}}) by(service, endpoint, destId)'),
        # SQL 请求耗时
        ("sql_seconds", 'avg(arms_sql_requests_seconds_ign_rpc{{service=~"{svc}"}}) by(service)'),
        # 异常请求数
        ("exception_count", 'sum(arms_exception_requests_count_ign_destid_endpoint_rpc{{service=~"{svc}"}}) by(service, endpoint)'),
    ],
    "jvm": [
        # Young GC 频率
        ("young_gc_rate", 'avg(irate(arms_jvm_gc_total{{service=~"{svc}",gen="young"}}[1m])) by(service, serverIp)'),
        # Old GC 频率
        ("old_gc_rate", 'avg(irate(arms_jvm_gc_total{{service=~"{svc}",gen="old"}}[1m])) by(service, serverIp)'),
        # Young GC 耗时
        ("young_gc_seconds", 'avg(increase(arms_jvm_gc_seconds_total{{service=~"{svc}",gen="young"}}[1m])) by(service, serverIp)'),
        # Old GC 耗时
        ("old_gc_seconds", 'avg(increase(arms_jvm_gc_seconds_total{{service=~"{svc}",gen="old"}}[1m])) by(service, serverIp)'),
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
        "服务健康快照：一次调用并行查询指定服务（如 'nfc-.*' 或 'nfc-finance'）的"
        "服务请求(request)、数据库(database)、SQL、JVM、系统(system) 五个维度的全部核心指标，"
        "返回结构化健康快照，用于快速巡检服务健康状态。\n"
        "巡检 nfc 等服务的健康/状态/异常时，先调用本工具拿快照；"
        "发现某维度异常后，再用 prometheus_metric_query 深入定位。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "服务过滤正则，如 'nfc-.*' 或 'nfc-finance'。必填。",
            },
            "dimensions": {
                "type": "array",
                "items": {"type": "string", "enum": ["request", "database", "sql", "jvm", "system"]},
                "description": "要检查的维度，不传则全部（request/database/sql/jvm/system）。",
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
        "required": ["service"],
    },
}


def _service_health_handler(args: dict, **kwargs: Any) -> str:
    args = args or {}
    import time as _time
    now = int(_time.time())
    start = args.get("start") or str(now - 600)  # 默认 10 分钟前
    end = args.get("end") or str(now)

    service = args.get("service", "").strip()
    if not service:
        return tool_error("请提供 service（服务过滤正则）")

    requested = args.get("dimensions") or list(_DIMENSION_QUERIES.keys())
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
# 工具 3: prometheus_metric_query — 任意指标深度查询
# ============================================================

_METRIC_QUERY_SCHEMA = {
    "name": "prometheus_metric_query",
    "description": (
        "任意 PromQL 指标深度查询：执行指定 PromQL（即时或最近 10 分钟范围查询）。"
        "用于健康快照发现某维度异常后，深入查看具体指标的趋势或明细。"
        "默认查最近 10 分钟，如需更长时间请显式传 start/end（Unix 时间戳）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "PromQL 查询语句。必填。",
            },
            "start": {
                "type": "string",
                "description": "开始时间（Unix 时间戳）。不传则默认 10 分钟前。",
            },
            "end": {
                "type": "string",
                "description": "结束时间（Unix 时间戳）。不传则默认当前时间。",
            },
            "step": {
                "type": "string",
                "description": "范围查询步长（如 '15s'）。仅范围查询用。",
            },
        },
        "required": ["query"],
    },
}


def _metric_query_handler(args: dict, **kwargs: Any) -> str:
    args = args or {}
    import time as _time
    now = int(_time.time())
    start = args.get("start") or str(now - 600)  # 默认 10 分钟前
    end = args.get("end") or str(now)

    query = (args.get("query") or "").strip()
    if not query:
        return tool_error("请提供 query（PromQL 查询语句）")

    step = args.get("step") or "15s"
    # 范围查询：带 start/end/step；即时查询：带 time
    try:
        result = _call_prometheus(
            "api/v1/query_range",
            {"query": query, "start": start, "end": end, "step": step},
        )
    except RuntimeError as e:
        return tool_error(str(e))
    if result.get("status") != "success":
        return tool_error(f"Prometheus 查询失败: {result.get('error', 'unknown error')}")
    simplified = _simplify_range_result(result)
    return tool_result(
        result_type="promql_query",
        raw_query=query,
        time_range={"start": start, "end": end, "step": step},
        result_count=len(simplified),
        results=simplified[:20],
        truncated=len(simplified) > 20,
    )


# ============================================================
# 注册
# ============================================================

registry.register(
    name="prometheus_service_discovery",
    schema=_SERVICE_DISCOVERY_SCHEMA,
    handler=_service_discovery_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="🔭",
)

registry.register(
    name="prometheus_service_health",
    schema=_SERVICE_HEALTH_SCHEMA,
    handler=_service_health_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="🩺",
)

registry.register(
    name="prometheus_metric_query",
    schema=_METRIC_QUERY_SCHEMA,
    handler=_metric_query_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="📈",
)
