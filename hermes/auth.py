"""
Authentication utilities - SQLite backed.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st
from sqlalchemy import select

from .data.db import session_scope
from .data.models import User, UserSession

_SESSION_EXPIRE_HOURS: int = 24


def hash_password(password: str) -> str:
    """Hash a password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


def init_default_user() -> None:
    """Initialize the default admin user if no users exist."""
    with session_scope() as sess:
        count = sess.query(User).count()
        if count == 0:
            default = User(
                username="admin",
                password_hash=hash_password("admin123"),
                is_active=True,
            )
            sess.add(default)
            sess.commit()


def get_user(username: str) -> Optional[User]:
    """Get a user by username."""
    with session_scope() as sess:
        return sess.query(User).filter(User.username == username).first()


def authenticate(username: str, password: str) -> Optional[User]:
    """Authenticate user by username and password."""
    with session_scope() as sess:
        user = sess.query(User).filter(
            User.username == username,
            User.is_active.is_(True)
        ).first()

        if user and user.password_hash == hash_password(password):
            user.last_login = datetime.utcnow()
            return user
    return None


def create_session(user: User) -> str:
    """Create a new session for the user. Returns session ID."""
    session_id = str(uuid.uuid4())
    now = datetime.utcnow()
    expire = now + timedelta(hours=_SESSION_EXPIRE_HOURS)

    with session_scope() as sess:
        session = UserSession(
            id=session_id,
            user_id=user.id,
            username=user.username,
            created_at=now,
            expires_at=expire,
        )
        sess.add(session)
        sess.commit()

    return session_id


def validate_session(session_id: str) -> Optional[User]:
    """Validate session and return User object if valid."""
    if not session_id:
        return None

    with session_scope() as sess:
        session = sess.query(UserSession).filter(
            UserSession.id == session_id
        ).first()

        if not session:
            return None

        if datetime.utcnow() > session.expires_at:
            sess.delete(session)
            sess.commit()
            return None

        return sess.query(User).get(session.user_id)


def cleanup_expired_sessions() -> int:
    """Delete all expired sessions. Returns count deleted."""
    with session_scope() as sess:
        expired = sess.query(UserSession).filter(
            UserSession.expires_at < datetime.utcnow()
        ).all()

        count = len(expired)
        for s in expired:
            sess.delete(s)
        sess.commit()
        return count


def logout() -> None:
    """Log the current user out and clean up session."""
    session_id = st.session_state.get("session_id")

    if session_id:
        with session_scope() as sess:
            session = sess.query(UserSession).filter(
                UserSession.id == session_id
            ).first()
            if session:
                sess.delete(session)
                sess.commit()

    for key in list(st.session_state.keys()):
        del st.session_state[key]

    st.query_params.clear()
    st.rerun()


def _preserve_session_in_url() -> None:
    """Ensure session_id stays in URL when navigating between pages."""
    current_session = st.session_state.get("session_id")
    if current_session:
        # Explicitly keep session_id in URL
        if "session_id" not in st.query_params or st.query_params["session_id"] != current_session:
            st.query_params.session_id = current_session


def _get_session_id_from_url() -> Optional[str]:
    """Get session_id from URL params."""
    if "session_id" in st.query_params:
        val = st.query_params["session_id"]
        if val and isinstance(val, str) and len(val) > 0:
            return val
    return None


def require_login() -> None:
    """Require login on every page."""
    # Make sure default user exists
    init_default_user()

    # Step 1: Preserve session in URL (fix nav issue)
    _preserve_session_in_url()

    # Step 2: If already authenticated, validate
    if st.session_state.get("authenticated", False):
        session_id = st.session_state.get("session_id")
        user = validate_session(session_id)
        if user:
            return
        # Session invalid, clear state
        st.session_state["authenticated"] = False
        st.session_state.pop("user", None)
        st.session_state.pop("session_id", None)

    # Step 3: Try to restore session from URL params
    session_id_from_url = _get_session_id_from_url()
    if session_id_from_url:
        user = validate_session(session_id_from_url)
        if user:
            st.session_state["authenticated"] = True
            st.session_state["user"] = user.to_dict()
            st.session_state["session_id"] = session_id_from_url
            # Ensure URL is synced
            st.query_params.session_id = session_id_from_url
            return

    # Step 4: Not authenticated - show login form
    show_login()
    st.stop()


def show_login() -> None:
    """Show login form."""
    st.markdown("""
    <div style="
        max-width: 400px;
        margin: 60px auto 20px;
        text-align: center;
    ">
        <h1 style="font-size: 42px; margin-bottom: 8px;">🎫 TickAI</h1>
        <p style="color: #64748b;">AI-Powered Ops Ticket Platform</p>
    </div>
    """, unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("### 🔐 Login")

        if "login_username" not in st.session_state:
            st.session_state["login_username"] = ""

        with st.form("login_form", border=False):
            username = st.text_input("Username", value=st.session_state["login_username"])
            password = st.text_input("Password", type="password")

            submitted = st.form_submit_button("Login", type="primary", use_container_width=True)

        if submitted:
            user = authenticate(username, password)
            if user:
                session_id = create_session(user)
                st.session_state["authenticated"] = True
                st.session_state["user"] = user.to_dict()
                st.session_state["session_id"] = session_id
                st.session_state["login_username"] = ""
                st.query_params.session_id = session_id
                st.success(f"Welcome back, {user.username}!")
                st.rerun()
            else:
                st.error("Invalid username or password")
                st.session_state["login_username"] = username

        with st.expander("💡 Need to reset admin password?"):
            if st.button("Reset Admin (admin/admin123)", use_container_width=True):
                with session_scope() as sess:
                    admin = sess.query(User).filter(User.username == "admin").first()
                    if admin:
                        admin.password_hash = hash_password("admin123")
                        sess.commit()
                    else:
                        admin = User(
                            username="admin",
                            password_hash=hash_password("admin123"),
                            is_active=True,
                        )
                        sess.add(admin)
                        sess.commit()
                st.success("Admin password reset! Login with admin/admin123.")
                st.rerun()

        st.caption("Default: admin / admin123")
