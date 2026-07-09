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
        "Search for user information in LDAP directory. STRICTLY READ-ONLY.\n\n"
        "This tool ONLY performs search queries. It cannot modify, create, "
        "delete, lock/unlock, or reset any LDAP entries.\n\n"
        "Supports searching by username, email, or UID. Returns user entries "
        "with attributes: cn, uid, mail, department, title, phone, and account "
        "status (expiration, lockout, disabled, password expired, last logon).\n\n"
        "IMPORTANT: When presenting results, report the data exactly as returned. "
        "Do NOT calculate or estimate relative time (e.g. '2 months until expiry'). "
        "Only state the factual timestamp.\n\n"
        "Usage examples:\n"
        "  - Search by username: {\"search_type\": \"username\", \"search_value\": \"john\"}\n"
        "  - Search by email: {\"search_type\": \"email\", \"search_value\": \"john@example.com\"}\n"
        "  - Search by UID: {\"search_type\": \"uid\", \"search_value\": \"jdoe\"}\n\n"
        "LDAP configuration (server, bind DN, password) is read from environment "
        "variables. This tool requires LDAP_SERVER and LDAP_BIND_DN to be configured."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "search_type": {
                "type": "string",
                "enum": ["username", "email", "uid"],
                "description": "Type of search to perform.",
            },
            "search_value": {
                "type": "string",
                "description": "The value to search for (username, email, or UID).",
            },
        },
        "required": ["search_type", "search_value"],
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
    search_type = args.get("search_type")
    search_value = args.get("search_value")

    if not search_type or not search_value:
        return tool_error("search_type and search_value are required")

    if search_type not in ["username", "email", "uid"]:
        return tool_error(f"invalid search_type: {search_type}")

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
        # READ-ONLY: connection is used exclusively for search, never modify
        with Connection(ldap_server, bind_dn, bind_password, auto_bind=True) as conn:
            escaped_value = _escape_ldap_filter(search_value)
            filter_map = {
                "username": f"(cn=*{escaped_value}*)",
                "email": f"(mail=*{escaped_value}*)",
                "uid": f"(uid=*{escaped_value}*)",
            }
            search_filter = filter_map[search_type]
            attributes = [
                "cn", "uid", "mail", "department", "title",
                "telephoneNumber", "mobile", "manager", "description",
                # Account status
                "userAccountControl", "accountExpires", "lockoutTime",
                "pwdLastSet", "badPwdCount", "lastLogon",
            ]
            conn.search(search_base, search_filter, search_scope=SUBTREE, attributes=attributes)

            results = []
            for entry in conn.entries:
                # Parse account control flags
                uac_raw = entry.userAccountControl.value if entry.userAccountControl else None
                uac_info = _parse_uac(uac_raw)

                user_data = {
                    "dn": str(entry.entry_dn),
                    "cn": entry.cn.value if entry.cn else None,
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
                    "bad_password_count": entry.badPwdCount.value if entry.badPwdCount else None,
                    "last_logon": _parse_win_time(entry.lastLogon.value if entry.lastLogon else None),
                }
                results.append(user_data)

            return tool_result(
                total=len(results),
                search_type=search_type,
                search_value=search_value,
                users=results,
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
