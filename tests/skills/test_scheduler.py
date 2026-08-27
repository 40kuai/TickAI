"""Tests for hermes.skills.scheduler — stdlib-based job scheduler."""
import time
import unittest
from unittest.mock import MagicMock

from hermes.skills.scheduler import (
    Harness,
    HarnessJob,
    JobAlreadyExistsError,
    JobNotFoundError,
)


class HarnessRegistrationTests(unittest.TestCase):
    def test_register_job(self):
        h = Harness()
        h.register("test", lambda: None, interval_seconds=60)
        jobs = h.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["name"], "test")
        self.assertEqual(jobs[0]["interval_seconds"], 60)
        self.assertEqual(jobs[0]["enabled"], True)

    def test_register_duplicate_raises(self):
        h = Harness()
        h.register("test", lambda: None, interval_seconds=60)
        with self.assertRaises(JobAlreadyExistsError):
            h.register("test", lambda: None, interval_seconds=60)

    def test_unregister_job(self):
        h = Harness()
        h.register("test", lambda: None, interval_seconds=60)
        h.unregister("test")
        self.assertEqual(len(h.list_jobs()), 0)

    def test_unregister_nonexistent_raises(self):
        h = Harness()
        with self.assertRaises(JobNotFoundError):
            h.unregister("nonexistent")

    def test_register_replaces_when_replace_true(self):
        h = Harness()
        h.register("test", lambda: 1, interval_seconds=60)
        h.register("test", lambda: 2, interval_seconds=120, replace=True)
        jobs = h.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["interval_seconds"], 120)


class HarnessExecutionTests(unittest.TestCase):
    def test_run_once_invokes_function(self):
        h = Harness()
        mock_fn = MagicMock()
        h.register("test", mock_fn, interval_seconds=60)
        h.run_once("test")
        mock_fn.assert_called_once()

    def test_run_once_records_last_run(self):
        h = Harness()
        h.register("test", lambda: None, interval_seconds=60)
        before = time.time()
        h.run_once("test")
        jobs = h.list_jobs()
        self.assertIsNotNone(jobs[0]["last_run_at"])
        self.assertGreaterEqual(jobs[0]["last_run_at"], before)

    def test_run_once_unknown_job_raises(self):
        h = Harness()
        with self.assertRaises(JobNotFoundError):
            h.run_once("nonexistent")

    def test_run_pending_runs_due_jobs(self):
        h = Harness()
        mock_fn = MagicMock()
        # interval 0 = always due
        h.register("test", mock_fn, interval_seconds=0)
        h._run_pending()
        mock_fn.assert_called_once()

    def test_run_pending_skips_not_due_jobs(self):
        h = Harness()
        mock_fn = MagicMock()
        # Manually register a job with future last_run
        h.register("test", mock_fn, interval_seconds=3600)
        # Pretend it ran recently
        h._jobs["test"].last_run_at = time.time()
        h._run_pending()
        mock_fn.assert_not_called()

    def test_run_pending_handles_job_exception(self):
        h = Harness()
        failing_fn = MagicMock(side_effect=RuntimeError("boom"))
        h.register("failing", failing_fn, interval_seconds=0)
        # Should not raise
        h._run_pending()
        failing_fn.assert_called_once()
        # But error is recorded
        jobs = h.list_jobs()
        self.assertIn("error", jobs[0])
        self.assertIn("boom", jobs[0]["error"])

    def test_enable_disable(self):
        h = Harness()
        h.register("test", lambda: None, interval_seconds=60)
        h.disable("test")
        self.assertFalse(h.list_jobs()[0]["enabled"])
        h.enable("test")
        self.assertTrue(h.list_jobs()[0]["enabled"])


class HarnessJobDataclassTests(unittest.TestCase):
    def test_job_serialization(self):
        from dataclasses import asdict
        j = HarnessJob(
            name="foo",
            func=lambda: None,
            interval_seconds=60,
            enabled=True,
        )
        d = asdict(j)
        # func not JSON-serializable, but we just need other fields
        self.assertEqual(d["name"], "foo")
        self.assertEqual(d["interval_seconds"], 60)
        self.assertTrue(d["enabled"])


if __name__ == "__main__":
    unittest.main()
