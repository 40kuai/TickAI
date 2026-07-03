"""
配置管理模块

从环境变量和 .env 文件加载配置，提供类型安全的访问接口。
"""
from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Locate the .env file at project root (parent of hermes/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # /Users/40kuai/Documents/ai/
_ENV_FILE = _PROJECT_ROOT / ".env"


def _load_env_file() -> dict:
    """最小化 .env 解析器。"""
    if not _ENV_FILE.exists():
        logger.debug(f".env 文件不存在: {_ENV_FILE}")
        return {}
    out = {}
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        out[k] = v
    logger.debug(f"从 .env 加载了 {len(out)} 个配置项")
    return out


_file_env = _load_env_file()


def _get(key: str, default: str = "") -> str:
    """获取配置值（优先级：环境变量 > .env 文件 > 默认值）"""
    val = os.environ.get(key)
    if val is not None:
        return val
    return _file_env.get(key) or default


def get(key: str, default: str = "") -> str:
    """
    重新读取配置（用于运行时可能变化的配置）

    Args:
        key: 配置键名
        default: 默认值

    Returns:
        配置值
    """
    return _get(key, default)


def reload_env() -> None:
    """重新加载 .env 文件"""
    global _file_env
    _file_env = _load_env_file()


# ============================================================
# 配置分组
# ============================================================

# 数据库（启动时解析一次，路径固定）
DB_PATH: str = str(_PROJECT_ROOT / _get("OPS_DB_PATH", "data/opsticket.db"))

# LLM (TokenHub) — 运行时读取，便于测试通过 os.environ 覆盖
def LLM_API_KEY() -> str:
    return get("TOKENHUB_API_KEY", "")


def LLM_MODEL() -> str:
    return get("TOKENHUB_MODEL", "deepseek-v4-flash")


def LLM_BASE_URL() -> str:
    return get("TOKENHUB_BASE_URL", "https://tokenhub.tencentmaas.com/plan/v3")


def LLM_CONFIGURED() -> bool:
    """检查 LLM 是否已配置"""
    return bool(LLM_API_KEY())


# LDAP — 运行时读取
def LDAP_SERVER() -> str:
    return get("LDAP_SERVER", "")


def LDAP_PORT() -> int:
    try:
        return int(get("LDAP_PORT", "389"))
    except ValueError:
        logger.warning(f"LDAP_PORT 配置错误，使用默认值 389")
        return 389


def LDAP_BIND_DN() -> str:
    return get("LDAP_BIND_DN", "")


def LDAP_BIND_PASSWORD() -> str:
    return get("LDAP_BIND_PASSWORD", "")


def LDAP_SEARCH_BASE() -> str:
    return get("LDAP_SEARCH_BASE", "")


def LDAP_USE_SSL() -> bool:
    return get("LDAP_USE_SSL", "false").lower() == "true"


def LDAP_CONFIGURED() -> bool:
    """检查 LDAP 是否已配置"""
    return bool(LDAP_SERVER() and LDAP_BIND_DN())
