"""自愈模块业务配置：阈值/白名单/业务时段。

所有值默认安全（白名单为空 = 无可用写动作），通过环境变量覆盖：
- SELFHEAL_SERVICE_WHITELIST: 逗号分隔的服务名（systemctl restart 目标）
- SELFHEAL_LOG_PATH_WHITELIST: 逗号分隔的日志/文件路径（truncate 目标）
- SELFHEAL_DROPCACHES_MODES_WHITELIST: 逗号分隔的 drop_caches 模式（如 1,2,3，清理页缓存/目录项/inode 缓存）
- SELFHEAL_PEAK_HOURS: 形如 "9-12,14-18" 的业务高峰期
"""
from __future__ import annotations

from typing import List, Tuple

from hermes.config import settings


def _split_csv(key: str) -> List[str]:
    raw = settings.get(key, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _parse_hours(raw: str) -> List[Tuple[int, int]]:
    """解析 '9-12,14-18' → [(9,12),(14,18)]；非法段跳过。"""
    ranges: List[Tuple[int, int]] = []
    for seg in raw.split(","):
        seg = seg.strip()
        if "-" not in seg:
            continue
        try:
            lo, hi = (int(x) for x in seg.split("-", 1))
            ranges.append((lo, hi))
        except ValueError:
            continue
    return ranges


def _parse_int(key: str, default: int) -> int:
    """解析整数配置; 空值/非数字回退默认, 与 _split_csv/_parse_hours 容错一致。"""
    try:
        return int(settings.get(key, "") or default)
    except ValueError:
        return default


# 磁盘/缓存分级阈值（百分比）
DISK_LOW_PCT = 80
DISK_HIGH_PCT = 90
CACHE_LOW_PCT = 80
CACHE_HIGH_PCT = 90

# 写操作白名单（默认空 = 无可用动作，安全兜底）
SERVICE_WHITELIST: List[str] = _split_csv("SELFHEAL_SERVICE_WHITELIST")
LOG_PATH_WHITELIST: List[str] = _split_csv("SELFHEAL_LOG_PATH_WHITELIST")
DROPCACHES_MODES_WHITELIST: List[str] = _split_csv("SELFHEAL_DROPCACHES_MODES_WHITELIST")

# 业务高峰期（进程重启在此区间强制高危）
PEAK_HOURS: List[Tuple[int, int]] = _parse_hours(
    settings.get("SELFHEAL_PEAK_HOURS", "9-18")
)

# 日志清理固定脚本类别白名单（通道一；默认空=安全兜底）
LOG_CLEANUP_CATEGORIES: List[str] = _split_csv("SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST")

# 统一审批出口: 同类操作冷却期(小时)。冷却期内同 server+scene+action 已执行过
# (executed/verified) 则不再生成审批单, 防自动探测周期性触发造成审批疲劳。
COOLDOWN_HOURS = _parse_int("SELFHEAL_COOLDOWN_HOURS", 6)

# 清理脚本内部阈值（MB/天）
JOURNAL_VACUUM_SIZE_MB = 200
SERVICE_LOG_MAX_MB = 100
SERVICE_LOG_MAX_DAYS = 7
DOCKER_LOG_MAX_MB = 50

# AI 清理中 docker 日志路径前缀（代码常量，不 env 化）
DOCKER_LOG_TRUNCATE_PREFIX = "/var/lib/docker/containers/"


def reload_config() -> None:
    """重新读取配置（环境变量 > .env > 默认，供测试/运行时配置变更）。"""
    global SERVICE_WHITELIST, LOG_PATH_WHITELIST, DROPCACHES_MODES_WHITELIST, PEAK_HOURS
    global LOG_CLEANUP_CATEGORIES, COOLDOWN_HOURS
    SERVICE_WHITELIST = _split_csv("SELFHEAL_SERVICE_WHITELIST")
    LOG_PATH_WHITELIST = _split_csv("SELFHEAL_LOG_PATH_WHITELIST")
    DROPCACHES_MODES_WHITELIST = _split_csv("SELFHEAL_DROPCACHES_MODES_WHITELIST")
    PEAK_HOURS = _parse_hours(settings.get("SELFHEAL_PEAK_HOURS", "9-18"))
    LOG_CLEANUP_CATEGORIES = _split_csv("SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST")
    COOLDOWN_HOURS = _parse_int("SELFHEAL_COOLDOWN_HOURS", 6)
