"""Tools 页面 — 工具注册表浏览、Schema 查看、单工具测试。"""
import json

import streamlit as st

from ui.common import init_page, sidebar_status, t
from hermes.tools.registry import registry
import hermes.tools  # noqa: F401

init_page(t("tools_title"), "🛠️")
sidebar_status()

st.title(f"🛠️ {t('tools_title')}")
st.caption(t("tools_subtitle"))

all_tools = list(registry._tools.values())
tools_by_toolset = {}
for tool in all_tools:
    ts = tool.get("toolset", "default")
    if ts not in tools_by_toolset:
        tools_by_toolset[ts] = []
    tools_by_toolset[ts].append(tool)

toolset_order = ["system", "k8s", "ldap", "ops", "default"]
toolset_names = {
    "system": t("tools_toolset_system"),
    "k8s": t("tools_toolset_k8s"),
    "ldap": t("tools_toolset_ldap"),
    "ops": t("tools_toolset_ops"),
    "default": t("tools_toolset_other"),
}

tab_overview, tab_test = st.tabs([
    f"📋 {t('tab_overview')}",
    f"⚡ {t('tab_test')}",
])

with tab_overview:
    st.write(f"{t('tools_total')}: **{len(all_tools)}**")
    for ts in toolset_order:
        if ts not in tools_by_toolset:
            continue
        tools = tools_by_toolset[ts]
        with st.expander(f"{toolset_names[ts]} ({len(tools)})", expanded=True):
            for tool in tools:
                schema = tool["schema"]
                with st.container(border=True):
                    header_cols = st.columns([3, 1, 1])
                    with header_cols[0]:
                        emoji = tool.get("emoji", "🔧")
                        st.markdown(f"**{emoji} {tool['name']}**")
                        st.caption(f"{schema.get('description', '')}")
                    with header_cols[1]:
                        st.markdown(f"`{tool['toolset']}`")
                    with header_cols[2]:
                        available = tool["check_fn"]()
                        status = "✅" if available else "❌"
                        label = t("tools_available") if available else t("tools_unavailable")
                        st.markdown(f"{status} {label}")
                    if "parameters" in schema:
                        with st.expander(t("tools_schema"), expanded=False):
                            st.json(schema["parameters"])
                    if "properties" in schema.get("parameters", {}):
                        st.markdown(t("tools_params"))
                        for param_name, param_info in schema["parameters"]["properties"].items():
                            required = param_name in schema["parameters"].get("required", [])
                            req_mark = "*" if required else ""
                            st.markdown(f"- **{req_mark}{param_name}** ({param_info.get('type')}): {param_info.get('description', '')}")

with tab_test:
    if not all_tools:
        st.warning(t("tools_no_tools"))
    else:
        tool_names = [t["name"] for t in all_tools]
        selected_tool_name = st.selectbox(t("tools_select_tool"), tool_names)
        
        if selected_tool_name:
            selected_tool = registry.get(selected_tool_name)
            if selected_tool:
                schema = selected_tool["schema"]
                st.markdown(f"**{t('tools_selected')}:** `{selected_tool_name}`")
                st.markdown(f"{t('tools_desc')}: {schema.get('description', '')}")
                
                params = {}
                if "parameters" in schema and "properties" in schema["parameters"]:
                    for param_name, param_info in schema["parameters"]["properties"].items():
                        param_type = param_info.get("type", "string")
                        required = param_name in schema["parameters"].get("required", [])
                        label = f"{param_name} {'*' if required else ''}"
                        
                        if param_type == "string":
                            params[param_name] = st.text_input(label, placeholder=param_info.get("description"))
                        elif param_type == "integer":
                            params[param_name] = st.number_input(label, value=0)
                        elif param_type == "boolean":
                            params[param_name] = st.checkbox(label)
                        else:
                            params[param_name] = st.text_input(label, placeholder=param_info.get("description"))
                
                if st.button(f"▶️ {t('tools_execute')}", type="primary"):
                    with st.spinner(t("tools_running")):
                        result = registry.dispatch(selected_tool_name, params)
                        try:
                            result_json = json.loads(result)
                            if "error" in result_json:
                                st.error(f"❌ {result_json['error']}")
                            else:
                                st.success(t("tools_success"))
                                st.json(result_json)
                        except json.JSONDecodeError:
                            st.code(result)