"""API 层技能进化门禁测试 (P2 能力治理).

覆盖 /api/skills 新增端点:
- POST /{name}/evolve                     生成候选(pending), 不写盘
- POST /{name}/versions/{vid}/approve     批准生效(写盘+状态流转)
- POST /{name}/versions/{vid}/reject      拒绝(不写盘)
- POST /{name}/versions/{vid}/rollback    回滚(生成新 active 版本)
- 非 pending approve/reject → 409; LLM 失败 → 502

关键安全约束: 测试绝不写真实技能库 —— mock save_skill 拦截写盘,
仅断言调用与 DB 状态流转。
"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/api_skill_evolution.db")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ["ADMIN_INITIAL_PASSWORD"] = "admin-test-pw"
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from hermes.agents.skill_evolver import EvolutionError  # noqa: E402
from hermes.auth import create_session, get_user, init_default_user  # noqa: E402
from hermes.data import db, models  # noqa: E402

from api.deps import create_access_token  # noqa: E402
from api.skill_routes import router as skill_router  # noqa: E402

app = FastAPI()
app.include_router(skill_router)

NEW_CONTENT = (
    "---\n"
    "name: detect_oom_killed\n"
    "description: improved version\n"
    "trigger: scheduled_daily\n"
    "severity: critical\n"
    "---\n\n"
    "# improved instructions\n"
)


class SkillEvolutionApiTests(unittest.TestCase):
    def setUp(self):
        # skill_versions 表结构随 P2 加了 status 列, 重建测试表保证最新 schema
        from sqlalchemy import text
        with db.engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS skill_versions"))
        db.init_db()
        with db.session_scope() as s:
            # 初始 active v1
            s.add(models.SkillVersion(
                skill_name="detect_oom_killed", version=1,
                content="# v1", diff="", reason="initial", status="active"))
        init_default_user()
        admin = get_user("admin")
        sid = create_session(admin)
        token = create_access_token({"session_id": sid})
        self.client = TestClient(app, base_url="https://testserver")
        self.client.cookies.set("access_token", token)

    def _pending_id(self):
        with db.session_scope() as s:
            row = (s.query(models.SkillVersion)
                   .filter_by(status="pending").first())
            return row.id if row else None

    @patch("hermes.agents.skill_evolver.evolve_skill", return_value=NEW_CONTENT)
    def test_evolve_creates_pending_without_writing(self, _mock_evolve):
        res = self.client.post("/api/skills/detect_oom_killed/evolve?max_tokens=2048")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "pending")
        with db.session_scope() as s:
            row = s.query(models.SkillVersion).get(data["version_id"])
            self.assertEqual(row.status, "pending")
            self.assertEqual(row.reason, "auto_evolve")
            self.assertEqual(row.version, 2)
            self.assertIn("improved", row.content)
        _mock_evolve.assert_called_once()
        self.assertEqual(_mock_evolve.call_args.kwargs["max_tokens"], 2048)

    @patch("hermes.agents.skill_evolver.evolve_skill",
           side_effect=EvolutionError("LLM down"))
    def test_evolve_llm_failure_returns_502(self, _mock_evolve):
        res = self.client.post("/api/skills/detect_oom_killed/evolve")
        self.assertEqual(res.status_code, 502)
        self.assertIn("进化生成失败", res.json()["detail"])

    @patch("hermes.agents.skill_evolver.evolve_skill", return_value=NEW_CONTENT)
    @patch("hermes.skills.loader.save_skill")
    def test_approve_applies_and_marks_candidate_disposed(self, mock_save, _mock_evolve):
        self.client.post("/api/skills/detect_oom_killed/evolve")
        vid = self._pending_id()
        self.assertIsNotNone(vid)
        res = self.client.post(f"/api/skills/detect_oom_killed/versions/{vid}/approve")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "approved")
        # 写盘被调用, 且不重复记录新版本(候选即线上版本, 不产生双记录)
        self.assertEqual(mock_save.call_count, 1)
        self.assertIs(mock_save.call_args.kwargs.get("record_version"), False)
        saved_content = mock_save.call_args[0][1]
        self.assertIn("improved", saved_content)
        with db.session_scope() as s:
            row = s.query(models.SkillVersion).get(vid)
            self.assertEqual(row.status, "active")  # 候选已成为线上版本
            self.assertEqual(s.query(models.SkillVersion).count(), 2)  # 初始 v1 + 候选, 无重复

    @patch("hermes.agents.skill_evolver.evolve_skill", return_value=NEW_CONTENT)
    @patch("hermes.skills.loader.save_skill")
    def test_reject_does_not_apply(self, mock_save, _mock_evolve):
        self.client.post("/api/skills/detect_oom_killed/evolve")
        vid = self._pending_id()
        res = self.client.post(f"/api/skills/detect_oom_killed/versions/{vid}/reject")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "rejected")
        mock_save.assert_not_called()
        with db.session_scope() as s:
            self.assertEqual(s.query(models.SkillVersion).get(vid).status, "rejected")

    @patch("hermes.agents.skill_evolver.evolve_skill", return_value=NEW_CONTENT)
    @patch("hermes.skills.loader.save_skill")
    def test_rollback_creates_new_active_version(self, mock_save, _mock_evolve):
        # 先审批候选让线上变为 NEW_CONTENT(v2 active), 再回滚到 v1(ORIGINAL) → 内容不同 → 200
        self.client.post("/api/skills/detect_oom_killed/evolve")
        vid = self._pending_id()
        self.client.post(f"/api/skills/detect_oom_killed/versions/{vid}/approve")
        with db.session_scope() as s:
            v1_id = s.query(models.SkillVersion).filter_by(version=1).first().id
        res = self.client.post(f"/api/skills/detect_oom_killed/versions/{v1_id}/rollback")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["target_version"], 1)
        # approve 写盘 1 次 + rollback 写盘 1 次, 最后一次 reason=rollback
        self.assertEqual(mock_save.call_count, 2)
        args = mock_save.call_args
        self.assertEqual(args[1]["reason"], "rollback")

    @patch("hermes.skills.loader.save_skill")
    def test_rollback_same_content_returns_409(self, mock_save):
        """目标版本内容与当前线上一致 → 拒绝, 防止产生无意义冗余版本(且不写盘)."""
        with db.session_scope() as s:
            v1_id = s.query(models.SkillVersion).filter_by(version=1).first().id
        res = self.client.post(f"/api/skills/detect_oom_killed/versions/{v1_id}/rollback")
        self.assertEqual(res.status_code, 409)
        self.assertIn("无需回滚", res.json()["detail"])
        mock_save.assert_not_called()

    @patch("hermes.agents.skill_evolver.evolve_skill", return_value=NEW_CONTENT)
    def test_approve_non_pending_returns_409(self, _mock_evolve):
        with db.session_scope() as s:
            v1 = s.query(models.SkillVersion).filter_by(version=1).first()
            v1_id = v1.id
        res = self.client.post(f"/api/skills/detect_oom_killed/versions/{v1_id}/approve")
        self.assertEqual(res.status_code, 409)
        self.assertIn("仅 pending", res.json()["detail"])

    def test_versions_include_status(self):
        with db.session_scope() as s:
            v1 = s.query(models.SkillVersion).filter_by(version=1).first()
        res = self.client.get("/api/skills/detect_oom_killed/versions")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["versions"][0]["id"], v1.id)
        self.assertEqual(data["versions"][0]["status"], "active")

    @patch("hermes.agents.skill_evolver.evolve_skill", return_value=NEW_CONTENT)
    def test_approve_missing_version_404(self, _mock_evolve):
        res = self.client.post("/api/skills/detect_oom_killed/versions/99999/approve")
        self.assertEqual(res.status_code, 404)

    # ---- 坏内容防御: LLM 生成无 frontmatter 的残片时, 必须 fail-closed ----
    BAD_CONTENT = "Multiple pressured mounts: a JSON array of the same objects, one per mount:"

    @patch("hermes.agents.skill_evolver.evolve_skill", return_value=BAD_CONTENT)
    def test_evolve_invalid_content_returns_502(self, _mock_evolve):
        """LLM 输出残片(无 frontmatter) → 不落 pending, 直接 502."""
        res = self.client.post("/api/skills/detect_oom_killed/evolve")
        self.assertEqual(res.status_code, 502)
        self.assertIn("进化生成失败", res.json()["detail"])
        with db.session_scope() as s:
            self.assertIsNone(s.query(models.SkillVersion)
                              .filter_by(status="pending").first())

    def test_approve_invalid_candidate_409(self):
        """历史坏候选(内容无 frontmatter) → 批准被拒(fail-closed), 不写盘."""
        with db.session_scope() as s:
            s.add(models.SkillVersion(
                skill_name="detect_oom_killed", version=99,
                content=self.BAD_CONTENT, diff="",
                reason="auto_evolve", status="pending"))
            s.flush()
            bad_id = s.query(models.SkillVersion).filter_by(version=99).first().id
        with patch("hermes.skills.loader.save_skill") as mock_save:
            res = self.client.post(
                f"/api/skills/detect_oom_killed/versions/{bad_id}/approve")
        self.assertEqual(res.status_code, 409)
        mock_save.assert_not_called()
        with db.session_scope() as s:
            row = s.query(models.SkillVersion).get(bad_id)
            self.assertEqual(row.status, "pending")  # 未被处置, 保持可重新生成


if __name__ == "__main__":
    unittest.main()
