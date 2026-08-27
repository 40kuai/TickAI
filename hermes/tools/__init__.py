"""
自动工具发现与注册模块

自动发现 hermes.tools/ 下的子模块并加载，
无需手动导入即可注册所有工具。
"""
import importlib
import logging
import pkgutil
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def _find_all_py_files(package_path: List[str], parent_name: str) -> List[str]:
    """递归查找所有 .py 文件"""
    modules = []
    hermes_root = Path(__file__).resolve().parent.parent  # hermes/
    for path in package_path:
        p = Path(path)
        for py_file in p.rglob("*.py"):
            if py_file.name == "__init__.py" or py_file.name == "registry.py":
                continue
            # 转换为模块名
            rel = py_file.relative_to(hermes_root)
            module_name = "hermes." + ".".join(rel.with_suffix("").parts)
            modules.append(module_name)
    return modules


def auto_register_tools(tool_modules: List[str] = None) -> None:
    """
    自动发现并注册工具模块（支持嵌套目录）

    Args:
        tool_modules: 如果指定，只加载这些模块；否则自动发现
    """
    if tool_modules is None:
        # 查找所有 .py 文件
        tool_modules = _find_all_py_files(__path__, "hermes.tools")

    for module_name in tool_modules:
        try:
            importlib.import_module(module_name)
            logger.debug(f"成功加载工具模块: {module_name}")
        except Exception as exc:
            logger.warning(f"无法加载工具模块 {module_name}: {exc}")


# 自动注册工具
auto_register_tools()
