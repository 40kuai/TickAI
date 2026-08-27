"""Tests for the aggregated prometheus_service_health tool.

The tool issues several PromQL queries in ONE call (rather than making the
LLM call query tools many times), returning a structured health snapshot.
"""
import json
import unittest
from unittest.mock import patch

from hermes.tools.prometheus.tools import (
    _service_health_handler,
    _SERVICE_HEALTH_SCHEMA,
)


class ServiceHealthSchemaTests(unittest.TestCase):
    def test_schema_has_service_and_query(self):
        props = _SERVICE_HEALTH_SCHEMA["parameters"]["properties"]
        self.assertIn("service", props)
        self.assertIn("query", props)

    def test_schema_has_dimension_enum(self):
        props = _SERVICE_HEALTH_SCHEMA["parameters"]["properties"]
        self.assertIn("dimensions", props)
        self.assertEqual(
            set(props["dimensions"]["items"]["enum"]),
            {"request", "database", "jvm", "system"},
        )

    def test_schema_has_default_time_range(self):
        props = _SERVICE_HEALTH_SCHEMA["parameters"]["properties"]
        self.assertIn("start", props)
        self.assertIn("end", props)


class ServiceHealthHandlerTests(unittest.TestCase):
    def _mock_call(self, side_effects):
        return patch(
            "hermes.tools.prometheus.tools._call_prometheus",
            side_effect=side_effects,
        )

    def test_missing_both_params_returns_error(self):
        out = _service_health_handler({})
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_query_mode_single_call(self):
        """传入 query 时只执行一次 PromQL 查询（不跑维度）。"""
        success = {
            "status": "success",
            "data": {"result": [
                {"metric": {"service": "nfc-finance"}, "value": [1710000000, "0.5"]}
            ]},
        }
        with self._mock_call([success]) as mock_call:
            out = _service_health_handler({"query": 'avg(up{service=~"nfc-.*"}) by(service)'})
        payload = json.loads(out)
        self.assertEqual(payload["result_type"], "promql_query")
        self.assertEqual(mock_call.call_count, 1)
        self.assertIn("results", payload)

    def test_query_mode_error(self):
        with self._mock_call(RuntimeError("boom")):
            out = _service_health_handler({"query": "up"})
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_returns_aggregated_result(self):
        success = {
            "status": "success",
            "data": {"result": [
                {"metric": {"service": "nfc-finance"}, "value": [1710000000, "1"]}
            ]},
        }
        # request(3) + database(3) + jvm(1) + system(4) = 11
        with self._mock_call([success] * 11):
            out = _service_health_handler({"service": "nfc-.*"})
        payload = json.loads(out)
        self.assertIn("service", payload)
        self.assertIn("dimensions", payload)
        self.assertEqual(payload["service"], "nfc-.*")
        self.assertIsInstance(payload["dimensions"], dict)

    def test_calls_prometheus_multiple_times(self):
        success = {"status": "success", "data": {"result": []}}
        with self._mock_call([success] * 11) as mock_call:
            _service_health_handler({"service": "nfc-.*"})
        # 全维度共 11 条查询（request3 + database3 + jvm1 + system4）
        self.assertEqual(mock_call.call_count, 11)

    def test_empty_results_are_ok_not_error(self):
        """查询全部返回空时，工具仍返回结构化结果（空维度），不报错。"""
        empty = {"status": "success", "data": {"result": []}}
        with self._mock_call([empty] * 11):
            out = _service_health_handler({"service": "nfc-.*"})
        payload = json.loads(out)
        self.assertNotIn("error", payload)
        self.assertIn("dimensions", payload)
        self.assertIn("hint", payload)


if __name__ == "__main__":
    unittest.main()
