"""Skills page — Browse / Run / Outcomes / Scheduler / Evolve."""
import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from ui.common import init_page, sidebar_status, t
from hermes.data import db
from hermes.config.settings import LLM_API_KEY
from hermes.skills.loader import list_skills, load_skill
from hermes.agents.skill_runner import SkillRunner
from hermes.agents.skill_evolver import SkillEvolver
from hermes.tools.k8s.kubeconfig import with_kubeconfig, list_contexts_safe
from hermes.data.models import SkillOutcome, SkillVersion
from hermes.tools.registry import registry
from hermes.core.llm import TokenHubClient

init_page(t("skills_title"), "🧠")
sidebar_status()

st.title(f"🧠 {t('skills_title')}")
st.caption(t("skills_subtitle"))


@st.cache_resource
def _get_llm_client():
    api_key = LLM_API_KEY()
    if not api_key:
        return None
    from hermes.config.settings import LLM_MODEL, LLM_BASE_URL
    return TokenHubClient(
        api_key=api_key,
        model=LLM_MODEL(),
        base_url=LLM_BASE_URL(),
    )


tab_browse, tab_run, tab_outcomes, tab_evolve = st.tabs([
    f"📂 {t('tab_browse')}",
    f"▶️ {t('tab_run')}",
    f"📊 {t('tab_outcomes')}",
    f"🤖 {t('tab_evolve')}",
])

skills = list_skills()
skill_names = [s["name"] for s in skills]

with tab_browse:
    if not skills:
        st.warning(t("no_skills_found"))
    else:
        st.write(f"**{len(skills)}** {t('skills_in_dir')}")
        for s in skills:
            with st.expander(f"📄 {s['name']}  —  {s['description'][:80]}"):
                c1, c2 = st.columns([1, 2])
                with c1:
                    st.markdown(f"**{t('trigger')}:** `{s['trigger'] or '(none)'}`")
                    st.markdown(f"**{t('severity')}:** `{s['severity'] or '(none)'}`")
                    st.markdown(f"**{t('path')}:**")
                    st.code(s["path"], language=None)
                with c2:
                    st.markdown(f"**{t('description')}:** {s['description']}")
                    st.markdown(f"**{t('body')}:**")
                    st.code(s["body"], language="markdown")

with tab_run:
    if not skills:
        st.warning(t("no_skills_to_run"))
    else:
        kubeconfig_path = st.text_input(
            t("kubeconfig_path"),
            value=st.session_state.get("kubeconfig_path", ""),
            placeholder=f"~/.kube/config  ({t('leave_empty_for_default')})",
            help=t("kubeconfig_path_help"),
            key="kubeconfig_path_input",
        )
        st.session_state["kubeconfig_path"] = kubeconfig_path

        contexts: list[dict] = []
        if kubeconfig_path:
            contexts = list_contexts_safe(kubeconfig_path)
            if not contexts:
                st.warning(t("kubeconfig_invalid"))

        if not contexts:
            contexts_out = registry.dispatch("list_k8s_contexts", {})
            try:
                contexts = json.loads(contexts_out).get("contexts", [])
            except Exception:
                contexts = []

        col1, col2 = st.columns(2)
        with col1:
            selected_skill = st.selectbox(t("select_skill"), skill_names)
        with col2:
            if contexts:
                ctx_names = [c["name"] for c in contexts]
                current_idx = 0
                for i, c in enumerate(contexts):
                    if c.get("is_current"):
                        current_idx = i
                        break
                cluster_context = st.selectbox(
                    t("cluster_context"),
                    ctx_names,
                    index=current_idx,
                    help=t("cluster_context_help"),
                )
            else:
                cluster_context = st.text_input(
                    t("cluster_context"),
                    value="",
                    placeholder=t("cluster_context_placeholder"),
                    help=t("cluster_context_help"),
                )

        triggered_by = st.radio(t("triggered_by"), ["user", "scheduled", "llm"], horizontal=True)

        if st.button(f"▶️ {t('run_skill')}", type="primary"):
            client = _get_llm_client()
            if client is None:
                st.error(t("error_no_llm_key"))
            else:
                with st.spinner(t("running_skill").format(skill=selected_skill)):
                    try:
                        with with_kubeconfig(kubeconfig_path):
                            runner = SkillRunner(
                                llm_client=client,
                                skills_dir=Path(__file__).parent.parent / "skills",
                                language=st.session_state.get("lang", "zh"),
                            )
                            outcome_id = runner.run(
                                selected_skill,
                                cluster_context=cluster_context,
                                triggered_by=triggered_by,
                            )
                        st.success(t("skill_completed").format(id=outcome_id))
                        with db.session_scope() as s:
                            o = s.get(SkillOutcome, outcome_id)
                            st.markdown(f"### {t('findings')}")
                            st.markdown(o.findings_summary or "_(no summary)_")
                            with st.expander(t("raw_json")):
                                try:
                                    st.json(json.loads(o.findings_json))
                                except Exception:
                                    st.code(o.findings_json)
                    except Exception as exc:
                        st.error(f"{t('skill_failed')}: {exc}")

