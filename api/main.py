"""FastAPI application entry point.

Replaces the Streamlit UI server. Serves the JSON API consumed by the
Vue frontend and, in production, serves the built Vue static assets.

Run with:
    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from hermes.auth import init_default_user
from hermes.data.db import init_db

from .auth_routes import router as auth_router
from .chat_routes import router as chat_router
from .history_routes import router as history_router
from .server_routes import router as server_router
from .ssh_credential_routes import router as ssh_cred_router
from .tool_routes import router as tool_router

app = FastAPI(title="TickAI API", version="1.0.0")

# ---------------------------------------------------------------------------
# CORS - allow the Vite dev server (http://localhost:5173)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(auth_router)
app.include_router(server_router)
app.include_router(ssh_cred_router)
app.include_router(chat_router)
app.include_router(tool_router)
app.include_router(history_router)


# ---------------------------------------------------------------------------
# Startup - initialize database and default admin user
# ---------------------------------------------------------------------------
@app.on_event("startup")
def startup() -> None:
    init_db()
    init_default_user()


# ---------------------------------------------------------------------------
# Production static file serving (Vue build output)
# ---------------------------------------------------------------------------
# If a "static" directory exists at the project root, mount it at "/" so the
# built Vue SPA is served by FastAPI in production. API routes (prefixed with
# /api/) are registered above and take priority over the static mount.
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
