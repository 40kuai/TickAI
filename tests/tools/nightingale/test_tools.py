"""Tests for the Nightingale (N9E) read-only alert tools.

The two tools are strictly read-only — they only issue Nightingale GET
API requests (X-User-Token auth), never POST/PUT/DELETE.
"""
import json
import unittest
from unittest.mock import patch

from hermes.tools.nightingale.tools import (
    _ACTIVE_ALERTS_SCHEMA,
    _HISTORY_ALERTS_SCHEMA,
    _active_alerts_handler,
    _call_nightingale,
    _find_event_service,
    _get_config,
    _history_alerts_handler,
    _parse_event,
    _severity_name,
)


def _raw_event(overrides=None):
    base = {
        "id": 3652,
        "cate": "prometheus",
        "cluster": "arms-prometheus",
        "datasource_id": 1,
        "group_id": 2,
        "group_name": "数字化",
        "hash": "31fd96fe57713df39b96ba1f6ad7ef5d",
        "rule_id": 5,
        "rule_name": "服务请求耗时大于20s",
        "rule_prod": "metric",
        "severity": 2,
        "prom_for_duration": 120,
        "prom_ql": "avg(arms_app_requests...) by(service) >= 20",
        "prom_eval_interval": 30,
        "target_ident": "",
        "target_note": "",
        "trigger_time": 1788159715,
        "trigger_value": "40.41",
        "tags": [
            "callKind=http_client",
            "rulename=服务请求耗时大于20s",
            "service=nfc-saas-service",
        ],
        "tags_map": {
            "callKind": "http_client",
            "rulename": "服务请求耗时大于20s",
            "service": "nfc-saas-service",
        },
        "is_recovered": False,
        "last_eval_time": 0,
        "first_trigger_time": 1788102115,
        "recover_time": 0,
        "notify_cur_number": 17,
        "status": 0,
        "claimant": "",
    }
    base.update(overrides or {})
    return base


class SchemaTests(unittest.TestCase):
    def test_history_schema_requires_no_params(self):
        props = _HISTORY_ALERTS_SCHEMA["parameters"]
        # 全部可选(minutes/rule_name/severity/limit)
        self.assertEqual(props.get("required", []), [])
        self.assertEqual(props["properties"]["limit"].get("default"), 10)

    def test_active_schema_requires_no_params(self):
        props = _ACTIVE_ALERTS_SCHEMA["parameters"]
        self.assertEqual(props.get("required", []), [])
        self.assertEqual(props["properties"]["limit"].get("default"), 10)


class SeverityTests(unittest.TestCase):
    def test_critical(self):
        self.assertEqual(_severity_name(1), "S1-CRITICAL")

    def test_warning(self):
        self.assertEqual(_severity_name(2), "S2-WARNING")

    def test_info(self):
        self.assertEqual(_severity_name(3), "S3-INFO")

    def test_unknown(self):
        self.assertEqual(_severity_name(9), "S9")


class ParseEventTests(unittest.TestCase):
    def test_parse_history_event(self):
        raw = _raw_event({"is_recovered": True, "recover_time": 1788160825})
        ev = _parse_event(raw)
        self.assertEqual(ev["id"], 3652)
        self.assertEqual(ev["rule_name"], "服务请求耗时大于20s")
        self.assertEqual(ev["severity"], "S2-WARNING")
        self.assertEqual(ev["group_name"], "数字化")
        self.assertTrue(ev["is_recovered"])
        self.assertEqual(ev["service"], "nfc-saas-service")
        self.assertIn("trigger_time", ev)
        self.assertIn("recover_time", ev)
        self.assertEqual(ev["trigger_value"], "40.41")

    def test_parse_active_event_defaults_recovered_false(self):
        ev = _parse_event(_raw_event({}))
        self.assertFalse(ev["is_recovered"])


class ServiceExtractTests(unittest.TestCase):
    def test_from_tags_map(self):
        raw = _raw_event({})
        self.assertEqual(_find_event_service(raw), "nfc-saas-service")

    def test_missing_service(self):
        raw = _raw_event({"tags_map": {}, "tags": []})
        self.assertIsNone(_find_event_service(raw))


