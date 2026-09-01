"""Dependency injection - JWT auth via HttpOnly Cookie.

The JWT carries the session_id created by hermes.auth.create_session().
On each request we decode the JWT, extract the session_id, and validate it
against the DB via hermes.auth.validate_session(). This reuses the existing
session infrastructure while adding a signed token layer so the cookie
cannot be tampered with.
"""
from __future__ import annotations

import os
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from hermes.auth import validate_session
from hermes.config.settings import get as settings_get
from hermes.data.models import User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JWT configuration
# ---------------------------------------------------------------------------
# Secret is read from JWT_SECRET (env var or .env file, via settings.get).
# If not set, a random value is generated on each startup. In production the
# operator MUST set JWT_SECRET — otherwise tokens do not survive restarts and
# a multi-worker deployment cannot validate each other's tokens.
JWT_SECRET: str = settings_get("JWT_SECRET") or secrets.token_urlsafe(32)
if not settings_get("JWT_SECRET"):
    logger.warning(
        "JWT_SECRET 未配置，已生成随机密钥。生产环境必须设置 JWT_SECRET，"
        "否则重启后所有登录态失效，且多 worker 之间无法互认 token。"
    )
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_HOURS: int = 24


def create_access_token(data: dict) -> str:
    """Create a signed JWT containing *data* plus an expiry claim."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT. Returns the payload dict or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


def get_current_user(request: Request) -> User:
    """FastAPI dependency: verify the user from the HttpOnly Cookie JWT.

    Returns the authenticated User object or raises 401.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    session_id = payload.get("session_id")
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = validate_session(session_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    return user
