"""
Authentication utilities - SQLite backed.
"""
from __future__ import annotations

import bcrypt
import uuid
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st
from sqlalchemy import select

from .data.db import session_scope
from .data.models import User, UserSession

_SESSION_EXPIRE_HOURS: int = 24


def hash_password(password: str) -> str:
    """Hash a password using bcrypt with automatic salt.

    bcrypt provides:
    - Automatic salt generation (no separate salt storage)
    - Adaptive cost factor (slower = harder to brute-force)
    - Designed specifically for password hashing
    """
    # bcrypt requires bytes
    password_bytes = password.encode("utf-8")
    # Generate salt with default work factor (12)
    salt = bcrypt.gensalt()
    # Hash and return as string (contains salt inside)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def init_default_user() -> None:
    """Initialize the default admin user if no users exist.

    Password is read from ADMIN_INITIAL_PASSWORD environment variable.
    If not set, generates a random password and prints it to console.
    Never uses hardcoded default credentials.
    """
    import secrets
    from hermes.config.settings import get

    with session_scope() as sess:
        count = sess.query(User).count()
        if count == 0:
            # Get password from environment, or generate secure random
            env_password = get("ADMIN_INITIAL_PASSWORD", "").strip()
            if env_password:
                password = env_password
                print("[AUTH] Using admin password from ADMIN_INITIAL_PASSWORD", flush=True)
            else:
                # Generate a secure random password (16 chars, alphanumeric)
                password = secrets.token_urlsafe(12)
                print("=" * 60, flush=True)
                print("[AUTH] ADMIN CREDENTIALS GENERATED", flush=True)
                print(f"  Username: admin", flush=True)
                print(f"  Password: {password}", flush=True)
                print("  Save this password! It won't be shown again.", flush=True)
                print("=" * 60, flush=True)

            default = User(
                username="admin",
                password_hash=hash_password(password),
                is_active=True,
            )
            sess.add(default)
            sess.commit()


def get_user(username: str) -> Optional[User]:
    """Get a user by username."""
    with session_scope() as sess:
        return sess.query(User).filter(User.username == username).first()


def _is_sha256_hash(hash_str: str) -> bool:
    """Check if a hash is SHA-256 format (64 hex chars)."""
    return len(hash_str) == 64 and all(c in "0123456789abcdef" for c in hash_str.lower())


def authenticate(username: str, password: str) -> Optional[User]:
    """Authenticate user by username and password.

    Transparently migrates SHA-256 hashes to bcrypt on first login.
    """
    with session_scope() as sess:
        user = sess.query(User).filter(
            User.username == username,
            User.is_active.is_(True)
        ).first()

        if not user:
            return None

        # Case 1: bcrypt hash (modern)
        if user.password_hash.startswith("$2b$"):
            if verify_password(password, user.password_hash):
                user.last_login = datetime.utcnow()
                return user
            return None

        # Case 2: SHA-256 hash (legacy) - verify and migrate
        import hashlib
        legacy_hash = hashlib.sha256(password.encode()).hexdigest()
        if user.password_hash == legacy_hash:
            # Migration: upgrade to bcrypt
            user.password_hash = hash_password(password)
            user.last_login = datetime.utcnow()
            sess.commit()
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


def _set_session_param(session_id: str) -> None:
    """Store session_id in query_params for refresh persistence."""
    st.query_params["sid"] = session_id


def _clear_session_param() -> None:
    """Remove session_id from query_params."""
    if "sid" in st.query_params:
        del st.query_params["sid"]


def _get_session_from_param() -> Optional[str]:
    """Get session_id from query_params (survives page refresh)."""
    sid = st.query_params.get("sid")
    if sid and isinstance(sid, str) and len(sid) > 0:
        return sid
    return None


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

    _clear_session_param()

    for key in list(st.session_state.keys()):
        del st.session_state[key]

    st.rerun()


def require_login() -> None:
    """Require login on every page.

    Session lifecycle:
    1. st.session_state holds login state during active tab session
    2. On page refresh, st.session_state is lost -> restore from query_params
    3. When authenticated, always sync sid to URL for refresh persistence
    """
    # Make sure default user exists
    init_default_user()

    # Step 1: If already authenticated in st.session_state, validate
    if st.session_state.get("authenticated", False):
        session_id = st.session_state.get("session_id")
        user = validate_session(session_id)
        if user:
            # Ensure sid is in URL so refresh keeps the session
            if st.query_params.get("sid") != session_id:
                st.query_params["sid"] = session_id
            return
        # Session invalid, clear state
        st.session_state["authenticated"] = False
        st.session_state.pop("user", None)
        st.session_state.pop("session_id", None)
        _clear_session_param()

    # Step 2: Try restore from query_params (survives refresh)
    session_id_from_param = _get_session_from_param()
    if session_id_from_param:
        user = validate_session(session_id_from_param)
        if user:
            st.session_state["authenticated"] = True
            st.session_state["user"] = user.to_dict()
            st.session_state["session_id"] = session_id_from_param
            return
        # Invalid session in param, clean up
        _clear_session_param()

    # Step 3: Not authenticated - show login form
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
                _set_session_param(session_id)
                st.success(f"Welcome back, {user.username}!")
                st.rerun()
            else:
                st.error("Invalid username or password")
                st.session_state["login_username"] = username

        st.caption(
            "💡 First run? Check console for generated admin password. "
            "Or set ADMIN_INITIAL_PASSWORD in .env before first startup."
        )
