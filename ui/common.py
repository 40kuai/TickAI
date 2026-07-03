"""Common utilities for UI pages."""
import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hermes.data import db  # noqa: E402
from hermes.i18n.strings import render_language_selector, t  # noqa: E402
from hermes.auth import require_login, logout  # noqa: E402


def init_page(title: str, icon: str = "🛠️", layout: str = "wide") -> None:
    """Initialize a page with standard config and login protection."""
    st.set_page_config(page_title=f"{title} · TickAI", page_icon=icon, layout=layout)
    db.init_db()

    # Require login on ALL pages
    require_login()

    render_language_selector()


def sidebar_status() -> None:
    """Render sidebar status info and logout button."""
    from hermes import config

    with st.sidebar:
        st.title("🎫 TickAI")
        st.caption("AI-Powered Ops Ticket Platform")
        st.divider()

        # Show logged in user
        if st.session_state.get("authenticated"):
            st.success(f"👤 {st.session_state.get('user', {}).get('username', '?')}")
            if st.button("🚪 Logout", use_container_width=True):
                logout()
            st.divider()

        if config.LLM_API_KEY():
            st.success(f"LLM Ready: `{config.LLM_MODEL()}`")
        else:
            st.warning("LLM not configured")


def handle_error(exc: Exception, msg_key: str) -> None:
    st.error(f"{t(msg_key)} {exc}")


def confirm_action(message: str, key: str) -> bool:
    if st.session_state.get(f"confirm_{key}"):
        st.warning(message)
        c1, c2 = st.columns([1, 4])
        if c1.button("✅", key=f"yes_{key}"):
            st.session_state.pop(f"confirm_{key}")
            return True
        if c1.button("❌", key=f"no_{key}"):
            st.session_state.pop(f"confirm_{key}")
            st.rerun()
        return False
    return None
