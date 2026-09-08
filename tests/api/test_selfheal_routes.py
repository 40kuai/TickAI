"""API 层自愈闭环测试 (KR2 Task 7).

覆盖 /api/selfheal 路由:
- GET  /scenes                       4 场景元数据
- POST /run                          高危 disk_clean → pending
- GET  /actions                      status_filter + created_at 倒序
- POST /actions/{id}/approve         重渲染→执行→验证
- POST /actions/{id}/reject          置 rejected
- GET  /stats                        成功率统计

鉴权说明（关键纠正#1）: get_current_user 从 HttpOnly Cookie 读取 access_token(JWT),
不走 Authorization 头。token 由 create_access_token({"session_id": sid}) 生成,
sid 由 hermes.auth.create_session(user)（签名是 create_session(user: User)）创建。
测试用 TestClient(base_url="https://testserver") + client.cookies.set 携带 cookie。
"""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

# 测试 DB 隔离: 必须先于任何 hermes/api 模块导入设置环境变量
# COOKIE_SECURE 用 setdefault,避免覆盖 test_security_fixes 的 true(其测试 Secure 标志)
os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/api_selfheal.db")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ["ADMIN_INITIAL_PASSWORD"] = "admin-test-pw"
os.environ["SELFHEAL_LOG_PATH_WHITELIST"] = "/var/log/nginx/access.log"
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from hermes.auth import create_session, get_user, init_default_user  # noqa: E402
from hermes.data import db, models  # noqa: E402
from hermes.selfheal import config  # noqa: E402

from api.deps import create_access_token  # noqa: E402
from api.selfheal_routes import router as selfheal_router  # noqa: E402

# 挂载在 Task 8 前,自建最小 app 承载 selfheal 路由(不改 api/main.py)
app = FastAPI()
app.include_router(selfheal_router)

DISK_PROBE_HIGH = (
    "Filesystem Type Size Used Avail Use% Mounted on\n"
    "/dev/vda1 ext4 50G 48G 2G 96% /\n"
)
DISK_PROBE_OK = (
    "Filesystem Type Size Used Avail Use% Mounted on\n"
    "/dev/vda1 ext4 50G 35G 15G 70% /\n"
)


class _SelfHealApiTestCase(unittest.TestCase):
    """公共 setUp: 初始化测试 DB、清空自愈动作表、确保 Server + 默认凭据、
    init_default_user() 创建 admin,并生成 HttpOnly Cookie 鉴权客户端。"""

    def setUp(self):
        db.init_db()
        # 强制重置自愈白名单, 防御上游测试(tests/selfheal tearDown pop)污染本文件用例
        os.environ["SELFHEAL_LOG_PATH_WHITELIST"] = "/var/log/nginx/access.log"
        config.reload_config()
        with db.session_scope() as s:
            s.query(models.SelfHealAction).delete()
            cred = s.query(models.SSHCredential).filter_by(name="c").first()
            if cred is None:
                cred = models.SSHCredential(name="c", username="root",
                                            password="x", port=22, is_default=True)
                s.add(cred)
                s.flush()
            if not s.query(models.Server).first():
                s.add(models.Server(name="s", host="10.0.0.1",
                                    ssh_credential_id=cred.id))
        # 鉴权: 初始化 admin → 创建 session → 签发 JWT → 写入 cookie
        init_default_user()
        admin = get_user("admin")
        sid = create_session(admin)
        token = create_access_token({"session_id": sid})
        self.client = TestClient(app, base_url="https://testserver")
        self.client.cookies.set("access_token", token)
        with db.session_scope() as s:
            self.server_id = s.query(models.Server).first().id

    def _unauth_client(self):
        return TestClient(app, base_url="https://testserver")

    def _create_pending(self, scene="disk_clean", action_name="truncate_log",
                        target=None, severity="high"):
        target = target or {"mount": "/", "path": "/var/log/nginx/access.log"}
        with db.session_scope() as s:
            act = models.SelfHealAction(
                server_id=self.server_id, scene=scene,
                target=json.dumps(target, ensure_ascii=False),
                severity=severity, action_name=action_name,
                rendered_command=None, status="pending", triggered_by="user",
            )
            s.add(act)
            s.flush()
            return act.id


class ScenesEndpointTests(_SelfHealApiTestCase):
    def test_scenes_returns_scenes(self):
        # /scenes 派生自 orchestrator.SCENE_ACTION(4 场景); ai_log_cleanup 为 AI 通道专用,
        # 经 POST /ai-plan 生成审批单, 不进 SCENE_ACTION 枚举(避免污染对话工具场景面)。
        r = self.client.get("/api/selfheal/scenes")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(len(d), 4)
        names = {item["name"] for item in d}
        self.assertEqual(names, {"process_restart", "disk_clean", "cache_clean", "log_cleanup_script"})


