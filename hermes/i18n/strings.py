"""Internationalization — flat-key dictionaries + simple t() function.

- Languages: "zh" (default), "en"
- Use t("key") to translate. Falls back to "zh", then to the key itself.
- The current language lives in st.session_state["lang"].
- Call render_language_selector() once per page in the sidebar.
"""
from __future__ import annotations

from typing import Optional

# Supported languages (code, display label)
SUPPORTED = [("zh", "中文"), ("en", "English")]
DEFAULT_LANG = "zh"

# Flat-key translations. Keep keys grouped by prefix for readability.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh": {
        # --- common ---
        "lang_label": "🌐 语言",
        "save": "保存",
        "delete": "删除",
        "cancel": "取消",
        "save_failed": "保存失败：",
        "required_missing": "名称、主机和用户名必填。",
        "saved": "已保存「{name}」。",
        "thinking": "思考中...",
        "error_occurred": "出错：",

        # --- status & source enums ---
        "status_success": "成功",
        "status_failed": "失败",
        "status_timeout": "超时",
        "status_ssh_error": "SSH 错误",
        "source_user_button": "手动",
        "source_llm_tool_call": "LLM 触发",

        # --- home ---
        "home_title": "🧠 Hermes",
        "home_subtitle": "AI 驱动的智能运维平台",
        "home_section_system": "系统",
        "home_llm_ready": "LLM 已就绪：",
        "home_llm_not_configured": "LLM 未配置（在 .env 中设置 TOKENHUB_API_KEY）",
        "home_metric_servers": "活跃服务器",
        "home_metric_total_runs": "总运行次数",
        "home_metric_last_run": "最近运行",
        "home_intro": (
            "**Hermes** 是一个 AI 驱动的智能运维平台，支持远程服务器管理、Kubernetes 集群分析、"
            "自动化技能执行，并通过 LLM 提供智能运维建议。"
        ),
        "home_get_started": "快速开始",
        "home_get_started_steps": (
            "1. **Servers** → 添加主机，直接用快捷按钮检查磁盘/资源/服务\n"
            "2. **History** → 浏览审计日志\n"
            "3. **Tools** → 查看和测试已注册的工具\n"
            "4. **Skills** → 运行 AI 技能分析集群"
        ),
        "home_architecture": "架构速览（10 秒看懂）",
        "home_quick_stats": "快速统计",
        "home_recent_runs": "最近运行",
        "home_no_runs": "暂无运行记录。先在 **Servers** 页面添加服务器并执行检查。",

        # --- servers page ---
        "servers_title": "🖥️ 服务器",
        "servers_caption": "添加并管理你可以通过 SSH 登录的主机。",
        "servers_add": "➕ 添加新服务器",
        "servers_field_name": "名称*",
        "servers_field_host": "主机 / IP*",
        "servers_field_port": "端口",
        "servers_field_user": "用户名*",
        "servers_field_password": "密码",
        "servers_field_tags": "标签（逗号分隔）",
        "servers_field_notes": "备注",
        "servers_save_btn": "保存",
        "servers_test_btn": "测试连接",
        "servers_delete_btn": "删除",
        "servers_connected": "✓ 已连通",
        "servers_connecting": "连接中...",
        "servers_no_servers": "暂无服务器。请在上方添加一台开始。",
        "servers_count": "共 {n} 台活跃服务器",
        "servers_last_seen": "最近连接：{time}",
        "servers_never_seen": "最近连接：从未",
        "servers_tag_label": "标签：",
        "servers_notes_label": "备注：",
        "servers_action_disk": "💾 磁盘",
        "servers_action_resources": "📊 资源",
        "servers_action_services": "⚙️ 服务",
        "servers_action_result": "结果",
        "servers_action_success": "✓ 完成，耗时 {ms}ms",
        "servers_action_close": "关闭",
        "servers_executing": "正在 SSH 到 {name}...",

        # --- history page ---
        "history_title": "📜 历史",
        "history_caption": "所有 SSH 运行的审计日志。",
        "history_filter_server": "服务器",
        "history_filter_status": "状态",
        "history_filter_source": "来源",
        "history_filter_window": "时间范围",
        "history_all": "（全部）",
        "history_window_24h": "最近 24 小时",
        "history_window_7d": "最近 7 天",
        "history_window_30d": "最近 30 天",
        "history_window_all": "全部时间",
        "history_total": "**共 {n} 条**",
        "history_no_matches": "没有匹配的运行记录。",
        "history_field_status": "**状态：** ",
        "history_field_exit": "**退出码：** ",
        "history_field_source": "**触发方式：** ",
        "history_field_context": "**上下文：** ",
        "history_field_run_id": "**运行 ID：** ",
        "history_field_server_id": "**服务器 ID：** ",
        "history_structured": "**结构化结果：**",
        "history_stdout": "标准输出",
        "history_stderr": "标准错误",
        "history_col_time": "时间",
        "history_col_server": "服务器",
        "history_col_command": "命令",
        "history_col_status": "状态",
        "history_col_duration": "耗时",
        "history_col_source": "来源",

        # --- ask LLM page ---
        "ask_title": "💬 询问 LLM",
        "ask_caption": "用自然语言问关于你服务器集群的问题。LLM 可调用只读工具。",
        "ask_no_key": "尚未设置 TOKENHUB_API_KEY。请编辑 `.env` 启用 LLM。",
        "ask_new_conv": "➕ 新对话",
        "ask_section_conversations": "对话",
        "ask_input": "输入消息...",
        "ask_model_caption": "模型：",
        "ask_total_runs": "本对话已触发运行：{n} 次",
        "ask_tool_check_disk": "💾 检查磁盘",
        "ask_tool_check_disk_direct": "💾 检查磁盘（直接）",
        "ask_tool_check_resources": "📊 检查资源",
        "ask_tool_list_services": "⚙️ 列出服务",
        "ask_tool_list_servers": "🖥️ 列出服务器",
        "ask_tool_query_runs": "📜 查询历史",
        "ask_tool_generic": "🔧 {name}",
        "ask_tool_result": "📥 工具结果 ({id})",
        "ask_tool_call_label": "{label} ({ms}ms)",

        # --- tools page ---
        "tools_title": "🛠️ 工具",
        "tools_subtitle": "查看和测试已注册的工具，了解 LLM 可用的能力。",
        "tools_total": "工具总数",
        "tools_toolset_system": "🖥️ 系统工具",
        "tools_toolset_k8s": "☸️ Kubernetes",
        "tools_toolset_ldap": "🔍 LDAP",
        "tools_toolset_ops": "⚡ 运维工具",
        "tools_toolset_other": "🔧 其他",
        "tools_available": "可用",
        "tools_unavailable": "不可用",
        "tools_schema": "Schema",
        "tools_params": "参数说明",
        "tools_select_tool": "选择工具",
        "tools_selected": "已选工具",
        "tools_desc": "描述",
        "tools_execute": "执行",
        "tools_running": "执行中...",
        "tools_success": "✓ 执行成功",
        "tools_no_tools": "暂无已注册的工具。",

        # --- recent runs table (home + history share these) ---
        "runs_col_time": "时间",
        "runs_col_server": "服务器",
        "runs_col_command": "命令",
        "runs_col_status": "状态",
        "runs_col_duration": "耗时",
        "runs_col_source": "来源",

        # --- skills page ---
        "skills_title": "技能（Skills）",
        "skills_subtitle": "AI 驱动的 Kubernetes 集群分析 — 严格只读。",
        "tab_browse": "浏览",
        "tab_run": "运行",
        "tab_outcomes": "结果",
        "tab_evolve": "自动优化",
        "no_skills_found": "未找到任何 skill。检查 hermes/skills/library/ 目录。",
        "no_skills_to_run": "没有可运行的 skill。",
        "no_skills_to_evolve": "没有可优化的 skill。",
        "skills_in_dir": "个 skill 在 hermes/skills/library/ 目录中。",
        "trigger": "触发方式",
        "severity": "严重级别",
        "path": "路径",
        "description": "描述",
        "body": "正文",
        "select_skill": "选择 skill",
        "select_skill_to_evolve": "选择要优化的 skill",
        "cluster_context": "集群 context（kubeconfig）",
        "cluster_context_help": "留空 = 用当前 context。",
        "kubeconfig_path": "Kubeconfig 路径（可选）",
        "kubeconfig_path_help": "留空 = ~/.kube/config。切换会立即刷新下面的 context 列表。",
        "kubeconfig_invalid": "无法读取该 kubeconfig 文件（不存在或格式错误）。",
        "leave_empty_for_default": "留空 = 默认",
        "cluster_context_placeholder": "(留空 = current-context)",
        "triggered_by": "触发方式",
        "run_skill": "运行 skill",
        "running_skill": "正在运行 {skill}（LLM + 工具调用）...",
        "skill_completed": "Skill 运行完成（outcome #{id}）",
        "skill_failed": "运行失败",
        "findings": "发现",
        "raw_json": "原始 JSON",
        "no_outcomes_yet": "还没有 outcome。先在“运行”页跑一次。",
        "accepted": "已采纳",
        "rejected": "已拒绝",
        "pending": "待定",
        "decision": "决策",
        "effect": "效果",
        "accept": "采纳",
        "reject": "拒绝",
        "view_findings": "查看发现",
        "evolve_intro": (
            "Evolver 读取最近的 outcome，用 LLM 重新生成 skill .md，"
            "然后你可以选择是否保存。\n\n"
            "**注意**：自动优化会让 LLM 改写你的 skill，建议先在不保存（auto-save off）"
            "状态下预览新版本，确认无误后再开启自动保存。"
        ),
        "auto_save_evolved": "自动保存新版本",
        "auto_save_evolved_help": "关闭时只预览，不写入 skill 文件。",
        "evolve_skill": "运行 evolver",
        "evolving_skill": "正在用 LLM 改写 {skill}...",
        "evolve_done": "新版本已生成。",
        "evolve_not_saved": "未保存。如需保存，请勾选“自动保存新版本”后重跑。",
        "evolve_failed": "Evolver 失败",
        "new_version_preview": "新版本预览",
        "version_history": "{skill} 的版本历史",
        "error_no_llm_key": "未配置 LLM API key。请在 .env 中设置 TOKENHUB_API_KEY。",

    },
    "en": {
        # --- common ---
        "lang_label": "🌐 Language",
        "save": "Save",
        "delete": "Delete",
        "cancel": "Cancel",
        "save_failed": "Save failed: ",
        "required_missing": "Name, host and username are required.",
        "saved": "Saved '{name}'.",
        "thinking": "Thinking...",
        "error_occurred": "Error: ",

        # --- status & source enums ---
        "status_success": "success",
        "status_failed": "failed",
        "status_timeout": "timeout",
        "status_ssh_error": "ssh error",
        "source_user_button": "manual",
        "source_llm_tool_call": "LLM-triggered",

        # --- home ---
        "home_title": "🧠 Hermes",
        "home_subtitle": "AI-Powered Intelligent Ops Platform",
        "home_section_system": "System",
        "home_llm_ready": "LLM ready: ",
        "home_llm_not_configured": "LLM not configured (set TOKENHUB_API_KEY in .env)",
        "home_metric_servers": "Active servers",
        "home_metric_total_runs": "Total runs",
        "home_metric_last_run": "Last run",
        "home_intro": (
            "**Hermes** is an AI-powered intelligent ops platform that supports remote server management, "
            "Kubernetes cluster analysis, automated skill execution, and intelligent ops recommendations via LLM."
        ),
        "home_get_started": "Get started",
        "home_get_started_steps": (
            "1. **Servers** → add hosts and use quick actions to check disk/resources/services\n"
            "2. **History** → browse the audit log\n"
            "3. **Tools** → view and test registered tools\n"
            "4. **Skills** → run AI skills for cluster analysis"
        ),
        "home_architecture": "Architecture (in 10 seconds)",
        "home_quick_stats": "Quick stats",
        "home_recent_runs": "Recent runs",
        "home_no_runs": "No runs yet. Add a server and run a check on the **Servers** page.",

        # --- servers page ---
        "servers_title": "🖥️ Servers",
        "servers_caption": "Add and manage the hosts you can SSH into.",
        "servers_add": "➕ Add a new server",
        "servers_field_name": "Name*",
        "servers_field_host": "Host / IP*",
        "servers_field_port": "Port",
        "servers_field_user": "Username*",
        "servers_field_password": "Password",
        "servers_field_tags": "Tags (comma-separated)",
        "servers_field_notes": "Notes",
        "servers_save_btn": "Save server",
        "servers_test_btn": "Test SSH",
        "servers_delete_btn": "Delete",
        "servers_connected": "✓ Connected",
        "servers_connecting": "Connecting...",
        "servers_no_servers": "No servers yet. Add one above to get started.",
        "servers_count": "{n} active server(s)",
        "servers_last_seen": "Last seen: {time}",
        "servers_never_seen": "Last seen: never",
        "servers_tag_label": "Tags: ",
        "servers_notes_label": "Notes: ",
        "servers_action_disk": "💾 Disk",
        "servers_action_resources": "📊 Resources",
        "servers_action_services": "⚙️ Services",
        "servers_action_result": "Result",
        "servers_action_success": "✓ Completed in {ms}ms",
        "servers_action_close": "Close",
        "servers_executing": "SSH-ing into {name}...",

        # --- history page ---
        "history_title": "📜 History",
        "history_caption": "Audit log of every SSH run.",
        "history_filter_server": "Server",
        "history_filter_status": "Status",
        "history_filter_source": "Source",
        "history_filter_window": "Window",
        "history_all": "(all)",
        "history_window_24h": "Last 24h",
        "history_window_7d": "Last 7d",
        "history_window_30d": "Last 30d",
        "history_window_all": "All time",
        "history_total": "**{n} run(s)**",
        "history_no_matches": "No runs match your filters.",
        "history_field_status": "**Status:** ",
        "history_field_exit": "**Exit code:** ",
        "history_field_source": "**Triggered by:** ",
        "history_field_context": "**Context:** ",
        "history_field_run_id": "**Run ID:** ",
        "history_field_server_id": "**Server ID:** ",
        "history_structured": "**Structured result:**",
        "history_stdout": "stdout",
        "history_stderr": "stderr",
        "history_col_time": "Time",
        "history_col_server": "Server",
        "history_col_command": "Command",
        "history_col_status": "Status",
        "history_col_duration": "Duration",
        "history_col_source": "Source",

        # --- ask LLM page ---
        "ask_title": "💬 Ask LLM",
        "ask_caption": "Ask natural-language questions about your fleet. LLM can call read-only tools.",
        "ask_no_key": "TOKENHUB_API_KEY is not set. Edit `.env` to enable the LLM.",
        "ask_new_conv": "➕ New conversation",
        "ask_section_conversations": "Conversations",
        "ask_input": "Type a message…",
        "ask_model_caption": "Model: ",
        "ask_total_runs": "Total runs in this convo: {n}",
        "ask_tool_check_disk": "💾 Check disk",
        "ask_tool_check_disk_direct": "💾 Check disk (direct)",
        "ask_tool_check_resources": "📊 Check resources",
        "ask_tool_list_services": "⚙️ List services",
        "ask_tool_list_servers": "🖥️ List servers",
        "ask_tool_query_runs": "📜 Query history",
        "ask_tool_generic": "🔧 {name}",
        "ask_tool_result": "📥 tool result ({id})",
        "ask_tool_call_label": "{label} ({ms}ms)",

        # --- tools page ---
        "tools_title": "🛠️ Tools",
        "tools_subtitle": "View and test registered tools to understand LLM capabilities.",
        "tools_total": "Total tools",
        "tools_toolset_system": "🖥️ System",
        "tools_toolset_k8s": "☸️ Kubernetes",
        "tools_toolset_ldap": "🔍 LDAP",
        "tools_toolset_ops": "⚡ Ops",
        "tools_toolset_other": "🔧 Other",
        "tools_available": "Available",
        "tools_unavailable": "Unavailable",
        "tools_schema": "Schema",
        "tools_params": "Parameters",
        "tools_select_tool": "Select tool",
        "tools_selected": "Selected",
        "tools_desc": "Description",
        "tools_execute": "Execute",
        "tools_running": "Running...",
        "tools_success": "✓ Success",
        "tools_no_tools": "No tools registered.",

        # --- recent runs table ---
        "runs_col_time": "Time",
        "runs_col_server": "Server",
        "runs_col_command": "Command",
        "runs_col_status": "Status",
        "runs_col_duration": "Duration",
        "runs_col_source": "Source",

        # --- skills page ---
        "skills_title": "Skills",
        "skills_subtitle": "AI-driven K8s cluster analysis — strictly read-only.",
        "tab_browse": "Browse",
        "tab_run": "Run",
        "tab_outcomes": "Outcomes",
        "tab_evolve": "Evolve",
        "no_skills_found": "No skills found. Check hermes/skills/library/ directory.",
        "no_skills_to_run": "No skills to run.",
        "no_skills_to_evolve": "No skills to evolve.",
        "skills_in_dir": " skill(s) in hermes/skills/library/.",
        "trigger": "Trigger",
        "severity": "Severity",
        "path": "Path",
        "description": "Description",
        "body": "Body",
        "select_skill": "Select a skill",
        "select_skill_to_evolve": "Select a skill to evolve",
        "cluster_context": "Cluster context (kubeconfig)",
        "cluster_context_help": "Leave empty to use the current-context.",
        "kubeconfig_path": "Kubeconfig path (optional)",
        "kubeconfig_path_help": "Leave empty for ~/.kube/config. The context list below refreshes when this changes.",
        "kubeconfig_invalid": "Cannot read this kubeconfig file (missing or invalid format).",
        "leave_empty_for_default": "leave empty for default",
        "cluster_context_placeholder": "(leave empty for current-context)",
        "triggered_by": "Triggered by",
        "run_skill": "Run skill",
        "running_skill": "Running {skill} (LLM + tool calls)...",
        "skill_completed": "Skill completed (outcome #{id})",
        "skill_failed": "Skill failed",
        "findings": "Findings",
        "raw_json": "Raw JSON",
        "no_outcomes_yet": "No outcomes yet. Run a skill from the Run tab first.",
        "accepted": "Accepted",
        "rejected": "Rejected",
        "pending": "Pending",
        "decision": "Decision",
        "effect": "Effect",
        "accept": "Accept",
        "reject": "Reject",
        "view_findings": "View findings",
        "evolve_intro": (
            "The evolver reads recent outcomes and asks the LLM to regenerate "
            "the skill .md. You can choose to save the new version.\n\n"
            "**Note**: Auto-evolution lets the LLM rewrite your skill. We recommend "
            "previewing without saving first, then enabling auto-save once you trust it."
        ),
        "auto_save_evolved": "Auto-save the new version",
        "auto_save_evolved_help": "If off, the new content is shown but not written to disk.",
        "evolve_skill": "Run evolver",
        "evolving_skill": "Evolving {skill} with LLM...",
        "evolve_done": "New version generated.",
        "evolve_not_saved": "Not saved. Tick 'Auto-save' and re-run to save it.",
        "evolve_failed": "Evolver failed",
        "new_version_preview": "New version preview",
        "version_history": "Version history of {skill}",
        "error_no_llm_key": "LLM API key not configured. Set TOKENHUB_API_KEY in .env.",

    },
}


