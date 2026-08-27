"""
Hermes 日志系统

统一的日志配置，支持按天轮转和多级别输出。
"""
import logging
import sys
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

# Project root - ai/ directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_DIR = _PROJECT_ROOT / "logs"

# 防止重复初始化的标志
_logging_initialized = False


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
    format_str: Optional[str] = None,
    retention_days: int = 7
) -> None:
    """
    配置全局日志系统

    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 日志文件路径（可选，默认 logs/app.log）
        format_str: 自定义日志格式（可选）
        retention_days: 日志保留天数（默认 7 天）
    """
    global _logging_initialized
    if _logging_initialized:
        return  # 已经初始化过，避免重复执行

    if format_str is None:
        format_str = (
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    formatter = logging.Formatter(format_str)

    # 根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 移除已存在的 handlers，避免重复
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 文件输出 - 按天轮转，保留 7 天
    if log_file is None:
        log_file = _LOG_DIR / "app.log"

    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",       # 每天凌晨轮转
        interval=1,               # 每 1 天
        backupCount=retention_days,  # 保留 7 天
        encoding="utf-8",
        utc=False              # 使用本地时区
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 屏蔽第三方库的 verbose 日志
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("streamlit").setLevel(logging.WARNING)

    # 写入日志位置信息
    root_logger.info(f"Logging initialized - log file: {log_file}")
    root_logger.info(f"Log retention: {retention_days} days")

    _logging_initialized = True
