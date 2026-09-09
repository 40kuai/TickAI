"""自愈执行层：命令模板白名单 + SSH 写执行器。

安全模型核心：
- 写命令只能来自 ACTIONS 模板，render 前必须通过 validate 白名单校验
- LLM 永远无法构造任意命令
- exec_ssh 复用 get_server_ssh_args 凭据隔离，命令已由模板白名单保证
"""
from __future__ import annotations

import re
from typing import Any, Dict

from hermes.tools.ssh import get_server_ssh_args

from . import config


def _in_whitelist(value: str, whitelist) -> bool:
    return value in whitelist


def _cleanup_category_valid(value: str | None) -> bool:
    """run_cleanup_script 类别校验: 空/缺省 → all(白名单非空即放行), 其余须白名单内。"""
    value = (value or "").strip()
    if value == "" or value == "all":
        return bool(config.LOG_CLEANUP_CATEGORIES)
    return _in_whitelist(value, config.LOG_CLEANUP_CATEGORIES)


# docker 容器日志路径: <prefix><container_id>/<container_id>-json.log
# 全匹配(拒绝空白/分号/管道等 shell 元字符), 防止前缀校验被注入绕过。
_DOCKER_LOG_PATH_RE = re.compile(
    rf"^{re.escape(config.DOCKER_LOG_TRUNCATE_PREFIX)}[A-Za-z0-9]+/[A-Za-z0-9_-]+-json\.log$"
)


def _is_docker_log_path(value: str) -> bool:
    return bool(_DOCKER_LOG_PATH_RE.fullmatch(value))


ACTIONS: Dict[str, Dict[str, Any]] = {
    "restart_service": {
        "scene": "process_restart",
        "template": "systemctl restart {service} || docker restart {service}",
        "validate": lambda p: _in_whitelist(p.get("service", ""), config.SERVICE_WHITELIST),
        "why": "服务名必须在 SELFHEAL_SERVICE_WHITELIST 内",
    },
    "truncate_log": {
        "scene": "disk_clean",
        "template": "truncate -s 0 {path}",
        "validate": lambda p: _in_whitelist(p.get("path", ""), config.LOG_PATH_WHITELIST),
        "why": "路径必须在 SELFHEAL_LOG_PATH_WHITELIST 内",
    },
    "clean_cache": {
        "scene": "cache_clean",
        "template": "sync && echo {mode} > /proc/sys/vm/drop_caches",
        "validate": lambda p: _in_whitelist(p.get("mode", ""), config.DROPCACHES_MODES_WHITELIST),
        "why": "模式必须在 SELFHEAL_DROPCACHES_MODES_WHITELIST 内",
    },
    "run_cleanup_script": {
        "scene": "log_cleanup_script",
        "template": "bash -s -- {category}",
        "validate": lambda p: _cleanup_category_valid(p.get("category", "")),
        "why": "清理类别必须 SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST 内(缺省=全部, 白名单非空)",
        "executor": "cleanup_script",
    },
    "journal_vacuum": {
        "scene": "ai_log_cleanup",
        "template": "journalctl --vacuum-size={size}M",
        "validate": lambda p: str(p.get("size", "")).isdigit() and 1 <= int(p["size"]) <= 10000,
        "why": "size 必须是 1-10000 的数字",
    },
    "docker_log_truncate": {
        "scene": "ai_log_cleanup",
        "template": "truncate -s 0 {path}",
        "validate": lambda p: _is_docker_log_path(str(p.get("path", ""))),
        "why": f"path 必须匹配 {config.DOCKER_LOG_TRUNCATE_PREFIX}<容器ID>/<容器ID>-json.log",
    },
}


def render_command(action_name: str, params: Dict[str, Any]) -> str:
    """渲染白名单命令。校验失败抛 ValueError（安全兜底，绝不 fallback）。"""
    action = ACTIONS.get(action_name)
    if action is None:
        raise ValueError(f"unknown action: {action_name}")
    if not action["validate"](params):
        raise ValueError(
            f"action '{action_name}' 参数校验失败: {action['why']}; params={params}"
        )
    if action.get("executor") == "cleanup_script":
        # 脚本类动作: 复用 build_cleanup_command 完整结果(含 env 阈值前缀),
        # 保证审计命令与实际执行命令一致, 阈值经 env 下发远端脚本。
        from .run_cleanup import build_cleanup_command
        return build_cleanup_command(params.get("category", ""))
    return action["template"].format(**params)