class RunEndpointTests(_SelfHealApiTestCase):
    def test_run_requires_auth(self):
        c = self._unauth_client()
        r = c.post("/api/selfheal/run", json={
            "server_id": self.server_id, "scene": "disk_clean",
            "target": {"mount": "/", "path": "/var/log/nginx/access.log"},
        })
        self.assertEqual(r.status_code, 401)

    def test_run_high_risk_disk_clean_pending(self):
        # 探测 stdout 96% ≥ 高危阈值 90 → 落审批单 pending,不执行写命令
        import hermes.selfheal.approval as approval_mod
        with patch.object(approval_mod, "_default_judge",
                          side_effect=RuntimeError("no TOKENHUB_API_KEY in test")), \
                patch("hermes.selfheal.actions.exec_ssh", return_value={
                "success": True, "exit_code": 0, "stdout": DISK_PROBE_HIGH,
                "stderr": ""}):
            r = self.client.post("/api/selfheal/run", json={
                "server_id": self.server_id, "scene": "disk_clean",
                "target": {"mount": "/", "path": "/var/log/nginx/access.log"},
            })
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["severity"], "high")
        self.assertIn("action_id", d)
        # DB 断言: 落库 pending 记录, rendered_command 为 None（审批时才渲染）
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, d["action_id"])
            self.assertEqual(row.status, "pending")
            self.assertIsNone(row.rendered_command)


class ActionsEndpointTests(_SelfHealApiTestCase):
    def test_actions_list_with_status_filter(self):
        pid = self._create_pending()
        with db.session_scope() as s:
            s.add(models.SelfHealAction(
                server_id=self.server_id, scene="disk_clean",
                target=json.dumps({"mount": "/", "path": "/var/log/nginx/access.log"}),
                severity="low", action_name="truncate_log",
                rendered_command="truncate -s 0 /var/log/nginx/access.log",
                status="executed", triggered_by="user", success=True,
            ))
        # 无 filter → 全量
        r = self.client.get("/api/selfheal/actions")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 2)
        # status_filter=pending → 只含 pending 记录
        r2 = self.client.get("/api/selfheal/actions?status_filter=pending")
        self.assertEqual(r2.status_code, 200)
        data = r2.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], pid)


class ApproveTests(_SelfHealApiTestCase):
    def test_approve_rerenders_executes_and_verifies(self):
        pid = self._create_pending()
        # 执行 + 验证共 2 次 exec_ssh（approve 走 api/selfheal_routes 里的 actions,
        # 与 orchestrator 同为 hermes.selfheal.actions 模块,统一 patch 即可覆盖）
        with patch("hermes.selfheal.actions.exec_ssh", side_effect=[
                {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
                {"success": True, "exit_code": 0, "stdout": DISK_PROBE_OK, "stderr": ""},
        ]):
            r = self.client.post(f"/api/selfheal/actions/{pid}/approve")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["status"], "verified")
        self.assertTrue(d["success"])
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, pid)
            self.assertEqual(row.status, "verified")
            self.assertTrue(row.success)
            self.assertEqual(row.rendered_command, "truncate -s 0 /var/log/nginx/access.log")
            self.assertEqual(row.approver, "admin")
            self.assertIsNotNone(row.approved_at)

    def test_approve_verification_failed(self):
        pid = self._create_pending()
        with patch("hermes.selfheal.actions.exec_ssh", side_effect=[
                {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
                {"success": True, "exit_code": 0, "stdout": DISK_PROBE_HIGH, "stderr": ""},
        ]):
            r = self.client.post(f"/api/selfheal/actions/{pid}/approve")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["status"], "verification_failed")
        self.assertFalse(d["success"])
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, pid)
            self.assertEqual(row.status, "verification_failed")
            self.assertFalse(row.success)

    def test_reject_marks_rejected(self):
        pid = self._create_pending()
        r = self.client.post(f"/api/selfheal/actions/{pid}/reject")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["status"], "rejected")
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, pid)
            self.assertEqual(row.status, "rejected")

    def test_approve_render_failure_marks_failed(self):
        # 白名单外 path → render_command 抛 ValueError → failed(与 orchestrator 一致)
        pid = self._create_pending(target={"mount": "/", "path": "/tmp/evil.log"})
        r = self.client.post(f"/api/selfheal/actions/{pid}/approve")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["status"], "failed")
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, pid)
            self.assertEqual(row.status, "failed")
            self.assertFalse(row.success)
            self.assertIn("渲染命令失败", row.execution_result or "")

    def test_approve_exec_failure_marks_failed(self):
        # 执行失败(exec_ssh success=False)→ failed,落完整 exec_result + success=False,
        # 且保留 rendered_command(与 orchestrator 低危路径分叉修复对齐)
        pid = self._create_pending()
        with patch("hermes.selfheal.actions.exec_ssh", return_value={
                "success": False, "error": "SSH error: command failed",
                "exit_code": 1, "stdout": "", "stderr": "permission denied"}):
            r = self.client.post(f"/api/selfheal/actions/{pid}/approve")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["status"], "failed")
        self.assertFalse(d["success"])
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, pid)
            self.assertEqual(row.status, "failed")
            self.assertFalse(row.success)
            self.assertEqual(row.rendered_command,
                             "truncate -s 0 /var/log/nginx/access.log")
            self.assertIn("command failed", row.execution_result or "")

    def test_approve_non_pending_returns_400(self):
        pid = self._create_pending()
        with db.session_scope() as s:
            s.get(models.SelfHealAction, pid).status = "verified"
        r = self.client.post(f"/api/selfheal/actions/{pid}/approve")
        self.assertEqual(r.status_code, 400)

    def test_approve_missing_action_returns_404(self):
        r = self.client.post("/api/selfheal/actions/999999/approve")
        self.assertEqual(r.status_code, 404)


