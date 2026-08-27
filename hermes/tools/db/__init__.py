"""DB query tools — read-only queries against the local SQLite database.

Exports:
- list_servers_handler, query_runs_handler
- LIST_SERVERS_SCHEMA, QUERY_RUNS_SCHEMA

Side effect: importing this package registers both tools in the global registry.
"""
from hermes.tools.db.queries import (  # noqa: F401
    LIST_SERVERS_SCHEMA,
    QUERY_RUNS_SCHEMA,
    list_servers_handler,
    query_runs_handler,
)
