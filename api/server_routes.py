"""Server management routes - CRUD, SSH inspection, and external sync."""
from __future__ import annotations

import json
import subprocess
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from hermes.data.db import session_scope
from hermes.data.models import Server, SyncConfig, SSHCredential
from hermes.tools.registry import registry
from hermes.tools.ssh import runner as ssh_runner

from .deps import get_current_user

router = APIRouter(prefix="/api/servers", tags=["servers"])


class ServerCreate(BaseModel):
    name: str
    host: str
    tags: str = ""
    notes: str = ""
    ssh_credential_id: Optional[int] = None


@router.get("")
def list_servers(user=Depends(get_current_user)):
    """List all servers (passwords masked). Reuses list_servers tool."""
    result_json = registry.dispatch("list_servers", {"active_only": True})
    return json.loads(result_json)


@router.post("")
def add_server(server: ServerCreate, user=Depends(get_current_user)):
    """Add a new server."""
    with session_scope() as s:
        sv = Server(
            name=server.name,
            host=server.host,
            tags=server.tags,
            notes=server.notes,
            ssh_credential_id=server.ssh_credential_id,
        )
        s.add(sv)
        s.flush()
        result = sv.to_dict()
    return result


@router.delete("/{server_id}")
def delete_server(server_id: int, user=Depends(get_current_user)):
    """Soft-delete a server (mark inactive)."""
    with session_scope() as s:
        sv = s.get(Server, server_id)
        if sv is None or not sv.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server not found",
            )
        sv.is_active = False
    return {"detail": "Server deleted"}


def _get_server_credentials(server_id: int) -> dict:
    """Look up a server's SSH credentials. Falls back to default credential.
    Raises 404/400 if not available."""
    with session_scope() as s:
        sv = s.get(Server, server_id)
        if sv is None or not sv.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server not found",
            )
        # Use bound credential, or fall back to default
        cred = sv.ssh_credential
        if cred is None:
            cred = s.query(SSHCredential).filter(SSHCredential.is_default == True).first()
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Server has no SSH credential bound and no default credential set",
            )
        return {"host": sv.host, **cred.to_connect_args()}


def _dispatch_and_persist(
    server_id: int, tool_name: str, command_label: str
) -> dict:
    """Dispatch an SSH tool with server credentials and persist the audit."""
    args = _get_server_credentials(server_id)
    result_json = registry.dispatch(tool_name, args)
    # Persist audit record (best-effort, never breaks the response)
    try:
        ssh_runner.persist_tool_run(
            server_id, command_label, result_json, "user_button"
        )
    except Exception:
        pass
    return json.loads(result_json)


@router.post("/{server_id}/check_disk")
def check_disk(server_id: int, user=Depends(get_current_user)):
    """Check disk usage on a server via SSH."""
    return _dispatch_and_persist(server_id, "check_disk_usage", "df -Th")


@router.post("/{server_id}/check_resources")
def check_resources(server_id: int, user=Depends(get_current_user)):
    """Check CPU/memory resources on a server via SSH."""
    return _dispatch_and_persist(server_id, "check_resources", "check_resources")


@router.post("/{server_id}/list_services")
def list_services(server_id: int, user=Depends(get_current_user)):
    """List systemd services on a server via SSH."""
    return _dispatch_and_persist(server_id, "list_services", "list_services")


# ============================================================
# External host sync (page-configurable)
# ============================================================

DEFAULT_FIELD_MAPPING = {
    "name": "name",
    "host": "host",
    "tags": "tags",
    "notes": "notes",
}


class SyncConfigUpdate(BaseModel):
    api_url: str = ""
    auth_type: str = "none"  # none|basic|bearer
    auth_username: str = ""
    auth_password: str = ""
    api_token: str = ""
    response_path: str = ""
    timeout: int = 30
    field_mapping: dict = DEFAULT_FIELD_MAPPING
    enabled: bool = False