class RejectErrorTests(_SelfHealApiTestCase):
    def test_reject_non_pending_returns_400(self):
        pid = self._create_pending()
        with db.session_scope() as s:
            s.get(models.SelfHealAction, pid).status = "verified"
        r = self.client.post(f"/api/selfheal/actions/{pid}/reject")
        self.assertEqual(r.status_code, 400)

    def test_reject_missing_action_returns_404(self):
        r = self.client.post("/api/selfheal/actions/999999/reject")
        self.assertEqual(r.status_code, 404)


class StatsTests(_SelfHealApiTestCase):
    def test_stats_success_rate(self):
        # verified×2(success) + verification_failed×1 + executed×1 + pending×1
        rows = [
            ("verified", True), ("verified", True),
            ("verification_failed", False),
            ("executed", None),
            ("pending", None),
        ]
        with db.session_scope() as s:
            for status_, success in rows:
                s.add(models.SelfHealAction(
                    server_id=self.server_id, scene="disk_clean", target="/",
                    severity="low", action_name="truncate_log",
                    rendered_command="x", status=status_,
                    triggered_by="user", success=success,
                ))
        r = self.client.get("/api/selfheal/stats")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["total_executed"], 4)  # verified×2 + verification_failed + executed
        self.assertEqual(d["success"], 2)
        self.assertEqual(d["success_rate"], 50.0)
        self.assertEqual(d["pending"], 1)

    def test_stats_empty_db(self):
        # 空库 → total=0, success_rate=0.0(避免除零), pending=0
        r = self.client.get("/api/selfheal/stats")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["total_executed"], 0)
        self.assertEqual(d["success"], 0)
        self.assertEqual(d["success_rate"], 0.0)
        self.assertEqual(d["pending"], 0)


class ScanLogEndpointTests(_SelfHealApiTestCase):
    def test_scan_log_endpoint(self):
        # 只读扫描: mock actions.exec_ssh 返回 7 条命令输出
        outs = iter([
            {"success": True, "exit_code": 0,
             "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                       "/dev/vda1 ext4 99G 84G 16G 85% /\n", "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": "journals take up 1.2G\n", "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
        ])
        with patch("hermes.selfheal.actions.exec_ssh", side_effect=lambda *a, **k: next(outs)):
            r = self.client.post("/api/selfheal/scan-log",
                                 json={"server_id": self.server_id})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("disk", data)
        self.assertEqual(data["disk"]["/"]["use_pct"], 85)


class AiPlanEndpointTests(_SelfHealApiTestCase):
    def test_ai_plan_endpoint_generates_pending(self):
        # create_plan 已接入统一出口: AI 判定不可用(fail-closed) → approval 挂单, 不执行
        import hermes.selfheal.approval as approval_mod
        strategy = {"mount": "/", "items": [
            {"type": "journal_vacuum", "size": "200"},
            {"type": "truncate_file", "path": "/etc/passwd"},
        ]}
        with patch.object(approval_mod, "_default_judge",
                          side_effect=RuntimeError("no TOKENHUB_API_KEY in test")):
            r = self.client.post("/api/selfheal/ai-plan",
                                 json={"server_id": self.server_id, "strategy": strategy})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["accepted"], 1)
        self.assertEqual(data["rejected"], 1)
        self.assertIn("plan_id", data)

    def test_ai_plan_missing_strategy_400(self):
        r = self.client.post("/api/selfheal/ai-plan", json={"server_id": self.server_id})
        self.assertEqual(r.status_code, 400)


class PlanIdFilterTests(_SelfHealApiTestCase):
    def test_actions_filter_by_plan_id(self):
        from hermes.selfheal import ai_cleanup
        import hermes.selfheal.approval as approval_mod
        strategy = {"mount": "/", "items": [
            {"type": "journal_vacuum", "size": "200"},
        ]}
        # 归入不同 plan_id 的干扰动作: 若无 plan_id 过滤, 会被一并返回
        # AI 判定不可用(fail-closed) → approval 挂单, 不执行(防触网与真实 SSH)
        with patch.object(approval_mod, "_default_judge",
                          side_effect=RuntimeError("no TOKENHUB_API_KEY in test")):
            ai_cleanup.create_plan(self.server_id, strategy, plan_id="plan-x")
            ai_cleanup.create_plan(self.server_id, strategy, plan_id="plan-y")
        r = self.client.get("/api/selfheal/actions?plan_id=plan-x")
        self.assertEqual(r.status_code, 200)
        rows = r.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["plan_id"], "plan-x")


if __name__ == "__main__":
    unittest.main()
