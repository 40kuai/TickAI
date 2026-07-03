"""Ask LLM 页面 — 与 LLM 聊天，工具调用内联展示。"""
import json

import streamlit as st

from ui.common import init_page, sidebar_status, t
from hermes import config, db
from hermes.agents import chat as llm_agent
from hermes.data.models import Conversation

init_page(t("ask_title"), "💬")
sidebar_status()

# 自定义样式注入
st.markdown("""
<style>
/* 侧边栏基础样式 */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
}

/* 所有按钮统一高度 - 最简单最可靠 */
[data-testid="stSidebar"] button[kind="secondary"],
[data-testid="stSidebar"] button[kind="primary"] {
    height: 44px !important;
    min-height: 44px !important;
    max-height: 44px !important;
    line-height: 44px !important;
    border-radius: 10px !important;
    transition: all 0.25s ease !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    font-size: 14px !important;
}

/* Secondary 按钮样式 */
[data-testid="stSidebar"] button[kind="secondary"] {
    border: 1px solid #e2e8f0 !important;
    background: white !important;
}
[data-testid="stSidebar"] button[kind="secondary"]:hover {
    border-color: #94a3b8 !important;
    background: #f8fafc !important;
}

/* Primary 按钮样式（选中状态） */
[data-testid="stSidebar"] button[kind="primary"] {
    border: none !important;
    box-shadow: 0 2px 8px rgba(59, 130, 246, 0.3) !important;
}

/* 警告对话框样式 */
[data-testid="stAlert"] {
    border-radius: 12px !important;
    border: none !important;
    box-shadow: 0 2px 12px rgba(245, 158, 11, 0.15) !important;
}

/* 主按钮样式（确认删除） */
button[kind="primary"] {
    background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%) !important;
    border: none !important;
    font-weight: 600 !important;
    border-radius: 10px !important;
}
button[kind="primary"]:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(239, 68, 68, 0.4);
}
</style>
""", unsafe_allow_html=True)

st.title(t("ask_title"))
st.caption(t("ask_caption"))


def _tool_label(name: str) -> str:
    mapping = {
        "check_disk_on_server": "ask_tool_check_disk",
        "check_resources_on_server": "ask_tool_check_resources",
        "list_services_on_server": "ask_tool_list_services",
        "list_servers": "ask_tool_list_servers",
        "query_runs": "ask_tool_query_runs",
        "check_disk_usage": "ask_tool_check_disk_direct",
    }
    return t(mapping.get(name, "ask_tool_generic"), name=name)


if not config.LLM_API_KEY():
    st.warning(t("ask_no_key"))
    st.stop()

