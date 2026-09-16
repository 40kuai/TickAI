"""固定日志清理脚本执行入口(通道一).

脚本本体 scripts/cleanup_logs.sh 存于仓库, 经 SSH stdin 注入执行,
不落地远端文件、不启用 SFTP。类别受 config.LOG_CLEANUP_CATEGORIES 白名单约束。
远端脚本阈值由本模块以环境变量前缀下发, 与 config.py 保持单一来源。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config

_CLEANUP_SCRIPT = Path(__file__).resolve().parent / "scripts" / "cleanup_logs.sh"

# category 允许的字符集(白名单成员外的恶意配置无法拼入命令)
_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def load_cleanup_script() -> str:
    """读取仓库内置清理脚本全文(执行时经 stdin 注入)。"""
    return _CLEANUP_SCRIPT.read_text(encoding="utf-8")


def _normalize_category(category: str | None) -> str:
    """category 缺省/为空 → 'all'(脚本内置聚合); 其余原样。"""
    return (category or "").strip() or "all"


def build_cleanup_command(category: str | None) -> str:
    """构建 ssh 命令: <阈值env> bash -s -- <category>。

    类别缺省/为空 → 'all'(跑全部4类); 'all' 仅在白名单非空时放行(安全兜底);
    其他类别必须白名单内且仅含 [a-z0-9-](校验失败抛 ValueError)。
    阈值以 env 前缀下发, 保证远端脚本与 config 单一来源一致。
    """
    category = _normalize_category(category)
    if category == "all":
        if not config.LOG_CLEANUP_CATEGORIES:
            raise ValueError("清理类别白名单为空, 禁止 all 全量清理")
    else:
        if category not in config.LOG_CLEANUP_CATEGORIES:
            raise ValueError(
                f"category {category!r} 不在白名单: {config.LOG_CLEANUP_CATEGORIES}"
            )
        if not _CATEGORY_RE.fullmatch(category):
            raise ValueError(f"category {category!r} 含非法字符(仅允许 [a-z0-9-])")
    env_prefix = (
        f"JOURNAL_VACUUM_SIZE_MB={config.JOURNAL_VACUUM_SIZE_MB} "
        f"SERVICE_LOG_MAX_MB={config.SERVICE_LOG_MAX_MB} "
        f"SERVICE_LOG_MAX_DAYS={config.SERVICE_LOG_MAX_DAYS} "
        f"DOCKER_LOG_MAX_MB={config.DOCKER_LOG_MAX_MB}"
    )
    return f"{env_prefix} bash -s -- {category}"


# 脚本输出行: "[cleanup][ts] <category>: remove|truncate <path> [<size_bytes>]"
# 类别名通用化(不再硬编码 system/service/docker-log); 路径允许含空格;
# 末尾可选 size(字节) 供影响面体积估计。
_REMOVE_LINE_RE = re.compile(
    r"\b([a-z][a-z0-9-]*):\s+(?:remove|truncate)\s+(.+?)(?:\s+(\d+))?$",
    re.MULTILINE,
)
# dry 模式计划命令行: "[cleanup][ts] <category>: would run: <cmd>"
_PLANNED_RE = re.compile(r"\b([a-z][a-z0-9-]*):\s+would\s+run:\s+(.+)")


def build_dry_run_command(category: str | None) -> str:
    """构建 dry-run 命令: <阈值env> bash -s -- <category> dry。

    复用 build_cleanup_command 的类别/白名单校验(非法类别抛 ValueError),
    仅追加第二个位置参数 dry —— 脚本 dry 模式只打印将处理文件, 不执行写操作。
    """
    return f"{build_cleanup_command(category)} dry"


def parse_dry_run_output(stdout: str) -> dict[str, object]:
    """统计 dry-run 输出影响面。

    返回 {files, paths, total_size_mb, planned_commands}:
      files: 全量 remove/truncate 行数(不截断, 避免影响面低估)
      paths: 前 50 条被处理路径(供展示)
      total_size_mb: remove/truncate 行附带 size 累计(MB, 保留 1 位; 无 size 数据为 0.0)
      planned_commands: dry 模式输出的计划执行命令(前 20 条, 如 docker-prune,
                        其文件数/体积不可预估, 供 AI 识别"有动作但不可测")
    """
    text = stdout or ""
    matches = list(_REMOVE_LINE_RE.finditer(text))
    paths = [m.group(2) for m in matches[:50]]
    total_bytes = sum(int(m.group(3)) for m in matches if m.group(3) is not None)
    planned = [m.group(2) for m in _PLANNED_RE.finditer(text)][:20]
    return {
        "files": len(matches),
        "paths": paths,
        "total_size_mb": round(total_bytes / 1048576, 1),
        "planned_commands": planned,
    }

