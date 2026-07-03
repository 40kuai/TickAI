"""Hermes — AI-Powered Operations Platform."""
import streamlit as st

from ui.common import init_page, sidebar_status, t
from hermes import audit
from hermes.core import setup_logging

setup_logging()
init_page(t("home_title"), "🧠")
sidebar_status()

# 标题区域
st.markdown("""
<div style="
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    padding: 32px;
    border-radius: 16px;
    margin-bottom: 24px;
    color: white;
">
    <h1 style="margin: 0; font-size: 42px;">🎫 TickAI</h1>
    <p style="margin: 8px 0 0 0; font-size: 18px; opacity: 0.95;">
        AI 驱动的智能运维工单平台 — 让 AI 接管繁琐运维
    </p>
</div>
""", unsafe_allow_html=True)

# 平台介绍卡片
st.markdown("""
<div style="
    background: #f8fafc;
    padding: 24px;
    border-radius: 12px;
    border-left: 4px solid #3b82f6;
    margin-bottom: 24px;
">
    <h3 style="margin: 0 0 12px 0; color: #1e40af;">
        关于 TickAI
    </h3>
    <p style="margin: 0; color: #374151; line-height: 1.7;">
        TickAI 是一个专为运维团队打造的 AI 智能工单平台。基于大语言模型技术，
        它能够自动执行服务器巡检、Kubernetes 集群分析、资源优化等任务，
        帮助运维工程师从繁琐的手动操作中解放出来，专注于真正重要的事。
    </p>
</div>
""", unsafe_allow_html=True)

# 核心功能卡片
st.subheader("✨ 核心功能")

col1, col2 = st.columns(2)
with col1:
    st.markdown("""
    <div style="
        background: white;
        padding: 20px;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        margin-bottom: 16px;
    ">
        <h4 style="margin: 0 0 8px 0; color: #059669;">
            🖥️ 服务器管理
        </h4>
        <p style="margin: 0; color: #4b5563; font-size: 14px;">
            一键检查磁盘使用率、系统资源、服务状态，支持批量 SSH 操作
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("""
    <div style="
        background: white;
        padding: 20px;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        margin-bottom: 16px;
    ">
        <h4 style="margin: 0 0 8px 0; color: #7c3aed;">
            🤖 智能对话
        </h4>
        <p style="margin: 0; color: #4b5563; font-size: 14px;">
            自然语言提问，AI 自动调用工具回答，支持上下文记忆
        </p>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown("""
    <div style="
        background: white;
        padding: 20px;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        margin-bottom: 16px;
    ">
        <h4 style="margin: 0 0 8px 0; color: #2563eb;">
            ☸️ Kubernetes 分析
        </h4>
        <p style="margin: 0; color: #4b5563; font-size: 14px;">
            自动检测 Pod OOM、节点资源瓶颈、服务异常等常见问题
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("""
    <div style="
        background: white;
        padding: 20px;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        margin-bottom: 16px;
    ">
        <h4 style="margin: 0 0 8px 0; color: #d97706;">
            📊 全程审计
        </h4>
        <p style="margin: 0; color: #4b5563; font-size: 14px;">
            所有工具调用完整记录，可追溯、可审计、可复盘
        </p>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# 快速开始
st.subheader("🚀 快速开始")
st.markdown("""
| 步骤 | 操作 | 说明 |
|------|------|------|
| 1️⃣ | **Servers** 页面 | 添加你的服务器主机，配置 SSH 连接 |
| 2️⃣ | 点击快捷按钮 | 测试磁盘、资源、服务检查，验证连接 |
| 3️⃣ | **Ask LLM** 页面 | 用自然语言提问运维问题，让 AI 帮你分析 |
| 4️⃣ | **Skills** 页面 | 运行预制的 AI 技能，执行复杂运维分析 |
""")

st.divider()

# 统计数据
st.subheader("📈 快速统计")

runs = audit.list_runs(limit=1000)
success_runs = [r for r in runs if r.status == "success"]
success_rate = int(len(success_runs) / len(runs) * 100) if runs else 0

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown(f"""
    <div style="
        text-align: center;
        padding: 16px;
        background: #eff6ff;
        border-radius: 10px;
    ">
        <div style="font-size: 32px; font-weight: bold; color: #2563eb;">
            {len(audit.get_servers())}
        </div>
        <div style="font-size: 13px; color: #3b82f6; margin-top: 4px;">
            活跃服务器
        </div>
    </div>
    """, unsafe_allow_html=True)
with col2:
    st.markdown(f"""
    <div style="
        text-align: center;
        padding: 16px;
        background: #ecfdf5;
        border-radius: 10px;
    ">
        <div style="font-size: 32px; font-weight: bold; color: #059669;">
            {len(runs)}
        </div>
        <div style="font-size: 13px; color: #10b981; margin-top: 4px;">
            总运行次数
        </div>
    </div>
    """, unsafe_allow_html=True)
with col3:
    st.markdown(f"""
    <div style="
        text-align: center;
        padding: 16px;
        background: #fef3c7;
        border-radius: 10px;
    ">
        <div style="font-size: 32px; font-weight: bold; color: #d97706;">
            {success_rate}%
        </div>
        <div style="font-size: 13px; color: #f59e0b; margin-top: 4px;">
            成功率
        </div>
    </div>
    """, unsafe_allow_html=True)
with col4:
    last = runs[0] if runs else None
    last_time = last.started_at.strftime("%H:%M") if (last and last.started_at) else "—"
    st.markdown(f"""
    <div style="
        text-align: center;
        padding: 16px;
        background: #faf5ff;
        border-radius: 10px;
    ">
        <div style="font-size: 32px; font-weight: bold; color: #7c3aed;">
            {last_time}
        </div>
        <div style="font-size: 13px; color: #8b5cf6; margin-top: 4px;">
            最近运行
        </div>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# 最近运行
st.subheader("🕒 最近运行")
recent = audit.list_runs(limit=8)
if not recent:
    st.info(t("home_no_runs"))
else:
    rows = []
    for r in recent:
        status_emoji = "✅" if r.status == "success" else "❌" if r.status == "failed" else "⏱️"
        rows.append({
            "时间": r.started_at.strftime("%m-%d %H:%M:%S") if r.started_at else "",
            "服务器": r.server.name if r.server else "—",
            "命令": r.command,
            "状态": f"{status_emoji} {r.status}",
            "耗时": f"{r.duration_ms}ms",
        })
    st.dataframe(rows, width="stretch")
