"""Tests for the prometheus tools (service discovery / health snapshot / metric query).

The three tools are each designed so a single call covers one full query scenario,
avoiding multiple tool round trips that exhaust `max_tool_rounds`.
"""
import json
import unittest
from unittest.mock import patch

from hermes.tools.prometheus.tools import (
    _DIMENSION_QUERIES,
    _METRIC_QUERY_SCHEMA,
    _SERVICE_DISCOVERY_SCHEMA,
    _SERVICE_HEALTH_SCHEMA,
    _metric_query_handler,
    _service_discovery_handler,
    _service_health_handler,
)


def _success(rows):
    return {"status": "success", "data": {"result": rows}}


def _row(service, value="1", extra_metric=None):
    metric = {"service": service}
    if extra_metric:
        metric.update(extra_metric)
    return {"metric": metric, "value": [1710000000, value]}


class DiscoverySchemaTests(unittest.TestCase):
    def test_schema_requires_service_prefix(self):
        props = _SERVICE_DISCOVERY_SCHEMA["parameters"]
        self.assertIn("service_prefix", props["properties"])
        self.assertIn("service_prefix", props["required"])


class DiscoveryHandlerTests(unittest.TestCase):
    def _mock_call(self, side_effects):
        return patch(
            "hermes.tools.prometheus.tools._call_prometheus",
            side_effect=side_effects,
        )

    def test_missing_prefix_returns_error(self):
        out = _service_discovery_handler({})
        self.assertIn("error", json.loads(out))

    def test_discovers_services_from_db_metric(self):
        with self._mock_call([_success([_row("nfc-finance"), _row("nfc-fund")])]) as mock_call:
            out = _service_discovery_handler({"service_prefix": "nfc-.*"})
        payload = json.loads(out)
        self.assertEqual(payload["result_type"], "service_discovery")
        self.assertEqual(payload["services"], ["nfc-finance", "nfc-fund"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["matched_metric"], "arms_db_requests_seconds_ign_rpc")
        # 主查询命中则只调用一次
        self.assertEqual(mock_call.call_count, 1)

    def test_falls_back_to_cpu_metric_when_db_empty(self):
        with self._mock_call([_success([]), _success([_row("nfc-gateway")])]):
            out = _service_discovery_handler({"service_prefix": "nfc-.*"})
        payload = json.loads(out)
        self.assertEqual(payload["matched_metric"], "arms_system_cpu_idle")
        self.assertEqual(payload["services"], ["nfc-gateway"])

    def test_empty_services_not_error(self):
        with self._mock_call([_success([]), _success([])]):
            out = _service_discovery_handler({"service_prefix": "nfc-.*"})
        payload = json.loads(out)
        self.assertNotIn("error", payload)
        self.assertEqual(payload["services"], [])
        self.assertIn("hint", payload)


class HealthSchemaTests(unittest.TestCase):
    def test_schema_requires_service(self):
        props = _SERVICE_HEALTH_SCHEMA["parameters"]
        self.assertIn("service", props["properties"])
        self.assertIn("service", props["required"])

    def test_schema_has_five_dimension_enum(self):
        props = _SERVICE_HEALTH_SCHEMA["parameters"]["properties"]
        self.assertIn("dimensions", props)
        self.assertEqual(
            set(props["dimensions"]["items"]["enum"]),
            {"request", "database", "sql", "jvm", "system"},
        )

    def test_queries_cover_all_whitelist_dimensions(self):
        # request(5) + database(4) + sql(5) + jvm(4) + system(4) = 22
        total = sum(len(v) for v in _DIMENSION_QUERIES.values())
        self.assertEqual(total, 22)
        self.assertEqual(
            set(_DIMENSION_QUERIES.keys()),
            {"request", "database", "sql", "jvm", "system"},
        )
        # 覆盖 skill 白名单关键指标
        jvm_names = {n for n, _ in _DIMENSION_QUERIES["jvm"]}
        self.assertIn("young_gc_rate", jvm_names)
        self.assertIn("old_gc_rate", jvm_names)
        self.assertIn("young_gc_seconds", jvm_names)
        self.assertIn("old_gc_seconds", jvm_names)
        sql_names = {n for n, _ in _DIMENSION_QUERIES["sql"]}
        self.assertIn("exception_count", sql_names)


class HealthHandlerTests(unittest.TestCase):
    def _mock_call(self, side_effects):
        return patch(
            "hermes.tools.prometheus.tools._call_prometheus",
            side_effect=side_effects,
        )

    def test_missing_service_returns_error(self):
        out = _service_health_handler({})
        self.assertIn("error", json.loads(out))

    def test_full_snapshot_calls_all_dimensions(self):
        empty = _success([])
        # 全维度共 22 条查询
        with self._mock_call([empty] * 22) as mock_call:
            out = _service_health_handler({"service": "nfc-.*"})
        payload = json.loads(out)
        self.assertEqual(payload["result_type"], "service_health")
        self.assertEqual(payload["service"], "nfc-.*")
        self.assertEqual(set(payload["dimensions"].keys()), {"request", "database", "sql", "jvm", "system"})
        self.assertEqual(mock_call.call_count, 22)

    def test_subset_dimensions_only_queries_those(self):
        empty = _success([])
        # jvm 4 条
        with self._mock_call([empty] * 4) as mock_call:
            out = _service_health_handler({"service": "nfc-.*", "dimensions": ["jvm"]})
        payload = json.loads(out)
        self.assertEqual(set(payload["dimensions"].keys()), {"jvm"})
        self.assertEqual(mock_call.call_count, 4)

    def test_empty_results_are_ok_not_error(self):
        empty = _success([])
        with self._mock_call([empty] * 22):
            out = _service_health_handler({"service": "nfc-.*"})
        payload = json.loads(out)
        self.assertNotIn("error", payload)
        self.assertIn("hint", payload)


class MetricQuerySchemaTests(unittest.TestCase):
    def test_schema_requires_query(self):
        props = _METRIC_QUERY_SCHEMA["parameters"]
        self.assertIn("query", props["properties"])
        self.assertIn("query", props["required"])


class MetricQueryHandlerTests(unittest.TestCase):
    def _mock_call(self, side_effects):
        return patch(
            "hermes.tools.prometheus.tools._call_prometheus",
            side_effect=side_effects,
        )

    def test_missing_query_returns_error(self):
        out = _metric_query_handler({})
        self.assertIn("error", json.loads(out))

    def test_range_query_single_call(self):
        success = {
            "status": "success",
            "data": {"result": [
                {"metric": {"service": "nfc-finance"}, "values": [[1710000000, "0.5"]]}
            ]},
        }
        with self._mock_call([success]) as mock_call:
            out = _metric_query_handler({"query": 'avg(up{service=~"nfc-.*"}) by(service)'})
        payload = json.loads(out)
        self.assertEqual(payload["result_type"], "promql_query")
        self.assertEqual(mock_call.call_count, 1)
        self.assertIn("results", payload)
        # 使用范围查询端点
        args = mock_call.call_args[0]
        self.assertEqual(args[0], "api/v1/query_range")

    def test_query_error(self):
        with self._mock_call(RuntimeError("boom")):
            out = _metric_query_handler({"query": "up"})
        self.assertIn("error", json.loads(out))


if __name__ == "__main__":
    unittest.main()
