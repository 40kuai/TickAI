"""自愈执行层：命令模板白名单 + SSH 写执行器。

安全模型核心：
- 写命令只能来自 ACTIONS 模板，render 前必须通过 validate 白名单校验
- LLM 永远无法构造任意命令
- exec_ssh 复用 get_server_ssh_args 凭据隔离，命令已由模板白名单保证
"""
from __future__ import annotations

from typing import Any, Dict

from hermes.tools.ssh import get_server_ssh_args

from . import config


def _in_whitelist(value: str, whitelist) -> bool:
    return value in whitelist


ACTIONS: Dict[str, Dict[str, Any]] = {
    "restart_service": {
        "scene": "process_restart",
        "template": "systemctl restart {service}",
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
    return action["template"].format(**params)


def _connect_exec(host: str, port: int, username: str, password: str,
                  key_content: str, command: str, timeout: int = 10) -> Dict[str, Any]:
    """真正执行 SSH 命令并返回结果（可被测试 patch）。"""
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
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return {"success": exit_code == 0, "exit_code": exit_code,
                "stdout": out, "stderr": err}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"SSH error: {type(exc).__name__}: {exc}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def exec_ssh(server_id: int, command: str, timeout: int = 10) -> Dict[str, Any]:
    """在服务器上执行一条已由模板白名单渲染的命令。

    任何异常（凭据缺失、连接失败等）都收敛为结构化的失败结果，
    供上层自愈编排判断，绝不向上抛导致流程中断。
    """
    try:
        host, cred_args, server_name = get_server_ssh_args(server_id)
        return _connect_exec(
            host=host,
            port=int(cred_args.get("port", 22)),
            username=cred_args["username"],
            password=cred_args.get("password", ""),
            key_content=cred_args.get("key_content", ""),
            command=command,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"SSH error: {type(exc).__name__}: {exc}"}
