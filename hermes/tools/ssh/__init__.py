"""Shared SSH helpers for server credential lookup."""
from __future__ import annotations

from typing import Optional, Tuple

from hermes.data.db import session_scope
from hermes.data.models import Server, SSHCredential


def get_server_ssh_args(server_id: int) -> Tuple[str, dict, str]:
    """Look up a server and its SSH credential by server_id.

    Falls back to the default SSH credential if the server has no
    specific credential bound.

    Returns:
        (host, cred_args, server_name) where cred_args contains
        port/username/password or port/username/key_content.

    Raises:
        ValueError: if server not found or no SSH credential available.
    """
    if not isinstance(server_id, int):
        raise ValueError("server_id must be an integer")

    with session_scope() as s:
        server = s.query(Server).filter(Server.id == server_id).first()
        if server is None:
            raise ValueError(
                f"server id={server_id} not found (use list_servers to find valid IDs)"
            )

        # Use bound credential, or fall back to default
        cred = server.ssh_credential
        if cred is None:
            cred = s.query(SSHCredential).filter(SSHCredential.is_default == True).first()

        if cred is None:
            raise ValueError(
                f"server '{server.name}' has no SSH credential bound and no default credential set. "
                f"Please bind an SSH credential or set a default in the server management page."
            )

        host = server.host
        server_name = server.name
        cred_args = cred.to_connect_args()

    return host, cred_args, server_name
