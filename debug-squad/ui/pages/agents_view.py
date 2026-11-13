"""AI agents - who did what on the latest review."""

from __future__ import annotations

import html

import streamlit as st

from models.schemas import AgentName, AgentStatus
from routing.intent_router import INTENT_LABELS, WORKFLOW_LABELS
from ui import components as ui

TECHNIQUE_WORDS = {
    "zero_shot": "asked directly",
    "few_shot": "shown worked examples first",
    "reflection": "re-thought its answer after failed tests",
    "react_tool_use": "used tools",
}

TOOL_WORDS = {
    "ast.parse": "Python's own parser",
    "ruff": "Ruff linter",
    "knowledge_base": "reference notes",
    "sandbox": "safe test runner",
}


def render(settings) -> None:
    ui.page_header(
        "AI agents",
        "Four specialists, each with one job. Here's who worked on your latest "
        "review and what they did - all recorded as it happened.",
    )

    session = st.session_state.get("session")
    ui.squad_cards(session)

    if session is None:
        ui.empty_state(
            "Nobody's worked on anything yet",
            "Run a review from the code workspace and each agent's work shows up here.",
        )
        return

    if session.intent:
        how = {
            "llm_zero_shot": "The AI read your request",
            "keyword_fallback": "Matched on keywords in your request",
            "manual_override": "You chose this yourself",
        }.get(session.intent.method, session.intent.method)
        ui.note(
            f"<b>Treated as: {INTENT_LABELS[session.intent.intent]}</b>, so the plan was "
            f"{WORKFLOW_LABELS[session.intent.intent].lower()}.<br>"
            f"{html.escape(how)}. {html.escape(session.intent.explanation)}",
            "neutral",
        )

    ui.trail(session)

    st.markdown("### Each agent's log")
    for name in [*ui.SQUAD, AgentName.EXPLAINER]:
        trace = session.trace_for(name)
        if trace is None:
            continue
        if name is AgentName.EXPLAINER and trace.status is AgentStatus.SKIPPED:
            continue

        header = f"{ui.AGENT_TITLES[name]} — {ui.STATUS_WORDS[trace.status].lower()}"
        if trace.duration_seconds:
            header += f", {trace.duration_seconds:.1f}s"

        with st.expander(header, expanded=trace.status is AgentStatus.FAILED):
            if trace.status is AgentStatus.SKIPPED:
                st.markdown(f"Not needed this time: {trace.skip_reason or ''}")
                continue
            if trace.error:
                ui.note(f"<b>Something went wrong:</b> {html.escape(trace.error[:500])}", "bad")

            st.markdown(f"**Result:** {trace.summary or '—'}")
            if trace.techniques:
                st.markdown(
                    "**How it was prompted:** "
                    + ", ".join(TECHNIQUE_WORDS.get(t.value, t.value) for t in trace.techniques)
                )
            if trace.tools_used:
                st.markdown(
                    "**Tools it used:** "
                    + ", ".join(TOOL_WORDS.get(t, t) for t in trace.tools_used)
                )
            for decision in [r for r in session.retrievals if r.agent == name]:
                ui.render_retrieval(decision)
            if trace.events:
                st.markdown("**Step by step**")
                st.code("\n".join(trace.events), language="text")
