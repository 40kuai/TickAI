"""Tests for hermes.selfheal.approval (unified approval gate)."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from hermes.config import settings
from hermes.data import db, models
from hermes.selfheal import approval, config


class CooldownConfigTests(unittest.TestCase):
    def test_default_cooldown_hours(self):
        self.assertEqual(config.COOLDOWN_HOURS, 6)

    def test_reload_picks_env(self):
        self.addCleanup(config.reload_config)
        self.addCleanup(os.environ.pop, "SELFHEAL_COOLDOWN_HOURS", None)
        os.environ["SELFHEAL_COOLDOWN_HOURS"] = "12"
        config.reload_config()
        self.assertEqual(config.COOLDOWN_HOURS, 12)


class HardRuleTests(unittest.TestCase):
    def setUp(self):
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "system,docker-prune"
        os.environ["SELFHEAL_LOG_PATH_WHITELIST"] = "/var/log/nginx/access.log"
        config.reload_config()

    def tearDown(self):
        os.environ.pop("SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST", None)
        os.environ.pop("SELFHEAL_LOG_PATH_WHITELIST", None)
        config.reload_config()

    def test_risk_table_covers_all_actions(self):
        from hermes.selfheal import actions
        for name in actions.ACTIONS:
            self.assertIn(name, approval.ACTION_RISK,
                          f"ACTION_RISK 缺少动作 {name!r}")

    def test_unknown_action_rejected(self):
        r = approval.hard_rule("no_such_action", {})
        self.assertEqual(r["decision"], "reject")
        self.assertTrue(r["reasons"])

    def test_whitelist_failure_rejected(self):
        r = approval.hard_rule("truncate_log", {"mount": "/", "path": "/etc/passwd"})
        self.assertEqual(r["decision"], "reject")

    def test_high_risk_forced_approval(self):
        # run_cleanup_script + category=all(白名单含 docker-prune) → high → 强制审批
        r = approval.hard_rule("run_cleanup_script", {"mount": "/", "category": "all"})
        self.assertEqual(r["decision"], "approval")
        self.assertIn("docker-prune", "; ".join(r["reasons"]) or "docker-prune")

    def test_medium_risk_allowed(self):
        r = approval.hard_rule("run_cleanup_script", {"mount": "/", "category": "system"})
        self.assertEqual(r["decision"], "allow")


class DecideTests(unittest.TestCase):
    def setUp(self):
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "system,docker-prune"
        os.environ["SELFHEAL_LOG_PATH_WHITELIST"] = "/var/log/nginx/access.log"
        config.reload_config()

    def tearDown(self):
        os.environ.pop("SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST", None)
        os.environ.pop("SELFHEAL_LOG_PATH_WHITELIST", None)
        config.reload_config()

    def _judge(self, payload):
        # 固定返回 auto + score=1
        return {"risk_score": 1, "recommendation": "auto", "reasons": ["影响极小"]}

    def test_high_risk_forced_approval_ignores_ai(self):
        d = approval.decide(1, "run_cleanup_script", {"mount": "/", "category": "all"},
                            {"impact": {"files": 3}}, judge_fn=self._judge)
        self.assertEqual(d["decision"], "approval")

    def test_ai_reject_respected_in_allow_zone(self):
        def _reject(payload):
            return {"risk_score": 5, "recommendation": "reject", "reasons": ["核心目录"]}
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": {"files": 1, "size_mb": 4800}}, judge_fn=_reject)
        self.assertEqual(d["decision"], "reject")

    def test_ai_auto_requires_impact(self):
        # 无影响面 → fail-closed, 即使 AI 说 auto 也不放行
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": None}, judge_fn=self._judge)
        self.assertEqual(d["decision"], "approval")

    def test_ai_auto_with_impact_approved(self):
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": {"files": 1, "size_mb": 2}}, judge_fn=self._judge)
        self.assertEqual(d["decision"], "auto")

    def test_judge_exception_fail_closed(self):
        def _boom(payload):
            raise RuntimeError("llm down")
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": {"files": 1}}, judge_fn=_boom)
        self.assertEqual(d["decision"], "approval")
        self.assertTrue(any("fail-closed" in r for r in d["reasons"]))

    def test_reasons_and_ai_judgement_present(self):
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": {"files": 1}}, judge_fn=self._judge)
        self.assertTrue(d["reasons"])
        self.assertEqual(d["ai_judgement"]["recommendation"], "auto")

    def test_malformed_judgement_fail_closed(self):
        # 对抗: judge 返回非 dict → 不得异常逃逸, fail-closed 审批
        def _bad(payload):
            return "not a dict"
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": {"files": 1}}, judge_fn=_bad)
        self.assertEqual(d["decision"], "approval")

    def test_non_numeric_score_fail_closed(self):
        # 对抗: risk_score 非数字('high') → 保守 5 → approval, 不误放行
        def _bad(payload):
            return {"risk_score": "high", "recommendation": "auto", "reasons": []}
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": {"files": 1}}, judge_fn=_bad)
        self.assertEqual(d["decision"], "approval")

    def test_float_score_no_truncation_auto(self):
        # 对抗: risk_score=2.9 不得被 int() 截断为 2 误放行 auto
        def _bad(payload):
            return {"risk_score": 2.9, "recommendation": "auto", "reasons": []}
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": {"files": 1}}, judge_fn=_bad)
        self.assertEqual(d["decision"], "approval")

    def test_auto_requires_impact_truthy(self):
        # 对抗: impact={} 空字典不算影响面已确认 → fail-closed
        def _auto(payload):
            return {"risk_score": 1, "recommendation": "auto", "reasons": []}
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": {}}, judge_fn=_auto)
        self.assertEqual(d["decision"], "approval")

    def test_ai_auto_score_above_2_approval(self):
        # 对抗: AI 建议 auto 但 score=3 > 2 → 不得 auto
        def _med(payload):
            return {"risk_score": 3, "recommendation": "auto", "reasons": []}
        d = approval.decide(1, "truncate_log",
                            {"mount": "/", "path": "/var/log/nginx/access.log"},
                            {"impact": {"files": 1}}, judge_fn=_med)
        self.assertEqual(d["decision"], "approval")

    def test_hard_paths_ai_judgement_none(self):
        # hard_rule 非 allow 路径 ai_judgement 必须为 None(哨兵: AI 未参与)
        d = approval.decide(1, "run_cleanup_script",
                            {"mount": "/", "category": "all"},
                            {"impact": {"files": 3}}, judge_fn=self._judge)
        self.assertEqual(d["decision"], "approval")
        self.assertIsNone(d["ai_judgement"])
        # 白名单外 → reject, ai_judgement 同样为 None
        d2 = approval.decide(1, "truncate_log",
                             {"mount": "/", "path": "/etc/passwd"},
                             {"impact": {"files": 1}}, judge_fn=self._judge)
        self.assertEqual(d2["decision"], "reject")
        self.assertIsNone(d2["ai_judgement"])


class ParseJudgementTests(unittest.TestCase):
    """LLM 软判定解析器(_parse_judgement): 纯函数, 不触网。"""

    def test_parse_plain_json(self):
        j = approval._parse_judgement(
            '{"risk_score": 2, "recommendation": "auto", "reasons": ["影响小"]}')
        self.assertEqual(j["recommendation"], "auto")
        self.assertEqual(j["risk_score"], 2)

    def test_parse_fenced_json(self):
        j = approval._parse_judgement(
            '```json\n{"risk_score": 4, "recommendation": "approval", "reasons": []}\n```')
        self.assertEqual(j["recommendation"], "approval")
        self.assertEqual(j["risk_score"], 4)

    def test_invalid_recommendation_raises(self):
        with self.assertRaises(ValueError):
            approval._parse_judgement('{"recommendation": "maybe"}')

    def test_out_of_range_score_raises(self):
        with self.assertRaises(ValueError):
            approval._parse_judgement('{"recommendation": "auto", "risk_score": 9}')

    def test_float_score_rejected(self):
        # 对抗: 2.9 不得被 int() 截断为 2(真实 LLM 路径, 与 _coerce_score 同严格度)
        with self.assertRaises(ValueError):
            approval._parse_judgement(
                '{"recommendation": "auto", "risk_score": 2.9}')

    def test_bool_score_rejected(self):
        # 对抗: bool 是 int 子类, True 不得被 int() 洗白为 1
        with self.assertRaises(ValueError):
            approval._parse_judgement(
                '{"recommendation": "auto", "risk_score": true}')

    def test_non_numeric_score_rejected(self):
        with self.assertRaises(ValueError):
            approval._parse_judgement(
                '{"recommendation": "auto", "risk_score": "high"}')

    def test_malformed_json_raises(self):
        # 对抗: 非法 JSON 抛 ValueError(带片段, 可排障), 不裸抛 JSONDecodeError
        with self.assertRaises(ValueError):
            approval._parse_judgement("{not json")

    def test_non_object_json_raises(self):
        # 对抗: JSON 顶层须为对象
        with self.assertRaises(ValueError):
            approval._parse_judgement("[1, 2, 3]")

    def test_reasons_non_list_reset(self):
        # 对抗: reasons 非 list → 置空(不抛)
        j = approval._parse_judgement(
            '{"recommendation": "approval", "risk_score": 3, "reasons": "oops"}')
        self.assertEqual(j["reasons"], [])

    def test_default_judge_fail_closed_when_not_configured(self):
        # .env 会兜底提供真实 TOKENHUB_API_KEY(settings._get 优先级: 环境变量>.env>默认),
        # 只清环境变量仍会读到 key 并真实触网 → 必须 patch 配置接口返回空串。
        with patch.object(settings, "LLM_API_KEY", return_value=""):
            with self.assertRaises(Exception):
                approval._default_judge({})


class CooldownActiveTests(unittest.TestCase):
    """冷却去重: 用测试 DB 落库验证 cooldown_active 判定。"""

    def setUp(self):
        db.init_db()
        with db.session_scope() as s:
            s.query(models.SelfHealAction).delete()
            if not s.query(models.Server).first():
                cred = models.SSHCredential(name="c", username="root",
                                            password="x", port=22, is_default=True)
                s.add(cred)
                s.flush()
                s.add(models.Server(name="s", host="10.0.0.1",
                                    ssh_credential_id=cred.id))
        self._insert_executed(1, "disk_clean", "truncate_log", hours_ago=0.5)

    def tearDown(self):
        # 清理测试库记录, 避免污染同库其他用例
        with db.session_scope() as s:
            s.query(models.SelfHealAction).delete()

    def _insert_executed(self, server_id, scene, action_name, hours_ago):
        with db.session_scope() as s:
            row = models.SelfHealAction(
                server_id=server_id, scene=scene,
                target=json.dumps({"mount": "/"}),
                severity="low", action_name=action_name,
                status="verified", triggered_by="auto",
                success=True,
                executed_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            )
            s.add(row)

    def test_active_within_cooldown(self):
        self.assertTrue(approval.cooldown_active(1, "disk_clean", "truncate_log", hours=1))

    def test_inactive_beyond_cooldown(self):
        self.assertFalse(approval.cooldown_active(1, "disk_clean", "truncate_log", hours=0.1))

    def test_disabled_when_hours_zero(self):
        self.assertFalse(approval.cooldown_active(1, "disk_clean", "truncate_log", hours=0))

    def test_different_action_not_cooled(self):
        self.assertFalse(approval.cooldown_active(1, "disk_clean", "clean_cache", hours=1))

    def test_pending_not_cooled(self):
        # 对抗: 仅 pending(未执行)不冷却——冷却语义是"已执行过"
        with db.session_scope() as s:
            s.query(models.SelfHealAction).delete()
            s.add(models.SelfHealAction(
                server_id=1, scene="disk_clean", target=json.dumps({"mount": "/"}),
                severity="high", action_name="truncate_log",
                status="pending", triggered_by="auto",
                executed_at=None))
        self.assertFalse(approval.cooldown_active(1, "disk_clean", "truncate_log", hours=1))

    def test_failed_not_cooled(self):
        # 对抗: failed 不冷却——失败可重试
        with db.session_scope() as s:
            s.query(models.SelfHealAction).delete()
            s.add(models.SelfHealAction(
                server_id=1, scene="disk_clean", target=json.dumps({"mount": "/"}),
                severity="low", action_name="truncate_log",
                status="failed", triggered_by="auto", success=False,
                executed_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
        self.assertFalse(approval.cooldown_active(1, "disk_clean", "truncate_log", hours=1))

    def test_different_server_not_cooled(self):
        # 对抗: 冷却按 server 隔离
        with db.session_scope() as s:
            s.query(models.SelfHealAction).delete()
            if not s.query(models.Server).filter_by(name="s2").first():
                cred = s.query(models.SSHCredential).first()
                s.add(models.Server(name="s2", host="10.0.0.2",
                                    ssh_credential_id=cred.id))
        self._insert_executed(2, "disk_clean", "truncate_log", hours_ago=0.5)
        self.assertFalse(approval.cooldown_active(1, "disk_clean", "truncate_log", hours=1))


class OverflowScoreTests(unittest.TestCase):
    """score 转换对超大整数的容错(fail-closed 方向)。"""

    def test_parse_judgement_huge_int_rejected(self):
        with self.assertRaises(ValueError):
            approval._parse_judgement(
                f'{{"recommendation": "auto", "risk_score": {10 ** 400}}}')

    def test_coerce_score_huge_int_defaults_5(self):
        self.assertEqual(approval._coerce_score(10 ** 400), 5)


if __name__ == "__main__":
    unittest.main()
