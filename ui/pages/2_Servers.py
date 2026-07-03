"""Servers 页面 — 服务器清单的 CRUD 和快捷操作。"""
import json

import streamlit as st

from ui.common import init_page, sidebar_status, t, handle_error
from hermes.tools.ssh import runner as ssh_runner
from hermes.tools.registry import registry
from hermes.data import db, models
from hermes import audit
from hermes.data.models import Server

init_page(t("servers_title"), "🖥️")
sidebar_status()

st.title(t("servers_title"))
st.caption(t("servers_caption"))

with st.expander(t("servers_add"), expanded=False):
    with st.form("add_server", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(t("servers_field_name"), placeholder="prod-web-01")
            host = st.text_input(t("servers_field_host"), placeholder="10.0.0.1")
            port = st.number_input(t("servers_field_port"), min_value=1, max_value=65535, value=22)
        with c2:
            username = st.text_input(t("servers_field_user"), value="root")
            password = st.text_input(t("servers_field_password"), type="password")
            tags = st.text_input(t("servers_field_tags"), placeholder="prod,web")
        notes = st.text_area(t("servers_field_notes"), placeholder=t("servers_field_notes"))
        if st.form_submit_button(t("servers_save_btn")):
            if not (name and host and username):
                st.error(t("required_missing"))
            else:
                try:
                    with db.session_scope() as s:
                        s.add(models.Server(
                            name=name, host=host, port=int(port),
                            username=username, password=password,
                            tags=tags, notes=notes,
                        ))
                    st.success(t("saved", name=name))
                    st.rerun()
                except Exception as exc:
                    handle_error(exc, "save_failed")

servers = audit.get_servers()
if not servers:
    st.info(t("servers_no_servers"))
else:
    st.subheader(t("servers_count", n=len(servers)))
    for sv in servers:
        with st.container(border=True):
            info_cols = st.columns([4, 3, 3])
            with info_cols[0]:
                st.markdown(f"**{sv.name}**")
                st.caption(f"`{sv.username}@{sv.host}:{sv.port}`")
            with info_cols[1]:
                st.markdown(f"{t('servers_tag_label')}{sv.tags or '—'}")
                if sv.last_seen_at:
                    st.caption(t("servers_last_seen", time=sv.last_seen_at.strftime("%Y-%m-%d %H:%M")))
                else:
                    st.caption(t("servers_never_seen"))
            with info_cols[2]:
                st.markdown(f"{t('servers_notes_label')}{sv.notes or '—'}")

            action_cols = st.columns([2, 2, 2, 2, 1])
            with action_cols[0]:
                if st.button(t("servers_action_disk"), key=f"disk_{sv.id}"):
                    st.session_state[f"action_{sv.id}"] = "disk"
                    st.rerun()
            with action_cols[1]:
                if st.button(t("servers_action_resources"), key=f"res_{sv.id}"):
                    st.session_state[f"action_{sv.id}"] = "resources"
                    st.rerun()
            with action_cols[2]:
                if st.button(t("servers_action_services"), key=f"svc_{sv.id}"):
                    st.session_state[f"action_{sv.id}"] = "services"
                    st.rerun()
            with action_cols[3]:
                if st.button(t("servers_test_btn"), key=f"test_{sv.id}"):
                    with st.spinner(t("servers_connecting")):
                        try:
                            ssh_runner.run_command(sv.id, "df -Th", "user_button")
                            st.success(t("servers_connected"))
                        except Exception as exc:
                            st.error(f"✗ {exc}")
                    st.rerun()
            with action_cols[4]:
                confirm_key = f"delete_{sv.id}"
                if st.session_state.get(confirm_key):
                    if st.button("❌", key=f"confirm_no_{sv.id}"):
                        st.session_state.pop(confirm_key)
                        st.rerun()
                else:
                    if st.button(t("servers_delete_btn"), key=f"del_{sv.id}", type="secondary"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                if st.session_state.get(confirm_key):
                    st.warning(f"{t('delete')} **{sv.name}** ?")
                    if st.button("✅", key=f"confirm_yes_{sv.id}"):
                        with db.session_scope() as s:
                            row = s.get(Server, sv.id)
                            row.is_active = False
                        st.session_state.pop(confirm_key)
                        st.rerun()

            action_type = st.session_state.get(f"action_{sv.id}")
            if action_type:
                tool_map = {
                    "disk": {"tool_name": "check_disk_usage", "cmd_label": "df -Th"},
                    "resources": {"tool_name": "check_resources", "cmd_label": "check_resources"},
                    "services": {"tool_name": "list_services", "cmd_label": "list_services"},
                }
                tool_info = tool_map[action_type]
                with st.expander(f"🔍 {t('servers_action_result')}: {tool_info['cmd_label']}", expanded=True):
                    with st.spinner(t("servers_executing", name=sv.name)):
                        try:
                            if action_type == "disk":
                                run_record = ssh_runner.run_command(sv.id, "df -Th", "user_button")
                                result_json = run_record.structured_result
                                duration_ms = run_record.duration_ms
                            else:
                                args = {
                                    "host": sv.host,
                                    "port": sv.port,
                                    "username": sv.username,
                                    "password": sv.password,
                                }
                                result_json = registry.dispatch(tool_info["tool_name"], args)
                                ssh_runner.persist_tool_run(sv.id, tool_info["cmd_label"], result_json, "user_button")
                                try:
                                    result_data = json.loads(result_json)
                                    duration_ms = 0
                                except json.JSONDecodeError:
                                    duration_ms = 0

                            st.success(t("servers_action_success", ms=duration_ms))
                            if result_json:
                                try:
                                    data = json.loads(result_json)
                                    if "error" in data:
                                        st.error(f"❌ {data['error']}")
                                    elif isinstance(data, list) and len(data) > 0:
                                        st.dataframe(data, width="stretch")
                                    else:
                                        st.json(data)
                                except json.JSONDecodeError:
                                    st.code(result_json)
                        except Exception as exc:
                            st.error(f"✗ {exc}")
                if st.button(t("servers_action_close"), key=f"close_{sv.id}"):
                    st.session_state.pop(f"action_{sv.id}")
                    st.rerun()