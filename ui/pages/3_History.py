"""History 页面 — 可过滤的审计日志。"""
import json
from datetime import datetime, timedelta

import streamlit as st

from ui.common import init_page, sidebar_status, t
from hermes.data import db
from hermes import audit
from hermes.i18n.strings import status_label, source_label

init_page(t("history_title"), "📜")
sidebar_status()

st.title(t("history_title"))
st.caption(t("history_caption"))

with st.container(border=True):
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        servers = audit.get_servers()
        server_options = [t("history_all")] + [s.name for s in servers]
        server_name = st.selectbox(t("history_filter_server"), options=server_options)
    with c2:
        status_options = [t("history_all"), "success", "failed", "timeout", "ssh_error"]
        status = st.selectbox(
            t("history_filter_status"),
            options=status_options,
            format_func=lambda v: t("history_all") if v == t("history_all") else status_label(v),
        )
    with c3:
        source_options = [t("history_all"), "user_button", "llm_tool_call"]
        triggered_by = st.selectbox(
            t("history_filter_source"),
            options=source_options,
            format_func=lambda v: t("history_all") if v == t("history_all") else source_label(v),
        )
    with c4:
        window_options = {
            t("history_window_24h"): timedelta(hours=24),
            t("history_window_7d"): timedelta(days=7),
            t("history_window_30d"): timedelta(days=30),
            t("history_window_all"): None,
        }
        window_label = st.selectbox(t("history_filter_window"), options=list(window_options.keys()))
        since = window_options[window_label]

all_label = t("history_all")
runs = audit.list_runs(
    server_name=server_name if server_name != all_label else None,
    status=status if status != all_label else None,
    triggered_by=triggered_by if triggered_by != all_label else None,
    since=since,
    limit=500,
)

st.markdown(t("history_total", n=len(runs)))

if not runs:
    st.info(t("history_no_matches"))
else:
    for r in runs:
        status_zh = status_label(r.status)
        source_zh = source_label(r.triggered_by)
        badge = "✅" if r.status == "success" else "❌"
        header = (f"{badge} `{r.started_at.strftime('%Y-%m-%d %H:%M:%S')}` · "
                  f"{r.server.name if r.server else '?'} · "
                  f"{r.command} · {status_zh} · {r.duration_ms}ms · {source_zh}")
        with st.expander(header):
            c1, c2, c3 = st.columns(3)
            c1.write(f"{t('history_field_status')}`{status_zh}`")
            c1.write(f"{t('history_field_exit')}`{r.exit_code}`")
            c2.write(f"{t('history_field_source')}`{source_zh}`")
            if r.triggered_context:
                try:
                    ctx = json.loads(r.triggered_context)
                    c2.write(f"{t('history_field_context')}`{json.dumps(ctx, ensure_ascii=False)}`")
                except json.JSONDecodeError:
                    pass
            c3.write(f"{t('history_field_run_id')}`{r.id}`")
            c3.write(f"{t('history_field_server_id')}`{r.server_id}`")
            if r.structured_result:
                st.markdown(t("history_structured"))
                try:
                    st.json(json.loads(r.structured_result))
                except json.JSONDecodeError:
                    st.code(r.structured_result)
            if r.stdout:
                with st.expander(t("history_stdout"), expanded=False):
                    st.code(r.stdout)
            if r.stderr:
                with st.expander(t("history_stderr"), expanded=False):
                    st.code(r.stderr)