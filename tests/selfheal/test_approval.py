"""Tests for hermes.selfheal.approval (unified approval gate)."""
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