class SyncTestRequest(BaseModel):
    api_url: str = ""
    auth_type: str = "none"
    auth_username: str = ""
    auth_password: str = ""
    api_token: str = ""
    response_path: str = ""
    timeout: int = 30
    field_mapping: dict = DEFAULT_FIELD_MAPPING


def _get_or_create_sync_config(s) -> SyncConfig:
    """Get the single sync config row, create if not exists."""
    cfg = s.get(SyncConfig, 1)
    if cfg is None:
        cfg = SyncConfig(
            id=1,
            field_mapping=json.dumps(DEFAULT_FIELD_MAPPING),
        )
        s.add(cfg)
        s.flush()
    return cfg


def _extract_nested(data, path: str):
    """Extract a value from nested JSON using dot-notation path."""
    if not path:
        return data
    for key in path.split("."):
        if isinstance(data, dict):
            data = data.get(key, [])
        elif isinstance(data, list) and key.isdigit():
            data = data[int(key)] if int(key) < len(data) else []
        else:
            return []
    return data


def _apply_mapping(item: dict, mapping: dict) -> dict:
    """Map external fields to internal fields using the mapping config."""
    result = {}
    for internal_field, external_field in mapping.items():
        if external_field and isinstance(item, dict):
            result[internal_field] = item.get(external_field)
    return result


def _build_auth_headers(cfg) -> dict:
    """Build HTTP auth headers based on config."""
    headers = {"Content-Type": "application/json"}
    auth_type = getattr(cfg, "auth_type", "none") or "none"
    if auth_type == "bearer":
        token = getattr(cfg, "api_token", "") or ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "basic":
        import base64
        username = getattr(cfg, "auth_username", "") or ""
        password = getattr(cfg, "auth_password", "") or ""
        if username:
            cred = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {cred}"
    return headers


def _fetch_hosts(cfg) -> list:
    """Fetch host list from external API with pagination support."""
    if not cfg.api_url:
        raise ValueError("API URL 未配置")

    headers = _build_auth_headers(cfg)
    timeout = int(cfg.timeout or 30)
    response_path = cfg.response_path or ""

    all_items = []
    url = cfg.api_url
    page = 0
    max_pages = 50  # safety limit

    while url and page < max_pages:
        page += 1
        # Use subprocess curl for better SSL compatibility
        curl_cmd = ["curl", "-s", "--max-time", str(timeout), "-X", "GET"]
        for k, v in headers.items():
            curl_cmd.extend(["-H", f"{k}: {v}"])
        curl_cmd.append(url)

        try:
            result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=timeout + 10)
            if result.returncode != 0:
                raise ValueError(f"curl 失败: {result.stderr[:200]}")
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ValueError("外部 API 返回非 JSON 数据")
        except subprocess.TimeoutExpired:
            raise ValueError(f"外部 API 请求超时 ({timeout}s)")
        except Exception as exc:
            raise ValueError(f"无法连接外部 API: {exc}")

        # Extract items from current page
        page_items = _extract_nested(data, response_path)
        if not isinstance(page_items, list):
            if page == 1:
                raise ValueError(
                    f"响应格式不符，期望列表，得到 {type(page_items).__name__}。"
                    f"如需从嵌套字段提取，请配置响应路径。"
                )
            break

        all_items.extend(page_items)

        # Check for pagination (next URL)
        next_url = None
        if isinstance(data, dict):
            next_url = data.get("next")
        if not next_url:
            break
        # Fix http->https if original URL used https
        if cfg.api_url.startswith("https://") and next_url.startswith("http://"):
            next_url = "https://" + next_url[len("http://"):]
        url = next_url

    return all_items


@router.get("/sync/config")
def get_sync_config(user=Depends(get_current_user)):
    """Get current sync configuration."""
    with session_scope() as s:
        cfg = _get_or_create_sync_config(s)
        return cfg.to_dict()


