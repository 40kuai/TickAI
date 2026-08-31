"""Tests for the Jenkins read-only tools (service job matching + build records).

The two tools are strictly read-only — they only issue Jenkins GET API
requests (never POST/PUT/DELETE). See the design spec:
docs/superpowers/specs/2026-08-27-jenkins-tools-design.md
"""
import json
import unittest
from unittest.mock import patch

from hermes.tools.jenkins.tools import (
    _BUILD_RECORDS_SCHEMA,
    _SERVICE_JOBS_SCHEMA,
    _build_records_handler,
    _call_jenkins,
    _color_to_status,
    _find_matching_jobs,
    _service_jobs_handler,
)


def _jobs_payload(names, colors=None):
    colors = colors or ["blue"] * len(names)
    return {
        "jobs": [
            {"name": n, "color": c}
            for n, c in zip(names, colors)
        ]
    }


def _job_detail_payload(builds):
    return {"name": "nfc-be-camunda-company-k8s-prod", "builds": builds}


def _build(number, result="SUCCESS", timestamp=1787801640577, duration=30184):
    return {
        "number": number,
        "result": result,
        "timestamp": timestamp,
        "duration": duration,
        "estimatedDuration": 33569,
    }


def _build_detail(build, sha="5928dd8341d3d732dfd537b453aed2720ae7e967", branch="origin/main"):
    return {
        "number": build["number"],
        "result": build["result"],
        "timestamp": build["timestamp"],
        "duration": build["duration"],
        "actions": [
            {
                "lastBuiltRevision": {
                    "SHA1": sha,
                    "branch": [{"name": branch}],
                }
            }
        ],
    }


class SchemaTests(unittest.TestCase):
    def test_service_jobs_schema_requires_service(self):
        props = _SERVICE_JOBS_SCHEMA["parameters"]
        self.assertIn("service", props["properties"])
        self.assertIn("service", props["required"])

    def test_build_records_schema_requires_job_name(self):
        props = _BUILD_RECORDS_SCHEMA["parameters"]
        self.assertIn("job_name", props["properties"])
        self.assertIn("job_name", props["required"])
        # limit 有默认值 10
        self.assertEqual(props["properties"]["limit"].get("default"), 10)


class ColorStatusTests(unittest.TestCase):
    def test_blue_success(self):
        self.assertEqual(_color_to_status("blue"), "SUCCESS")

    def test_red_failure(self):
        self.assertEqual(_color_to_status("red"), "FAILURE")

    def test_aborted(self):
        self.assertEqual(_color_to_status("aborted"), "ABORTED")

    def test_notbuilt(self):
        self.assertEqual(_color_to_status("notbuilt"), "NOT_BUILT")

    def test_unknown_passthrough(self):
        self.assertEqual(_color_to_status("purple_anime"), "purple_anime")


class MatchingTests(unittest.TestCase):
    def test_case_insensitive_substring(self):
        names = ["nfc-be-camunda-company-k8s-prod", "habitat-it-i-server-web-fe-prod"]
        got = _find_matching_jobs(names, "NFC-camunda")
        self.assertEqual(got, ["nfc-be-camunda-company-k8s-prod"])

    def test_matches_multiple_environments(self):
        names = [
            "nfc-be-camunda-company-k8s-dev",
            "nfc-be-camunda-company-k8s-prod",
            "nfc-be-camunda-company-hsk8s-test",
            "nfc-fe-camunda-k8s-prod",
        ]
        got = _find_matching_jobs(names, "camunda")
        self.assertEqual(len(got), 4)

    def test_empty_keyword_matches_nothing(self):
        got = _find_matching_jobs(["a", "b"], "")
        self.assertEqual(got, [])