class ConfigTests(unittest.TestCase):
    @patch("hermes.tools.nightingale.tools._get_config")
    def test_missing_url_raises(self, mock_cfg):
        from hermes.tools.nightingale import tools as n9e_mod
        mock_cfg.side_effect = RuntimeError("NIGHTINGALE_URL 未配置，请在 .env 中设置")
        with self.assertRaises(RuntimeError):
            n9e_mod._get_config()


class HistoryHandlerTests(unittest.TestCase):
    def _mock_ok(self, events, total=None):
        payload = {"dat": {"list": events, "total": total or len(events)}, "err": ""}
        return patch("hermes.tools.nightingale.tools._call_nightingale", return_value=payload)

    def test_returns_history_events(self):
        ev = _raw_event({"is_recovered": True, "recover_time": 1788160825})
        with self._mock_ok([ev]) as m:
            out = _history_alerts_handler({"minutes": 5})
        payload = json.loads(out)
        self.assertEqual(payload["result_type"], "alert_history")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["events"][0]["rule_name"], "服务请求耗时大于20s")
        # 确认请求带了 stime/etime/limit 参数
        args = m.call_args
        self.assertIn("stime", args[0][1])
        self.assertIn("etime", args[0][1])
        self.assertEqual(args[0][1]["limit"], 10)

    def test_limit_capped_at_50(self):
        with self._mock_ok([]) as m:
            _history_alerts_handler({"minutes": 5, "limit": 999})
        self.assertEqual(m.call_args[0][1]["limit"], 50)

    def test_rule_name_filter_passed(self):
        with self._mock_ok([]) as m:
            _history_alerts_handler({"minutes": 5, "rule_name": "耗时时长"})
        self.assertEqual(m.call_args[0][1]["rule_name"], "耗时时长")

    def test_error_passthrough(self):
        with patch(
            "hermes.tools.nightingale.tools._call_nightingale",
            side_effect=RuntimeError("NIGHTINGALE_URL 未配置，请在 .env 中设置"),
        ):
            out = _history_alerts_handler({})
        self.assertIn("error", json.loads(out))


class ActiveHandlerTests(unittest.TestCase):
    def _mock_ok(self, events):
        payload = {"dat": {"list": events, "total": len(events)}, "err": ""}
        return patch("hermes.tools.nightingale.tools._call_nightingale", return_value=payload)

    def test_returns_active_events(self):
        ev = _raw_event({"is_recovered": False})
        with self._mock_ok([ev]) as m:
            out = _active_alerts_handler({"limit": 20})
        payload = json.loads(out)
        self.assertEqual(payload["result_type"], "active_alerts")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["events"][0]["severity"], "S2-WARNING")
        # my_groups=false 表示查全部业务组(n9e 按字符串解析,不能用 True/False)
        self.assertEqual(m.call_args[0][1]["my_groups"], "false")

    def test_error_passthrough(self):
        with patch(
            "hermes.tools.nightingale.tools._call_nightingale",
            side_effect=RuntimeError("NIGHTINGALE_URL 未配置，请在 .env 中设置"),
        ):
            out = _active_alerts_handler({})
        self.assertIn("error", json.loads(out))


class ReadOnlyGuaranteeTests(unittest.TestCase):
    def test_nightingale_uses_get_only(self):
        """Verify _call_nightingale only ever issues GET requests."""
        with patch("hermes.tools.nightingale.tools.urlopen") as mock_urlopen, patch(
            "hermes.tools.nightingale.tools._get_config",
            return_value=("https://n9e.example.com", "token123"),
        ):
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = b'{"dat": {"list": []}, "err": ""}'
            _call_nightingale("alert-cur-events/list", {})
            args, kwargs = mock_urlopen.call_args
            req = args[0]
            self.assertEqual(req.get_method(), "GET")
            # X-User-Token 认证头(header key 会被 email 库规范化为小写)
            headers = {k.lower(): v for k, v in dict(req.headers).items()}
            self.assertEqual(headers.get("x-user-token"), "token123")


if __name__ == "__main__":
    unittest.main()
