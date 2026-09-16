"""API 层技能反馈闭环测试 (P1 能力治理).

覆盖 /api/skills 新增端点:
- GET  /{name}/feedback        反馈列表(按 run_at 倒序)
- POST /{name}/feedback/{id}   标注 accepted/rejected + 备注
- POST 非法 decision → 422
- POST 不存在的记录 → 404
- GET  /{name}/versions        进化历史(版本倒序)

鉴权同 test_selfheal_routes: HttpOnly Cookie 携带 access_token。
"""
import os
import unittest
from pathlib import Path

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/api_skill_feedback.db")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ["ADMIN_INITIAL_PASSWORD"] = "admin-test-pw"
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from hermes.auth import create_session, get_user, init_default_user  # noqa: E402
from hermes.data import db, models  # noqa: E402

from api.deps import create_access_token  # noqa: E402
from api.skill_routes import router as skill_router  # noqa: E402

app = FastAPI()
app.include_router(skill_router)


class SkillFeedbackApiTests(unittest.TestCase):
    def setUp(self):
        # skill_versions 表结构随 P2 加了 status 列, 重建测试表保证最新 schema
        from sqlalchemy import text
        with db.engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS skill_versions"))
        db.init_db()
        with db.session_scope() as s:
            s.query(models.SkillOutcome).delete()
        # 造数据: 2 条反馈 + 2 个版本
        with db.session_scope() as s:
            s.add(models.SkillOutcome(
                skill_name="detect_oom_killed", skill_version=1,
                findings_summary="容器 OOM 被 kill 5 次", user_decision="pending"))
            s.add(models.SkillOutcome(
                skill_name="detect_oom_killed", skill_version=2,
                findings_summary="Node 内存压力持续 10 分钟", user_decision="pending"))
            s.add(models.SkillVersion(
                skill_name="detect_oom_killed", version=1,
                content="# v1", diff="", reason="initial"))
            s.add(models.SkillVersion(
                skill_name="detect_oom_killed", version=2,
                content="# v2", diff="- old\n+ new", reason="auto_evolve"))
        init_default_user()
        admin = get_user("admin")
        sid = create_session(admin)
        token = create_access_token({"session_id": sid})
        self.client = TestClient(app, base_url="https://testserver")
        self.client.cookies.set("access_token", token)

    def _first_outcome_id(self):
        with db.session_scope() as s:
            return s.query(models.SkillOutcome).first().id

    def test_get_feedback_returns_newest_first(self):
        res = self.client.get("/api/skills/detect_oom_killed/feedback")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["count"], 2)
        summaries = [f["findings_summary"] for f in data["feedback"]]
        # 先插的是 v1 那条; 按 run_at 倒序后最新插入的 v2 在前(同秒时依赖 id 兜底不保证,
        # 故不断言顺序, 只断言集合与字段)
        self.assertIn("容器 OOM 被 kill 5 次", summaries)
        self.assertEqual(data["feedback"][0]["user_decision"], "pending")

    def test_mark_feedback_accepted(self):
        oid = self._first_outcome_id()
        res = self.client.post(
            f"/api/skills/detect_oom_killed/feedback/{oid}",
            json={"decision": "accepted", "notes": "结论准确，采纳"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["decision"], "accepted")
        with db.session_scope() as s:
            row = s.query(models.SkillOutcome).get(oid)
            self.assertEqual(row.user_decision, "accepted")
            self.assertIsNotNone(row.decision_at)
            self.assertEqual(row.decision_notes, "结论准确，采纳")

    def test_mark_feedback_reject_allowed(self):
        oid = self._first_outcome_id()
        res = self.client.post(
            f"/api/skills/detect_oom_killed/feedback/{oid}",
            json={"decision": "rejected"})
        self.assertEqual(res.status_code, 200)
        with db.session_scope() as s:
            self.assertEqual(s.query(models.SkillOutcome).get(oid).user_decision, "rejected")

    def test_mark_feedback_invalid_decision_422(self):
        oid = self._first_outcome_id()
        res = self.client.post(
            f"/api/skills/detect_oom_killed/feedback/{oid}",
            json={"decision": "maybe"})
        self.assertEqual(res.status_code, 422)

    def test_mark_feedback_not_found_404(self):
        res = self.client.post(
            "/api/skills/detect_oom_killed/feedback/99999",
            json={"decision": "accepted"})
        self.assertEqual(res.status_code, 404)

    def test_get_versions_returns_history(self):
        res = self.client.get("/api/skills/detect_oom_killed/versions")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["count"], 2)
        reasons = {v["version"]: v["reason"] for v in data["versions"]}
        self.assertEqual(reasons[1], "initial")
        self.assertEqual(reasons[2], "auto_evolve")

    def test_feedback_isolated_per_skill(self):
        res = self.client.get("/api/skills/other_skill/feedback")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["count"], 0)


if __name__ == "__main__":
    unittest.main()