# Map enum values (machine-readable) → i18n keys
STATUS_KEY = {
    "success": "status_success",
    "failed": "status_failed",
    "timeout": "status_timeout",
    "ssh_error": "status_ssh_error",
}
SOURCE_KEY = {
    "user_button": "source_user_button",
    "llm_tool_call": "source_llm_tool_call",
}


def t(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Translate a key. Falls back to zh, then to the key itself.

    kwargs are substituted via str.format. Callers are responsible for escaping.
    """
    if lang is None:
        lang = get_lang()
    lang_dict = TRANSLATIONS.get(lang) or TRANSLATIONS[DEFAULT_LANG]
    text = lang_dict.get(key) or TRANSLATIONS[DEFAULT_LANG].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def status_label(value: str) -> str:
    """Map a status enum to its localized label."""
    return t(STATUS_KEY.get(value, "status_success"))


def source_label(value: str) -> str:
    """Map a source enum to its localized label."""
    return t(SOURCE_KEY.get(value, "source_user_button"))


def get_lang() -> str:
    """Read the current language from streamlit session_state (default zh).

    Falls back to DEFAULT_LANG if streamlit is not available (e.g. in tests).
    """
    try:
        import streamlit as st
        return st.session_state.get("lang", DEFAULT_LANG)
    except Exception:
        return DEFAULT_LANG


def set_lang(lang: str) -> None:
    """Persist the language choice to session_state."""
    try:
        import streamlit as st
        st.session_state["lang"] = lang
    except Exception:
        pass


def render_language_selector() -> str:
    """Render a sidebar radio to switch language. Returns the active code.

    Place this once at the top of every page's sidebar.
    """
    import streamlit as st
    current = get_lang()
    codes = [c for c, _ in SUPPORTED]
    labels = {c: lbl for c, lbl in SUPPORTED}
    with st.sidebar:
        new = st.radio(
            t("lang_label"),
            options=codes,
            index=codes.index(current) if current in codes else 0,
            format_func=lambda c: labels[c],
            key="lang_selector",
        )
    if new != current:
        set_lang(new)
        st.rerun()
    return new