with tab_outcomes:
    with db.session_scope() as s:
        outcomes = s.query(SkillOutcome).order_by(SkillOutcome.run_at.desc()).limit(50).all()
    if not outcomes:
        st.info(t("no_outcomes_yet"))
    else:
        n_accepted = sum(1 for o in outcomes if o.user_decision == "accepted")
        n_rejected = sum(1 for o in outcomes if o.user_decision == "rejected")
        n_pending = sum(1 for o in outcomes if o.user_decision == "pending")
        c1, c2, c3 = st.columns(3)
        c1.metric(t("accepted"), n_accepted)
        c2.metric(t("rejected"), n_rejected)
        c3.metric(t("pending"), n_pending)

        st.divider()
        for o in outcomes:
            decision_emoji = {
                "accepted": "✅", "rejected": "❌", "pending": "⏳"
            }.get(o.user_decision, "❔")
            with st.container(border=True):
                top = st.columns([3, 1, 1])
                with top[0]:
                    st.markdown(
                        f"**{o.skill_name}** @ `{o.cluster_context or '(default)'}` "
                        f"· _{o.run_at.strftime('%Y-%m-%d %H:%M') if o.run_at else '?'}_"
                    )
                with top[1]:
                    st.markdown(f"{decision_emoji} **{t('decision')}:** {o.user_decision}")
                with top[2]:
                    if o.outcome_effect:
                        st.markdown(f"**{t('effect')}:** {o.outcome_effect}")

                with st.expander(t("view_findings")):
                    st.markdown(o.findings_summary or "_(empty)_")
                    with st.expander(t("raw_json")):
                        try:
                            st.json(json.loads(o.findings_json))
                        except Exception:
                            st.code(o.findings_json)

                if o.user_decision == "pending":
                    btn_cols = st.columns([1, 1, 1, 3])
                    if btn_cols[0].button(f"✅ {t('accept')}", key=f"acc_{o.id}"):
                        with db.session_scope() as sess:
                            row = sess.get(SkillOutcome, o.id)
                            row.user_decision = "accepted"
                            row.decision_at = datetime.utcnow()
                        st.rerun()
                    if btn_cols[1].button(f"❌ {t('reject')}", key=f"rej_{o.id}"):
                        with db.session_scope() as sess:
                            row = sess.get(SkillOutcome, o.id)
                            row.user_decision = "rejected"
                            row.decision_at = datetime.utcnow()
                        st.rerun()

with tab_evolve:
    st.write(t("evolve_intro"))
    if not skills:
        st.warning(t("no_skills_to_evolve"))
    else:
        col1, col2 = st.columns([2, 1])
        with col1:
            evolve_target = st.selectbox(t("select_skill_to_evolve"), skill_names, key="evolve_sel")
        with col2:
            auto_save = st.checkbox(t("auto_save_evolved"), value=False,
                                     help=t("auto_save_evolved_help"))
        if st.button(f"🤖 {t('evolve_skill')}", type="primary"):
            client = _get_llm_client()
            if client is None:
                st.error(t("error_no_llm_key"))
            else:
                with st.spinner(t("evolving_skill").format(skill=evolve_target)):
                    try:
                        evolver = SkillEvolver(
                            llm_client=client,
                            skills_dir=Path(__file__).parent.parent / "skills",
                            language=st.session_state.get("lang", "zh"),
                        )
                        new_content = evolver.evolve(evolve_target, save=auto_save)
                        st.success(t("evolve_done"))
                        st.markdown(f"### {t('new_version_preview')}")
                        st.code(new_content, language="markdown")
                        if not auto_save:
                            st.info(t("evolve_not_saved"))
                    except Exception as exc:
                        st.error(f"{t('evolve_failed')}: {exc}")

    st.divider()
    if skills:
        with db.session_scope() as s:
            versions = s.query(SkillVersion).filter_by(skill_name=evolve_target).order_by(SkillVersion.version.desc()).all()
        if versions:
            st.markdown(f"### {t('version_history').format(skill=evolve_target)}")
            for v in versions:
                with st.expander(f"v{v.version} — {v.reason} — {v.created_at}"):
                    st.code(v.content, language="markdown")
                    if v.diff:
                        with st.expander("diff"):
                            st.code(v.diff, language="diff")