@router.put("/sync/config")
def update_sync_config(req: SyncConfigUpdate, user=Depends(get_current_user)):
    """Save sync configuration."""
    with session_scope() as s:
        cfg = _get_or_create_sync_config(s)
        cfg.api_url = req.api_url
        cfg.auth_type = req.auth_type
        cfg.auth_username = req.auth_username
        cfg.auth_password = req.auth_password
        cfg.api_token = req.api_token
        cfg.response_path = req.response_path
        cfg.timeout = req.timeout
        cfg.field_mapping = json.dumps(req.field_mapping, ensure_ascii=False)
        cfg.enabled = req.enabled
        return cfg.to_dict()


@router.post("/sync/test")
def test_sync(req: SyncTestRequest, user=Depends(get_current_user)):
    """Test connection to external API. Returns preview without writing to DB."""
    cfg = SyncConfig(
        api_url=req.api_url,
        auth_type=req.auth_type,
        auth_username=req.auth_username,
        auth_password=req.auth_password,
        api_token=req.api_token,
        response_path=req.response_path,
        timeout=req.timeout,
        field_mapping=json.dumps(req.field_mapping),
    )

    try:
        raw_list = _fetch_hosts(cfg)
    except ValueError as exc:
        return {"success": False, "error": str(exc), "total": 0, "preview": []}

    mapping = req.field_mapping or DEFAULT_FIELD_MAPPING
    preview = []
    for item in raw_list[:10]:
        if isinstance(item, dict):
            mapped = _apply_mapping(item, mapping)
            preview.append({
                "name": mapped.get("name") or mapped.get("host") or "",
                "host": mapped.get("host") or "",
                "tags": mapped.get("tags") or "",
            })

    return {
        "success": True,
        "total": len(raw_list),
        "preview": preview,
    }


@router.post("/sync")
def sync_servers(user=Depends(get_current_user)):
    """Fetch hosts from external API and upsert into DB (real-time)."""
    with session_scope() as s:
        cfg = _get_or_create_sync_config(s)
        if not cfg.api_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请先配置同步 API 地址",
            )
        # Detach config values before session closes
        cfg_data = cfg.to_dict()

    # Build a temp config for fetching (outside session)
    temp_cfg = SyncConfig(
        api_url=cfg_data["api_url"],
        auth_type=cfg_data.get("auth_type", "none"),
        auth_username=cfg_data.get("auth_username", ""),
        auth_password=cfg_data.get("auth_password", ""),
        api_token=cfg_data.get("api_token", ""),
        response_path=cfg_data["response_path"],
        timeout=cfg_data["timeout"],
        field_mapping=json.dumps(cfg_data["field_mapping"]),
    )

    try:
        raw_list = _fetch_hosts(temp_cfg)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )

    mapping = cfg_data["field_mapping"] or DEFAULT_FIELD_MAPPING

    added, updated, skipped = 0, 0, 0
    errors = []

    with session_scope() as s:
        for idx, item in enumerate(raw_list):
            if not isinstance(item, dict):
                skipped += 1
                continue

            mapped = _apply_mapping(item, mapping)
            host = mapped.get("host") or ""
            if not host:
                skipped += 1
                errors.append(f"#{idx}: 映射后缺少 host 字段")
                continue

            name = mapped.get("name") or host
            tags = mapped.get("tags") or ""
            if isinstance(tags, list):
                tags = ",".join(str(t) for t in tags)
            notes = mapped.get("notes") or ""

            existing = s.query(Server).filter(Server.host == host).first()
            if existing:
                existing.name = name
                existing.tags = tags
                existing.notes = notes
                existing.is_active = True
                updated += 1
            else:
                sv = Server(
                    name=name,
                    host=host,
                    tags=tags,
                    notes=notes,
                )
                s.add(sv)
                added += 1

    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "total": len(raw_list),
        "errors": errors[:10],
    }