def _connect_exec(host: str, port: int, username: str, password: str,
                  key_content: str, command: str, timeout: int = 10,
                  stdin_data: str | None = None) -> Dict[str, Any]:
    """真正执行 SSH 命令并返回结果（可被测试 patch）。

    stdin_data 非空时经 stdin 注入(写入后 shutdown_write 让远端读 EOF),
    用于仓库内置脚本经 bash -s -- 通道执行, 不落地远端文件。
    """
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if key_content:
            import io
            from paramiko import RSAKey, Ed25519Key, ECDSAKey
            pkey = None
            for KeyClass in (RSAKey, Ed25519Key, ECDSAKey):
                try:
                    pkey = KeyClass.from_private_key(io.StringIO(key_content))
                    break
                except Exception:
                    continue
            if pkey is None:
                return {"success": False, "error": "invalid SSH key content"}
            client.connect(hostname=host, port=port, username=username, pkey=pkey,
                           timeout=timeout, allow_agent=False, look_for_keys=False)
        else:
            client.connect(hostname=host, port=port, username=username,
                           password=password, timeout=timeout,
                           allow_agent=False, look_for_keys=False)
        _in, stdout, stderr = client.exec_command(command, timeout=timeout)
        if stdin_data:
            _in.write(stdin_data)
            _in.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        result = {"success": exit_code == 0, "exit_code": exit_code,
                  "stdout": out, "stderr": err}
        if exit_code != 0:
            # 命令失败但未抛异常: 补充可读 error(取 stderr 摘要), 供上层定位原因
            detail = (err.strip() or out.strip() or f"exit_code={exit_code}")
            result["error"] = f"exit_code={exit_code}: {detail[:200]}"
        return result
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"SSH error: {type(exc).__name__}: {exc}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def _ssh_exec(server_id: int, command: str, timeout: int,
              stdin_data: str | None = None) -> Dict[str, Any]:
    """解析凭据并执行 SSH(可带 stdin 注入)。异常统一收敛为结构化失败。"""
    try:
        host, cred_args, server_name = get_server_ssh_args(server_id)
        port = int(cred_args.get("port", 22))
        if not (1 <= port <= 65535):
            return {"success": False, "error": f"invalid SSH port: {port}"}
        return _connect_exec(
            host=host,
            port=port,
            username=cred_args["username"],
            password=cred_args.get("password", ""),
            key_content=cred_args.get("key_content", ""),
            command=command,
            timeout=timeout,
            stdin_data=stdin_data,
        )
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"SSH error: {type(exc).__name__}: {exc}"}


def exec_ssh(server_id: int, command: str, timeout: int = 10) -> Dict[str, Any]:
    """在服务器上执行一条已由模板白名单渲染的命令。

    任何异常（凭据缺失、连接失败等）都收敛为结构化的失败结果，
    供上层自愈编排判断，绝不向上抛导致流程中断。
    """
    return _ssh_exec(server_id, command, timeout)


def exec_action(server_id: int, action_name: str, command: str,
                target: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    """执行一条已渲染动作命令(支持脚本注入类动作)。

    cleanup_script 类经 stdin 注入仓库脚本(见 run_cleanup.load_cleanup_script),
    其余动作走通用 exec_ssh。未知 action 抛 ValueError, 绝不静默执行任意命令。
    """
    action = ACTIONS.get(action_name)
    if action is None:
        raise ValueError(f"unknown action: {action_name}")
    if action.get("executor") == "cleanup_script":
        from .run_cleanup import load_cleanup_script
        # 脚本读取失败不应被收敛为 SSH error, 故在 _ssh_exec 外读取
        script = load_cleanup_script()
        return _ssh_exec(server_id, command, timeout, stdin_data=script)
    return exec_ssh(server_id, command, timeout=timeout)
