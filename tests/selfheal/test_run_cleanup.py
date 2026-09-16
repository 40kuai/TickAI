"""Tests for hermes.selfheal.run_cleanup (通道一固定脚本入口)."""
import os
import subprocess
import tempfile
import unittest

from hermes.selfheal import config, run_cleanup


class RunCleanupTests(unittest.TestCase):
    def setUp(self):
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "system,service,docker-log,docker-prune"
        config.reload_config()

    def tearDown(self):
        os.environ.pop("SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST", None)
        config.reload_config()

    def test_load_script_non_empty(self):
        script = run_cleanup.load_cleanup_script()
        self.assertIn("clean_system", script)
        self.assertIn("bash", script)  # shebang
        self.assertTrue(script.lstrip().startswith("#!"))

    def test_build_command_allowed(self):
        cmd = run_cleanup.build_cleanup_command("system")
        # 阈值以 env 前缀下发 + bash -s -- <category>
        self.assertIn("JOURNAL_VACUUM_SIZE_MB=", cmd)
        self.assertIn("bash -s -- system", cmd)

    def test_build_command_rejects_unknown(self):
        with self.assertRaises(ValueError):
            run_cleanup.build_cleanup_command("rm -rf /")

    def test_build_command_defaults_to_all(self):
        # category 缺省/为空 → 默认 all(白名单非空时放行, 脚本内置聚合)
        cmd = run_cleanup.build_cleanup_command("")
        self.assertIn("bash -s -- all", cmd)
        cmd2 = run_cleanup.build_cleanup_command(None)
        self.assertIn("bash -s -- all", cmd2)

    def test_build_command_all_rejected_when_whitelist_empty(self):
        # 安全兜底: 白名单为空时连 all 也拒绝
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = ""
        config.reload_config()
        with self.assertRaises(ValueError):
            run_cleanup.build_cleanup_command("")
        with self.assertRaises(ValueError):
            run_cleanup.build_cleanup_command("all")

    def test_build_command_rejects_invalid_chars(self):
        # 白名单内但含非法字符(空格/分号) → 字符集校验拦截
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "a;rm -rf /,good"
        config.reload_config()
        with self.assertRaises(ValueError):
            run_cleanup.build_cleanup_command("a;rm -rf /")
        # 合法白名单成员仍可用
        self.assertIn("bash -s -- good", run_cleanup.build_cleanup_command("good"))


