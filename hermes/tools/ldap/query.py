"""LDAP Tool - STRICTLY READ-ONLY user information queries.

SECURITY: This tool only performs LDAP search operations. It does NOT
support any modification operations (add/modify/delete/rename/password reset).
The connection is used exclusively for conn.search() calls.

Handler registered:
  - ldap_search_user  -> search user by username/email/uid
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone

from hermes.tools.registry import registry, tool_error, tool_result
from hermes.config import (
    LDAP_SERVER,
    LDAP_PORT,
    LDAP_BIND_DN,
    LDAP_BIND_PASSWORD,
    LDAP_SEARCH_BASE,
    LDAP_USE_SSL,
    LDAP_CONFIGURED,
)


# Windows file time epoch: 1601-01-01 UTC
_WIN_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_WIN_MAX = 9223372036854775807  # 0x7FFFFFFFFFFFFFFF -> never expires

# userAccountControl bit flags
_UAC_DISABLED = 0x00000002
_UAC_LOCKED = 0x00000010
_UAC_PW_EXPIRED = 0x00800000


def _parse_win_time(val) -> Optional[str]:
    """Convert Windows file time to ISO string. None/Never -> '永不过期'."""
    if val is None:
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    if n == 0 or n >= _WIN_MAX:
        return "永不过期"
    dt = _WIN_EPOCH + timedelta(microseconds=n / 10)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _parse_lockout(val) -> Optional[str]:
    """0 -> not locked, non-zero -> locked (with timestamp)."""
    if val is None:
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    if n == 0:
        return "未锁定"
    dt = _WIN_EPOCH + timedelta(microseconds=n / 10)
    return f"已锁定 (锁定时间: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')})"


def _parse_uac(val) -> dict:
    """Parse userAccountControl flags."""
    if val is None:
        return {}
    try:
        flags = int(val)
    except (TypeError, ValueError):
        return {}
    return {
        "disabled": bool(flags & _UAC_DISABLED),
        "locked": bool(flags & _UAC_LOCKED),
        "password_expired": bool(flags & _UAC_PW_EXPIRED),
    }


LDAP_SEARCH_USER_SCHEMA = {
    "name": "ldap_search_user",
    "description": (
        "在 LDAP 目录中查询用户信息。严格只读。\n\n"
        "本工具只执行查询,不能修改、创建、删除、锁定/解锁或重置任何 LDAP 条目。\n\n"
        "传入单个查询值(用户名、邮箱或 UID)。工具会自动依次尝试 "
        "sAMAccountName → mail → uid,在第一个匹配处停止。\n\n"
        "返回用户条目属性:cn、username(sAMAccountName)、uid、mail、department、"
        "title、phone 以及账户状态(过期时间、锁定、禁用、密码过期、最后登录)。\n\n"
        "重要:呈现结果时,请如实报告返回的数据。不要计算或推测相对时间(如'还有 2 个月过期')。"
        "只陈述事实时间戳。\n\n"
        "用法示例:\n"
        "  {\"query\": \"helei\"}\n"
        "  {\"query\": \"helei@example.com\"}\n\n"
        "LDAP 配置(服务器、绑定 DN、密码)从环境变量读取。本工具要求配置 LDAP_SERVER 和 LDAP_BIND_DN。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要查询的值(用户名、邮箱或 UID)。",
            },
        },
        "required": ["query"],
    },
}


def _ldap_config_available() -> bool:
    return LDAP_CONFIGURED()


def _escape_ldap_filter(value: str) -> str:
    escape_map = {
        "\\": "\\5c",
        "*": "\\2a",
        "(": "\\28",
        ")": "\\29",
        "\x00": "\\00",
    }
    return "".join(escape_map.get(c, c) for c in value)


def ldap_search_user_handler(args: Dict[str, Any], **kwargs: Any) -> str:
    query = args.get("query")

    if not query:
        return tool_error("query is required")

    server = LDAP_SERVER()
    port = LDAP_PORT()
    bind_dn = LDAP_BIND_DN()
    bind_password = LDAP_BIND_PASSWORD()
    search_base = LDAP_SEARCH_BASE()
    use_ssl = LDAP_USE_SSL()

    if not server:
        return tool_error("LDAP_SERVER is not configured")
    if not bind_dn:
        return tool_error("LDAP_BIND_DN is not configured")

    try:
        from ldap3 import Server, Connection, SUBTREE

        ldap_server = Server(server, port=port, use_ssl=use_ssl, get_info=False)
        escaped_value = _escape_ldap_filter(query)
        # 按优先级依次尝试: sAMAccountName -> mail (精确匹配)
        filter_chain = [
            ("sAMAccountName", f"(sAMAccountName={escaped_value})"),
            ("mail", f"(mail={escaped_value})"),
        ]
        attributes = [
            "cn", "sAMAccountName", "uid", "mail", "department", "title",
            "telephoneNumber", "mobile", "manager", "description",
            # Account status
            "userAccountControl", "accountExpires", "lockoutTime",
            "pwdLastSet", "badPwdCount", "lastLogon",
            # Password expiry (computed by AD)
            "msDS-UserPasswordExpiryTimeComputed",
        ]

        # READ-ONLY: connection is used exclusively for search, never modify
        with Connection(ldap_server, bind_dn, bind_password, auto_bind=True) as conn:
            results = []
            matched_field = None
            for field_name, search_filter in filter_chain:
                conn.search(search_base, search_filter, search_scope=SUBTREE, attributes=attributes)
                if conn.entries:
                    results = conn.entries
                    matched_field = field_name
                    break

            users = []
            for entry in results:
                # Parse account control flags
                uac_raw = entry.userAccountControl.value if entry.userAccountControl else None
                uac_info = _parse_uac(uac_raw)

                user_data = {
                    "dn": str(entry.entry_dn),
                    "cn": entry.cn.value if entry.cn else None,
                    "username": entry.sAMAccountName.value if entry.sAMAccountName else None,
                    "uid": entry.uid.value if entry.uid else None,
                    "email": entry.mail.value if entry.mail else None,
                    "department": entry.department.value if entry.department else None,
                    "title": entry.title.value if entry.title else None,
                    "telephone": entry.telephoneNumber.value if entry.telephoneNumber else None,
                    "mobile": entry.mobile.value if entry.mobile else None,
                    "manager": entry.manager.value if entry.manager else None,
                    "description": entry.description.value if entry.description else None,
                    # Account status
                    "account_expires": _parse_win_time(entry.accountExpires.value if entry.accountExpires else None),
                    "lockout_status": _parse_lockout(entry.lockoutTime.value if entry.lockoutTime else None),
                    "account_disabled": uac_info.get("disabled", None),
                    "account_locked": uac_info.get("locked", None),
                    "password_expired": uac_info.get("password_expired", None),
                    "password_last_set": _parse_win_time(entry.pwdLastSet.value if entry.pwdLastSet else None),
                    "password_expires": _parse_win_time(
                        entry["msDS-UserPasswordExpiryTimeComputed"].value
                        if entry["msDS-UserPasswordExpiryTimeComputed"]
                        else None
                    ),
                    "bad_password_count": entry.badPwdCount.value if entry.badPwdCount else None,
                    "last_logon": _parse_win_time(entry.lastLogon.value if entry.lastLogon else None),
                }
                users.append(user_data)

            return tool_result(
                total=len(users),
                query=query,
                matched_by=matched_field,
                users=users,
            )
    except ImportError:
        return tool_error("ldap3 library not installed")
    except Exception as exc:
        return tool_error(f"LDAP error: {exc}")


registry.register(
    name="ldap_search_user",
    toolset="ldap",
    schema=LDAP_SEARCH_USER_SCHEMA,
    handler=ldap_search_user_handler,
    check_fn=_ldap_config_available,
    emoji="🔍",
    max_result_size_chars=8000,
)