class ServiceJobsHandlerTests(unittest.TestCase):
    def test_missing_service_returns_error(self):
        out = _service_jobs_handler({})
        self.assertIn("error", json.loads(out))

    def test_returns_matched_jobs(self):
        with patch(
            "hermes.tools.jenkins.tools._call_jenkins",
            return_value=_jobs_payload(
                ["nfc-be-camunda-company-k8s-prod", "nfc-fe-camunda-k8s-prod"]
            ),
        ):
            out = _service_jobs_handler({"service": "camunda"})
        payload = json.loads(out)
        self.assertEqual(payload["result_type"], "service_jobs")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["jobs"][0]["name"], "nfc-be-camunda-company-k8s-prod")
        self.assertEqual(payload["jobs"][0]["status"], "SUCCESS")
        self.assertIn("url", payload["jobs"][0])

    def test_truncates_when_too_many(self):
        many = [f"nfc-be-{i}-k8s-prod" for i in range(40)]
        with patch(
            "hermes.tools.jenkins.tools._call_jenkins",
            return_value=_jobs_payload(many),
        ):
            out = _service_jobs_handler({"service": "nfc"})
        payload = json.loads(out)
        self.assertEqual(len(payload["jobs"]), 30)
        self.assertEqual(payload["total"], 40)
        self.assertTrue(payload["truncated"])
        self.assertIn("hint", payload)

    def test_no_match_returns_empty_with_hint(self):
        with patch(
            "hermes.tools.jenkins.tools._call_jenkins",
            return_value=_jobs_payload(["nfc-be-camunda-k8s-prod"]),
        ):
            out = _service_jobs_handler({"service": "nonexistent-service"})
        payload = json.loads(out)
        self.assertNotIn("error", payload)
        self.assertEqual(payload["jobs"], [])
        self.assertIn("hint", payload)


class BuildRecordsHandlerTests(unittest.TestCase):
    def _mock(self, job_detail, build_details):
        def side_effect(endpoint):
            path = endpoint.split("?")[0].rstrip("/")  # 去掉 query
            segments = path.split("/")
            # build detail: job/<name>/<n>/api/json → 倒数第 3 段是数字构建号
            # job detail:  job/<name>/api/json → 倒数第 3 段是 job 名(非数字)
            if len(segments) >= 5 and segments[-2] == "api" and segments[-3].isdigit():
                num = int(segments[-3])
                if num in build_details:
                    return build_details[num]
                return _build_detail({"number": num})
            return job_detail

        return patch("hermes.tools.jenkins.tools._call_jenkins", side_effect=side_effect)

    def test_missing_job_name_returns_error(self):
        out = _build_records_handler({})
        self.assertIn("error", json.loads(out))

    def test_returns_build_records_with_git_info(self):
        builds = [_build(71), _build(70)]
        detail = _job_detail_payload(builds)
        with self._mock(detail, {71: _build_detail(builds[0]), 70: _build_detail(builds[1], sha="abc123")}) as m:
            out = _build_records_handler({"job_name": "nfc-be-camunda-company-k8s-prod"})
        payload = json.loads(out)
        self.assertEqual(payload["result_type"], "build_records")
        self.assertEqual(payload["job_name"], "nfc-be-camunda-company-k8s-prod")
        self.assertEqual(len(payload["builds"]), 2)
        b = payload["builds"][0]
        self.assertEqual(b["number"], 71)
        self.assertEqual(b["result"], "SUCCESS")
        self.assertIn("start_time", b)
        self.assertEqual(b["commit_sha"], "5928dd8341d3d732dfd537b453aed2720ae7e967")
        self.assertEqual(b["branch"], "origin/main")
        # 1 job detail + 2 build details = 3 calls
        self.assertEqual(m.call_count, 3)

    def test_default_limit_10_max_50(self):
        builds = [_build(i) for i in range(60, 0, -1)]
        with patch(
            "hermes.tools.jenkins.tools._call_jenkins",
            return_value=_job_detail_payload(builds),
        ):
            out = _build_records_handler({"job_name": "x", "limit": 50})
        payload = json.loads(out)
        self.assertEqual(len(payload["builds"]), 50)

    def test_job_not_found_returns_error(self):
        with patch(
            "hermes.tools.jenkins.tools._call_jenkins",
            side_effect=RuntimeError("HTTP 404: job not found"),
        ):
            out = _build_records_handler({"job_name": "no-such-job"})
        self.assertIn("error", json.loads(out))


class ReadOnlyGuaranteeTests(unittest.TestCase):
    def test_jenkins_uses_get_only(self):
        """Verify _call_jenkins only ever issues GET requests (no POST/PUT/DELETE)."""
        with patch(
            "hermes.tools.jenkins.tools.urlopen"
        ) as mock_urlopen, patch(
            "hermes.tools.jenkins.tools._get_config",
            return_value=("https://jenkins.example.com", "u", "p"),
        ):
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = b'{"jobs": []}'
            _call_jenkins("api/json?tree=jobs[name]")
            args, kwargs = mock_urlopen.call_args
            req = args[0]
            self.assertEqual(req.get_method(), "GET")


if __name__ == "__main__":
    unittest.main()
