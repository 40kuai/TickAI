"""SSH credential management routes - CRUD and server binding."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional

from hermes.data.db import session_scope
from hermes.data.models import Server, SSHCredential

from .deps import get_current_user

router = APIRouter(prefix="/api/ssh-credentials", tags=["ssh-credentials"])


class CredentialCreate(BaseModel):
    name: str
    port: int = 22
    username: str = "root"
    auth_type: str = "password"  # password|key
    password: str = ""
    key_content: str = ""
    description: str = ""


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    auth_type: Optional[str] = None
    password: Optional[str] = None
    key_content: Optional[str] = None
    description: Optional[str] = None


class ServerCredentialBind(BaseModel):
    ssh_credential_id: Optional[int] = None


@router.get("")
def list_credentials(user=Depends(get_current_user)):
    """List all SSH credentials with server count."""
    with session_scope() as s:
        creds = s.query(SSHCredential).order_by(SSHCredential.name).all()
        result = []
        for c in creds:
            count = s.query(Server).filter(Server.ssh_credential_id == c.id).count()
            d = c.to_dict()
            d["server_count"] = count
            result.append(d)
        return {"count": len(result), "credentials": result}


@router.post("")
def create_credential(req: CredentialCreate, user=Depends(get_current_user)):
    """Create a new SSH credential."""
    with session_scope() as s:
        existing = s.query(SSHCredential).filter(SSHCredential.name == req.name).first()
        if existing:
            raise HTTPException(400, f"凭据名称 '{req.name}' 已存在")
        c = SSHCredential(
            name=req.name,
            port=req.port,
            username=req.username,
            auth_type=req.auth_type,
            password=req.password,
            key_content=req.key_content,
            description=req.description,
        )
        s.add(c)
        s.flush()
        return c.to_dict()


@router.put("/{credential_id}")
def update_credential(credential_id: int, req: CredentialUpdate, user=Depends(get_current_user)):
    """Update an SSH credential."""
    with session_scope() as s:
        c = s.get(SSHCredential, credential_id)
        if not c:
            raise HTTPException(404, "凭据不存在")
        if req.name is not None:
            existing = s.query(SSHCredential).filter(
                SSHCredential.name == req.name, SSHCredential.id != credential_id
            ).first()
            if existing:
                raise HTTPException(400, f"凭据名称 '{req.name}' 已存在")
            c.name = req.name
        if req.port is not None:
            c.port = req.port
        if req.username is not None:
            c.username = req.username
        if req.auth_type is not None:
            c.auth_type = req.auth_type
        if req.password is not None:
            c.password = req.password
        if req.key_content is not None:
            c.key_content = req.key_content
        if req.description is not None:
            c.description = req.description
        return c.to_dict()


@router.delete("/{credential_id}")
def delete_credential(credential_id: int, user=Depends(get_current_user)):
    """Delete an SSH credential. Servers bound to it will be unbound."""
    with session_scope() as s:
        c = s.get(SSHCredential, credential_id)
        if not c:
            raise HTTPException(404, "凭据不存在")
        # Unbind all servers using this credential
        servers = s.query(Server).filter(Server.ssh_credential_id == credential_id).all()
        for sv in servers:
            sv.ssh_credential_id = None
        s.delete(c)
    return {"detail": "凭据已删除", "unbound_servers": len(servers)}


@router.put("/{credential_id}/default")
def set_default_credential(credential_id: int, user=Depends(get_current_user)):
    """Set a credential as the default. Unsets all others."""
    with session_scope() as s:
        c = s.get(SSHCredential, credential_id)
        if not c:
            raise HTTPException(404, "凭据不存在")
        # Unset all other defaults
        s.query(SSHCredential).filter(
            SSHCredential.id != credential_id
        ).update({SSHCredential.is_default: False})
        c.is_default = True
        return c.to_dict()


@router.put("/bind/{server_id}")
def bind_credential(server_id: int, req: ServerCredentialBind, user=Depends(get_current_user)):
    """Bind or unbind an SSH credential to a server."""
    with session_scope() as s:
        sv = s.get(Server, server_id)
        if not sv:
            raise HTTPException(404, "服务器不存在")
        if req.ssh_credential_id is not None:
            cred = s.get(SSHCredential, req.ssh_credential_id)
            if not cred:
                raise HTTPException(404, "凭据不存在")
            sv.ssh_credential_id = req.ssh_credential_id
        else:
            sv.ssh_credential_id = None
        return sv.to_dict()