class DryRunTests(unittest.TestCase):
    def test_build_dry_run_command_appends_dry(self):
        cmd = run_cleanup.build_dry_run_command("system")
        self.assertTrue(cmd.endswith(" bash -s -- system dry"))

    def test_build_dry_run_command_validates_category(self):
        with self.assertRaises(ValueError):
            run_cleanup.build_dry_run_command("; rm -rf /")

    def test_parse_dry_run_counts_files(self):
        out = (
            "[cleanup][2026-09-08 10:00:00] system: remove /var/log/a.gz\n"
            "[cleanup][2026-09-08 10:00:01] system: remove /var/log/b.1\n"
            "[cleanup][2026-09-08 10:00:02] service: truncate /data/app/logs/x.log\n"
            "[cleanup][2026-09-08 10:00:03] done: system\n"
        )
        impact = run_cleanup.parse_dry_run_output(out)
        self.assertEqual(impact["files"], 3)
        self.assertIn("/data/app/logs/x.log", impact["paths"])

    def test_parse_dry_run_empty(self):
        impact = run_cleanup.parse_dry_run_output("")
        self.assertEqual(impact["files"], 0)
        self.assertEqual(impact["paths"], [])
        self.assertEqual(impact["total_size_mb"], 0.0)
        self.assertEqual(impact["planned_commands"], [])

    def test_parse_dry_run_full_count_not_truncated(self):
        # 对抗: 超过 50 条时 files 必须全量计数, 仅 paths 截断到 50(避免影响面低估)
        lines = [
            f"[cleanup][2026-09-08 10:00:{i:02d}] system: remove /var/log/f{i}.gz 1048576"
            for i in range(60)
        ]
        impact = run_cleanup.parse_dry_run_output("\n".join(lines) + "\n")
        self.assertEqual(impact["files"], 60)
        self.assertEqual(len(impact["paths"]), 50)

    def test_parse_dry_run_totals_size_mb(self):
        # 对抗: 行尾 size(字节) 累计为可释放体积, 供 AI 估计影响面
        out = (
            "[cleanup][2026-09-08 10:00:00] system: remove /var/log/a.gz 1048576\n"
            "[cleanup][2026-09-08 10:00:01] system: remove /var/log/b.1 5242880\n"
            "[cleanup][2026-09-08 10:00:02] service: truncate /data/app/logs/x.log 1048576\n"
        )
        impact = run_cleanup.parse_dry_run_output(out)
        self.assertEqual(impact["files"], 3)
        self.assertEqual(impact["total_size_mb"], 7.0)  # (1+5+1)MB

    def test_parse_dry_run_planned_commands(self):
        # 对抗: docker-prune 等 dry 模式输出计划命令, AI 可识别"有动作但文件数/体积不可测"
        out = (
            "[cleanup][2026-09-08 10:00:00] docker-prune: would run: docker container prune -f\n"
            "[cleanup][2026-09-08 10:00:01] docker-prune: would run: docker image prune -f\n"
        )
        impact = run_cleanup.parse_dry_run_output(out)
        self.assertEqual(impact["files"], 0)
        self.assertEqual(
            impact["planned_commands"],
            ["docker container prune -f", "docker image prune -f"],
        )

    def test_parse_dry_run_path_with_space(self):
        # 对抗: 含空格路径不被截断(正则捕获到行尾, 仅末尾数字视为 size)
        out = "[cleanup][2026-09-08 10:00:00] service: truncate /data/app logs/app log.1 2097152\n"
        impact = run_cleanup.parse_dry_run_output(out)
        self.assertEqual(impact["files"], 1)
        self.assertEqual(impact["paths"], ["/data/app logs/app log.1"])
        self.assertEqual(impact["total_size_mb"], 2.0)


class DryRunEndToEndTests(unittest.TestCase):
    """脚本 dry 模式真实输出 → parse_dry_run_output(防解析端与测试自产自销)。"""

    def test_docker_prune_dry_output_has_planned_commands(self):
        # 假 docker 使 clean_docker_prune 走到 dry 输出分支; 验证产生端真实输出
        script = run_cleanup.load_cleanup_script()
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            os.makedirs(bin_dir)
            fake_docker = os.path.join(bin_dir, "docker")
            with open(fake_docker, "w") as f:
                f.write("#!/bin/sh\n:")
            os.chmod(fake_docker, 0o755)
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ.get('PATH', '')}")
            proc = subprocess.run(
                ["bash", "-s", "--", "docker-prune", "dry"],
                input=script, capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0)
        impact = run_cleanup.parse_dry_run_output(proc.stdout)
        self.assertEqual(impact["files"], 0)
        self.assertEqual(
            impact["planned_commands"],
            ["docker container prune -f", "docker image prune -f"],
        )

    def test_system_dry_output_carries_sizes(self):
        # system 类别 remove 行须附带 stat 大小(总影响面可估计)
        script = run_cleanup.load_cleanup_script()
        with tempfile.TemporaryDirectory() as tmp:
            # find 目标固定在 /var/log, 本地 macOS 上不注入实际文件,
            # 仅断言"输出行格式"契约: 若产生 remove 行则必须带 size 后缀
            proc = subprocess.run(
                ["bash", "-s", "--", "system", "dry"],
                input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0)
        impact = run_cleanup.parse_dry_run_output(proc.stdout)
        for line in proc.stdout.splitlines():
            if " remove " in line:
                # remove 行末尾须有 size 数字, 否则会被误判为裸路径
                self.assertRegex(line, r" remove .+ \d+$",
                                 f"remove 行缺少 size: {line}")
        # 无论本地是否命中文件, files 与 paths 必须一致(产生端格式不破坏解析端)
        self.assertEqual(impact["files"], len(impact["paths"]))
