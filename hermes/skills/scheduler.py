"""Harness scheduler — stdlib-based periodic job runner.

We use threading.Timer + a background thread for the actual loop. The
implementation has zero external dependencies (no APScheduler, no cron, etc).

Jobs are run in a separate thread, so they MUST be thread-safe.

Usage:
    harness = Harness()
    harness.register("detect_oom", run_detect_oom_skill, interval_seconds=3600)
    harness.start()   # starts the background tick thread
    # ...
    harness.stop()    # stops cleanly
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class JobAlreadyExistsError(ValueError):
    pass


class JobNotFoundError(KeyError):
    pass


@dataclass
class HarnessJob:
    name: str
    func: Callable[[], None]
    interval_seconds: int
    enabled: bool = True
    last_run_at: Optional[float] = None
    last_error: Optional[str] = None
    run_count: int = 0
    error_count: int = 0
    description: str = ""

    def is_due(self, now: float) -> bool:
        if not self.enabled:
            return False
        if self.last_run_at is None:
            return True
        return (now - self.last_run_at) >= self.interval_seconds


class Harness:
    """In-process job scheduler. Thread-safe.

    Each registered job runs at a fixed interval. The background tick thread
    checks all jobs every second and runs any that are due.
    """

    TICK_INTERVAL = 1.0  # seconds between checks

    def __init__(self):
        self._jobs: Dict[str, HarnessJob] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ============================================================
    # Registration
    # ============================================================

    def register(
        self,
        name: str,
        func: Callable[[], None],
        interval_seconds: int,
        enabled: bool = True,
        description: str = "",
        replace: bool = False,
    ) -> None:
        with self._lock:
            if name in self._jobs and not replace:
                raise JobAlreadyExistsError(f"job already registered: {name!r}")
            self._jobs[name] = HarnessJob(
                name=name,
                func=func,
                interval_seconds=interval_seconds,
                enabled=enabled,
                description=description,
            )
            logger.info("registered job %r (interval=%ds)", name, interval_seconds)

    def unregister(self, name: str) -> None:
        with self._lock:
            if name not in self._jobs:
                raise JobNotFoundError(name)
            del self._jobs[name]

    def enable(self, name: str) -> None:
        with self._lock:
            if name not in self._jobs:
                raise JobNotFoundError(name)
            self._jobs[name].enabled = True

    def disable(self, name: str) -> None:
        with self._lock:
            if name not in self._jobs:
                raise JobNotFoundError(name)
            self._jobs[name].enabled = False

    def list_jobs(self) -> List[dict]:
        with self._lock:
            out = []
            for j in self._jobs.values():
                out.append({
                    "name": j.name,
                    "interval_seconds": j.interval_seconds,
                    "enabled": j.enabled,
                    "last_run_at": j.last_run_at,
                    "last_error": j.last_error,
                    "run_count": j.run_count,
                    "error_count": j.error_count,
                    "description": j.description,
                    "error": j.last_error,  # also accessible as 'error' for UI
                })
            return out

    # ============================================================
    # Execution
    # ============================================================

    def run_once(self, name: str) -> None:
        with self._lock:
            if name not in self._jobs:
                raise JobNotFoundError(name)
            job = self._jobs[name]
        # Run outside the lock — the job may take a while
        self._invoke(job)

    def _invoke(self, job: HarnessJob) -> None:
        try:
            job.func()
            with self._lock:
                job.last_run_at = time.time()
                job.last_error = None
                job.run_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %r failed", job.name)
            with self._lock:
                job.last_run_at = time.time()
                job.last_error = f"{type(exc).__name__}: {exc}"
                job.error_count += 1

    def _run_pending(self) -> None:
        """Run all due jobs. Called by the tick thread every TICK_INTERVAL."""
        now = time.time()
        with self._lock:
            due = [j for j in self._jobs.values() if j.is_due(now)]
        for j in due:
            self._invoke(j)

    # ============================================================
    # Background loop
    # ============================================================

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="harness-tick", daemon=True)
        self._thread.start()
        logger.info("harness scheduler started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("harness scheduler stopped")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_pending()
            except Exception:  # noqa: BLE001
                logger.exception("error in tick loop")
            self._stop_event.wait(self.TICK_INTERVAL)
