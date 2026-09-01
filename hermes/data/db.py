"""Database engine, session factory, and init."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config.settings import DB_PATH
from .models import Base


def _make_engine() -> Engine:
    """Build the engine. Ensures parent dir exists and DB file is chmod 600."""
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        os.chmod(db_path, 0o600)
    return create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )


engine: Engine = _make_engine()
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False
)


def init_db(use_alembic: bool = False) -> None:
    """Initialize database.

    Args:
        use_alembic: If True, run Alembic migrations (requires alembic installed).
                     If False (default), use create_all (quick for dev/CI).
    """
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if use_alembic:
        # Run Alembic migrations (optional, requires `pip install alembic`)
        try:
            from alembic.config import Config
            from alembic import command

            alembic_cfg = Config("alembic.ini")
            alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{DB_PATH}")
            command.upgrade(alembic_cfg, "head")
        except ImportError:
            # Fallback to create_all if alembic not installed
            Base.metadata.create_all(engine)
    else:
        # Direct create_all for dev/CI (faster, no versioning)
        Base.metadata.create_all(engine)

    # Lock down permissions
    if db_path.exists():
        os.chmod(db_path, 0o600)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional context manager. Commits on success, rolls back on error.

    `expire_on_commit=False` means returned objects stay readable after the
    session closes — callers can safely use them outside the with-block.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Session:
    """For code paths that manage their own commit (e.g. per-request)."""
    return SessionLocal()
