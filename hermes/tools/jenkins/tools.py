"""Jenkins 只读查询工具 — 查询服务发布记录(仅 GET 请求,严格只读)。

可调用工具(每个工具覆盖一个查询场景):

  1. jenkins_service_jobs — 按服务名/关键字匹配 Jenkins job(如 nfc-camunda → 该服务
     各环境/部署方式的全部 job)。用于先定位某个服务对应的 job。
  2. jenkins_build_records — 查询单个 job 的最近构建发布记录(构建号、结果、
     开始时间、时长、git commit SHA、分支)。用于查看发布历史。

只读保证:工具仅使用 Jenkins GET API(/api/json?tree=...),绝不发起任何
POST/PUT/DELETE 请求(不触发构建、不创建/删除/修改 job)。

认证方式:HTTP Basic,从 .env 读取 JENKINS_URL / JENKINS_USER / JENKINS_PASSWORD。
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, List
from urllib.error import URLError, HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from hermes.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# 单个 job 列表返回上限(命中过多时截断并提示收窄关键字)
_JOBS_LIMIT = 30
# 构建记录默认/最大条数
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50


# ============================================================
# 配置读取
# ============================================================

def _get_config() -> tuple[str, str, str]:
    """从 .env 读取 Jenkins 连接配置。"""
    from hermes.config.settings import get as _get
    url = _get("JENKINS_URL", "").strip()
    user = _get("JENKINS_USER", "").strip()
    password = _get("JENKINS_PASSWORD", "").strip()
    if not url:
        raise RuntimeError("JENKINS_URL 未配置，请在 .env 中设置")
    if not user or not password:
        raise RuntimeError("JENKINS_USER / JENKINS_PASSWORD 未配置，请在 .env 中设置")
    return url, user, password


# ============================================================
# HTTP 调用(仅 GET,只读)
# ============================================================

def _call_jenkins(endpoint: str) -> dict:
    """调用 Jenkins GET API,返回解析后的 JSON 字典。

    Args:
        endpoint: API 路径,如 "api/json?tree=jobs[name,color]"。

    Returns:
        Jenkins 返回的 JSON 响应体。

    Raises:
        RuntimeError: 网络错误、非 200 状态码、JSON 解析失败。
    """
    url, user, password = _get_config()
    full_url = f"{url.rstrip('/')}/{endpoint.lstrip('/')}"

    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    req = Request(
        full_url,
        headers={
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except HTTPError as e:
        if e.code == 404:
            raise RuntimeError(f"Jenkins 返回 404：job 或资源不存在（请检查 job 名是否正确）") from e
        raise RuntimeError(f"Jenkins API 请求失败: HTTP {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"Jenkins API 请求失败: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Jenkins 返回非 JSON 响应: {e}") from e


# ============================================================
# 结果标准化
# ============================================================

# Jenkins job color → 最近构建状态
_COLOR_STATUS = {
    "blue": "SUCCESS",
    "blue_anime": "BUILDING(SUCCESS)",
    "red": "FAILURE",
    "red_anime": "BUILDING(FAILURE)",
    "aborted": "ABORTED",
    "aborted_anime": "BUILDING(ABORTED)",
    "yellow": "UNSTABLE",
    "yellow_anime": "BUILDING(UNSTABLE)",
    "notbuilt": "NOT_BUILT",
    "disabled": "DISABLED",
    "grey": "NOT_BUILT",
    "grey_anime": "BUILDING",
}


def _color_to_status(color: str) -> str:
    """把 Jenkins job color 转成人类可读状态。未知值原样返回。"""
    return _COLOR_STATUS.get(color, color or "UNKNOWN")


def _find_matching_jobs(job_names: List[str], keyword: str) -> List[str]:
    """按关键字匹配 job 名。

    匹配规则:关键字按 `-`/空白拆成词元,每个词元都需以大小写不敏感子串
    出现在 job 名中(AND)。如 "nfc-camunda" 能匹配 "nfc-be-camunda-company-k8s-prod"
    (包含 nfc 与 camunda)。空关键字返回空列表。
    """
    tokens = [t.lower() for t in keyword.strip().replace("_", "-").split("-") if t]
    tokens = [t for t in tokens if t]
    if not tokens:
        return []
    return [
        n for n in job_names
        if all(t in n.lower() for t in tokens)
    ]


def _format_timestamp(ts_ms: int) -> str:
    """毫秒时间戳 → 'YYYY-MM-DD HH:MM:SS' 本地时间(UTC+8)。"""
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        # 转本地时间显示(Asia/Shanghai = UTC+8)
        dt = dt.astimezone(timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, TypeError):
        return ""


def _format_duration(ms: int) -> str:
    """时长毫秒 → 'Xm Ys' 可读格式。"""
    try:
        total_sec = int(ms) // 1000
        m, s = divmod(total_sec, 60)
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h}h {m}m {s}s"
        return f"{m}m {s}s"
    except (ValueError, TypeError):
        return ""


# ============================================================
# 工具 1: jenkins_service_jobs — 按服务名匹配 job
# ============================================================

_SERVICE_JOBS_SCHEMA = {
    "name": "jenkins_service_jobs",
    "description": (
        "按服务名/关键字匹配 Jenkins 上的部署 job(严格只读,仅查询)。"
        "传入服务名或关键字(如 'camunda'、'mall'、'nfc'),返回匹配的所有 job,"
        "同一服务通常有多个 job(不同环境 dev/test/pre/prod 与部署方式 k8s/hsk8s/oss)。\n"
        "返回每个 job 的名称、最近构建状态和链接。匹配超过 30 个时会截断并提示收窄关键字。\n"
        "查询发布记录前,先用本工具确认服务对应的 job 名,再用 jenkins_build_records 查具体记录。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "服务名或关键字(大小写不敏感子串匹配 job 名)。必填。",
            },
        },
        "required": ["service"],
    },
}


def _service_jobs_handler(args: dict, **kwargs: Any) -> str:
    args = args or {}
    service = (args.get("service") or "").strip()
    if not service:
        return tool_error("请提供 service（服务名或关键字）")

    try:
        data = _call_jenkins("api/json?tree=jobs[name,color]")
    except RuntimeError as e:
        return tool_error(str(e))

    all_names = [j.get("name", "") for j in data.get("jobs", [])]
    matched = _find_matching_jobs(all_names, service)

    # 组织结果:名称 + 状态 + 链接
    color_map = {j.get("name"): j.get("color", "") for j in data.get("jobs", [])}
    jobs = []
    for name in matched[:_JOBS_LIMIT]:
        jobs.append({
            "name": name,
            "status": _color_to_status(color_map.get(name, "")),
            "url": f"/job/{quote(name)}",
        })

    truncated = len(matched) > _JOBS_LIMIT
    return tool_result(
        result_type="service_jobs",
        service=service,
        count=len(jobs),
        total=len(matched),
        truncated=truncated,
        jobs=jobs,
        hint=(
            "未匹配到任何 job 时,请检查服务名拼写,或尝试更短的关键字(如 'camunda' 而非全名)。"
            if not matched else
            f"匹配到 {len(matched)} 个 job,已展示前 {_JOBS_LIMIT} 个,请用更精确的关键字缩小范围。"
            if truncated else
            "确认 job 名后,用 jenkins_build_records 查询该 job 的发布记录。"
        ),
    )


# ============================================================
# 工具 2: jenkins_build_records — 查询单个 job 的发布记录
# ============================================================

_BUILD_RECORDS_SCHEMA = {
    "name": "jenkins_build_records",
    "description": (
        "查询某个 Jenkins job 的最近构建发布记录(严格只读,仅查询)。"
        "传入完整 job 名(用 jenkins_service_jobs 获取),返回最近 N 次构建的"
        "构建号、结果(SUCCESS/FAILURE/ABORTED)、开始时间、时长,"
        "以及该次构建对应的 git commit SHA 和分支(如可获取)。\n"
        "用于查看服务发布历史与定位失败构建。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_name": {
                "type": "string",
                "description": "完整的 Jenkins job 名,如 'nfc-be-camunda-company-k8s-prod'。必填。",
            },
            "limit": {
                "type": "integer",
                "description": "返回最近几次构建。默认 10,最大 50。",
                "default": 10,
            },
        },
        "required": ["job_name"],
    },
}


def _extract_revision(build_detail: dict) -> dict:
    """从构建详情提取 git commit SHA 与分支(lastBuiltRevision)。"""
    for action in build_detail.get("actions", []):
        rev = action.get("lastBuiltRevision")
        if not rev:
            continue
        sha = rev.get("SHA1") or rev.get("revision")
        branch = None
        for b in rev.get("branch", []) or []:
            if b.get("name"):
                branch = b["name"]
                break
        if sha:
            return {"commit_sha": sha, "branch": branch}
    return {"commit_sha": None, "branch": None}


def _build_records_handler(args: dict, **kwargs: Any) -> str:
    args = args or {}
    job_name = (args.get("job_name") or "").strip()
    if not job_name:
        return tool_error("请提供 job_name（完整 Jenkins job 名）")

    try:
        limit = int(args.get("limit", _DEFAULT_LIMIT))
    except (ValueError, TypeError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    job_path = quote(job_name, safe="")
    try:
        data = _call_jenkins(f"job/{job_path}/api/json?tree=name,builds[number,result,timestamp,duration]")
    except RuntimeError as e:
        return tool_error(str(e))

    builds = data.get("builds", [])[:limit]
    records = []
    for b in builds:
        number = b.get("number")
        # 逐条取构建详情拿 git 信息(仅 GET)
        try:
            detail = _call_jenkins(
                f"job/{job_path}/{number}/api/json?tree=number,result,timestamp,duration,actions[lastBuiltRevision[SHA1,branch[name]]]"
            )
            rev = _extract_revision(detail)
        except RuntimeError as e:
            logger.warning("jenkins 构建详情获取失败 %s #%s: %s", job_name, number, e)
            rev = {"commit_sha": None, "branch": None}

        records.append({
            "number": number,
            "result": b.get("result") or "RUNNING",
            "start_time": _format_timestamp(b.get("timestamp")),
            "duration": _format_duration(b.get("duration")),
            **rev,
        })

    return tool_result(
        result_type="build_records",
        job_name=data.get("name") or job_name,
        limit=limit,
        count=len(records),
        builds=records,
        hint=(
            "job 无构建历史,或所有构建详情均取不到 git 信息。"
            "commit_sha/branch 为空表示该 Pipeline 未启用 changeSet 追踪。"
            if not records or all(r["commit_sha"] is None for r in records)
            else ""
        ),
    )


# ============================================================
# 注册
# ============================================================

registry.register(
    name="jenkins_service_jobs",
    schema=_SERVICE_JOBS_SCHEMA,
    handler=_service_jobs_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="📦",
)

registry.register(
    name="jenkins_build_records",
    schema=_BUILD_RECORDS_SCHEMA,
    handler=_build_records_handler,
    check_fn=lambda: True,
    toolset="monitoring",
    emoji="🗂️",
)