with st.sidebar:
    st.subheader(t("ask_section_conversations"))
    with db.session_scope() as s:
        convs = s.query(Conversation).order_by(Conversation.updated_at.desc()).all()
    
    # 新对话按钮
    if st.button(f"➕ {t('ask_new_conv')}", use_container_width=True):
        st.session_state.pop("conv_id", None)
        st.rerun()
    
    # 处理删除确认
    delete_id = st.session_state.get("delete_conv_id")
    if delete_id is not None:
        delete_title = ""
        for c in convs:
            if c.id == delete_id:
                delete_title = c.title[:30]
                break
        
        st.markdown(f"""
        <div style="
            background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
            border-radius: 12px;
            padding: 16px;
            margin: 12px 0;
            border-left: 4px solid #f59e0b;
        ">
            <div style="font-weight: 600; color: #92400e; margin-bottom: 8px;">
                ⚠️ 确认删除
            </div>
            <div style="color: #78350f; font-size: 14px;">
                确定要删除对话 <b>#{delete_id}: {delete_title}</b> 吗？
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ 删除", type="primary", use_container_width=True):
                with db.session_scope() as s:
                    c = s.get(Conversation, delete_id)
                    if c:
                        s.delete(c)
                        s.commit()
                st.session_state.pop("delete_conv_id", None)
                if st.session_state.get("conv_id") == delete_id:
                    st.session_state.pop("conv_id", None)
                st.rerun()
        with col2:
            if st.button("❌ 取消", use_container_width=True):
                st.session_state.pop("delete_conv_id", None)
                st.rerun()
    
    # 对话列表 - 简洁单列布局
    current_conv = st.session_state.get("conv_id")
    for c in convs:
        is_active = current_conv == c.id
        title = f"#{c.id}: {c.title[:30]}"
        if st.button(
            title,
            key=f"conv_{c.id}",
            use_container_width=True,
            type="primary" if is_active else "secondary"
        ):
            st.session_state["conv_id"] = c.id
            st.rerun()
    
    # 选中对话后显示操作按钮
    if current_conv:
        with db.session_scope() as s:
            c = s.get(Conversation, current_conv)
            if c:
                st.caption(f"当前对话: #{c.id}: {c.title[:30]}")
                
                # 重命名功能
                if "rename_mode" not in st.session_state:
                    st.session_state["rename_mode"] = False
                
                if st.session_state["rename_mode"]:
                    new_title = st.text_input("新标题", value=c.title, max_chars=50, key="new_title")
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("✅ 保存", type="primary", use_container_width=True):
                            c.title = new_title
                            s.commit()
                            st.session_state["rename_mode"] = False
                            st.rerun()
                    with col2:
                        if st.button("❌ 取消", use_container_width=True):
                            st.session_state["rename_mode"] = False
                            st.rerun()
                else:
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("✏️ 重命名", use_container_width=True):
                            st.session_state["rename_mode"] = True
                            st.rerun()
                    with col2:
                        if st.button("🗑️ 删除", type="secondary", use_container_width=True):
                            st.session_state["delete_conv_id"] = current_conv
                            st.rerun()

conv_id = st.session_state.get("conv_id")
messages = []

if conv_id is not None:
    with db.session_scope() as s:
        c = s.get(Conversation, conv_id)
        if c is None:
            st.session_state.pop("conv_id", None)
            st.rerun()
        messages = json.loads(c.messages_json or "[]")

for msg in messages:
    role = msg.get("role")
    if role == "user":
        with st.chat_message("user"):
            st.write(msg.get("content", ""))
    elif role == "assistant":
        content = msg.get("content")
        tool_calls = msg.get("tool_calls") or []
        with st.chat_message("assistant"):
            if content:
                st.markdown(content)
            for tc in tool_calls:
                fn = tc.get("function") or {}
                tool_name = fn.get("name", "?")
                label = _tool_label(tool_name)
                with st.expander(label, expanded=False):
                    st.code(fn.get("arguments", ""), language="json")
    elif role == "tool":
        with st.chat_message("assistant"):
            with st.expander(t("ask_tool_result", id=msg.get("tool_call_id", "?")), expanded=False):
                st.code(msg.get("content", ""), language="json")

prompt = st.chat_input(t("ask_input"))
if prompt:
    with st.chat_message("user"):
        st.write(prompt)
    with st.chat_message("assistant"):
        with st.spinner(t("thinking")):
            try:
                result = llm_agent.chat(prompt, conversation_id=conv_id)
            except Exception as exc:
                st.error(f"{t('error_occurred')}{exc}")
                st.stop()
            if result["tool_calls"]:
                for tc in result["tool_calls"]:
                    label = _tool_label(tc["name"])
                    with st.expander(t("ask_tool_call_label", label=label, ms=tc["elapsed_ms"]),
                                     expanded=False):
                        st.json({"args": tc["args"]})
                        st.code(tc["result_preview"])
            st.markdown(result["reply"])
            st.session_state["conv_id"] = result["conversation_id"]

with st.sidebar:
    st.caption(f"{t('ask_model_caption')}`{config.LLM_MODEL()}`")
    if conv_id:
        with db.session_scope() as s:
            c = s.get(Conversation, conv_id)
            if c:
                st.caption(t("ask_total_runs", n=c.total_runs or 0))