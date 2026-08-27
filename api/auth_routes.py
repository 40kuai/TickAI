"""Authentication routes - login / logout / me.

Reuses hermes.auth.authenticate() for credential verification and
hermes.auth.create_session() for DB session creation. The session_id is
wrapped in a JWT and stored in an HttpOnly Cookie.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from hermes.auth import authenticate, create_session
from hermes.data.db import session_scope
from hermes.data.models import User, UserSession

from .deps import (
    JWT_EXPIRE_HOURS,
    create_access_token,
    decode_access_token,
    get_current_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(req: LoginRequest, response: Response):
    """Verify credentials and set HttpOnly Cookie with JWT."""
    user = authenticate(req.username, req.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    session_id = create_session(user)
    token = create_access_token(
        {
            "session_id": session_id,
            "sub": user.username,
            "user_id": user.id,
        }
    )

    response.set_cookie(
        key="access_token",
        value=token,
        max_age=JWT_EXPIRE_HOURS * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )

    return user.to_dict()


@router.post("/logout")
def logout(request: Request, response: Response):
    """Clear the auth cookie and invalidate the DB session (best-effort)."""
    token = request.cookies.get("access_token")
    if token:
        payload = decode_access_token(token)
        if payload:
            session_id = payload.get("session_id")
            if session_id:
                with session_scope() as s:
                    session = (
                        s.query(UserSession)
                        .filter(UserSession.id == session_id)
                        .first()
                    )
                    if session:
                        s.delete(session)

    response.delete_cookie(key="access_token", path="/")
    return {"detail": "Logged out"}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return user.to_dict()
