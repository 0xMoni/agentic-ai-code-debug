"""Shared UI building blocks."""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from models.schemas import (
    AgentName,
    AgentStatus,
    FindingSource,
    Issue,
    RetrievalDecision,
    TestOutcome,
    VerificationStatus,
)

STYLES_PATH = Path(__file__).resolve().parent / "styles.css"

SQUAD = [
    AgentName.BUG_FINDER,
    AgentName.STYLE_LINT,
    AgentName.FIXER,
    AgentName.TEST_WRITER,
]

AGENT_COLOURS: dict[AgentName, str] = {
    AgentName.BUG_FINDER: "var(--bug)",
    AgentName.STYLE_LINT: "var(--style)",
    AgentName.FIXER: "var(--fixer)",
    AgentName.TEST_WRITER: "var(--tests)",
    AgentName.EXPLAINER: "var(--explain)",
}

AGENT_TITLES: dict[AgentName, str] = {
    AgentName.BUG_FINDER: "Bug-Finder",
    AgentName.STYLE_LINT: "Style/Lint",
    AgentName.FIXER: "Fixer",
    AgentName.TEST_WRITER: "Test-Writer",
    AgentName.EXPLAINER: "Explainer",
}

AGENT_ROLES: dict[AgentName, str] = {
    AgentName.BUG_FINDER: "Hunts for logic slips, crashes waiting to happen and missed edge cases.",
    AgentName.STYLE_LINT: "Runs Ruff, then says what would make the code easier to read.",
    AgentName.FIXER: "Writes the corrected code, and tries again if the tests fail.",
    AgentName.TEST_WRITER: "Writes pytest tests and runs them safely in a sandbox.",
    AgentName.EXPLAINER: "Walks you through what the code does, without changing it.",
}

STATUS_WORDS: dict[AgentStatus, str] = {
    AgentStatus.WAITING: "Waiting",
    AgentStatus.RUNNING: "Working",
    AgentStatus.COMPLETED: "Done",
    AgentStatus.FAILED: "Failed",
    AgentStatus.SKIPPED: "Not needed",
}

OUTCOME_LABELS: dict[TestOutcome, str] = {
    TestOutcome.PASSED: "passed",
    TestOutcome.FAILED: "failed",
    TestOutcome.ERROR: "error",
    TestOutcome.SKIPPED: "skipped",
    TestOutcome.NOT_EXECUTED: "not run",
}

_TONE_COLOURS = {
    "ok": "var(--ok)",
    "warn": "var(--warn)",
    "bad": "var(--bad)",
    "accent": "var(--highlight)",
    "neutral": "var(--faint)",
}


def inject_styles() -> None:
    try:
        css = STYLES_PATH.read_text(encoding="utf-8")
    except OSError:
        return
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def brand() -> None:
    """The sidebar logo: four squares, one per agent colour."""
    squares = "".join(
        f'<span style="background:{AGENT_COLOURS[name]}"></span>' for name in SQUAD
    )
    st.markdown(
        f'<div class="ds-brand"><div class="mark">{squares}</div>'
        '<div><div class="name">Debug Squad</div>'
        '<div class="tag">Four agents, one review</div></div></div>',
        unsafe_allow_html=True,
    )


def status_list(items: list[tuple[str, str, str]]) -> None:
    """Sidebar system status. items: (what, how, tone)."""
    rows = "".join(
        f'<li><span class="dot {tone}"></span><span>'
        f'<span class="what">{html.escape(what)}</span>'
        f'<span class="how">{html.escape(how)}</span></span></li>'
        for what, how, tone in items
    )
    st.markdown(
        '<div class="ds-status-title">How things look</div>'
        f'<ul class="ds-status">{rows}</ul>',
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str = "") -> None:
    sub = f"<p>{html.escape(subtitle)}</p>" if subtitle else ""
    st.markdown(
        f'<div class="ds-intro"><h1>{html.escape(title)}</h1>{sub}</div>',
        unsafe_allow_html=True,
    )


def note(message: str, tone: str = "neutral") -> None:
    """A callout."""
    st.markdown(f'<div class="ds-note {tone}">{message}</div>', unsafe_allow_html=True)


def chip(label: str, tone: str = "neutral") -> str:
    """A small tag with a coloured dot."""
    colour = _TONE_COLOURS.get(tone, "var(--faint)")
    return (
        f'<span class="ds-tag" style="--agent:{colour}">'
        f"{html.escape(str(label))}</span>"
    )


def chip_row(chips: list[str]) -> None:
    st.markdown(f'<div class="ds-tags">{"".join(chips)}</div>', unsafe_allow_html=True)


def agent_tags(names: list[AgentName]) -> str:
    return "".join(
        f'<span class="ds-tag" style="--agent:{AGENT_COLOURS[name]}">'
        f"{AGENT_TITLES[name]}</span>"
        for name in names
    )


def facts(items: list[tuple[str | int, str]]) -> None:
    """A row of big numbers with plain labels."""
    parts = "".join(
        f'<div class="ds-fact"><div class="n">{html.escape(str(value))}</div>'
        f'<div class="k">{html.escape(label)}</div></div>'
        for value, label in items
    )
    st.markdown(f'<div class="ds-facts">{parts}</div>', unsafe_allow_html=True)


def kv(key: str, value: str, mono: bool = False) -> str:
    classes = "v mono" if mono else "v"
    return (
        f'<div class="ds-kv"><div class="k">{html.escape(key)}</div>'
        f'<div class="{classes}">{html.escape(str(value))}</div></div>'
    )


def kv_block(rows: list[tuple[str, str]], mono: bool = False) -> None:
    st.markdown(
        f'<div class="ds-panel">{"".join(kv(k, v, mono) for k, v in rows)}</div>',
        unsafe_allow_html=True,
    )


def badge(text: str, kind: str | None = None) -> str:
    kind = (kind or text).lower().replace(" ", "_")
    return f'<span class="ds-sev {kind}">{html.escape(text)}</span>'


def empty_state(message: str, hint: str = "") -> None:
    hint_html = f"<p>{html.escape(hint)}</p>" if hint else ""
    st.markdown(
        f'<div class="ds-panel"><h4>{html.escape(message)}</h4>{hint_html}</div>',
        unsafe_allow_html=True,
    )


def _finder_of(issue: Issue) -> AgentName:
    """Which agent's colour this issue should carry."""
    if issue.source is FindingSource.RUFF or issue.issue_id.upper().startswith("STYLE"):
        return AgentName.STYLE_LINT
    return AgentName.BUG_FINDER


def render_issue(issue: Issue) -> None:
    finder = _finder_of(issue)
    where = f"line {issue.line}" if issue.line else "whole file"
    source = (
        f"Ruff {issue.rule_code}" if issue.source is FindingSource.RUFF
        else "Python parser" if issue.source is FindingSource.AST
        else f"{AGENT_TITLES[finder]}, {issue.confidence.value} confidence"
    )
    extras = []
    if issue.reproduction:
        extras.append(f"<b>Try it:</b> {html.escape(issue.reproduction)}")
    if issue.suggested_fix:
        extras.append(f"<b>Fix:</b> {html.escape(issue.suggested_fix)}")
    extras_html = f'<div class="extra">{"<br>".join(extras)}</div>' if extras else ""

    st.markdown(
        f'<div class="ds-issue" style="--agent:{AGENT_COLOURS[finder]}">'
        f'<div class="head"><span class="title">{html.escape(issue.title)}</span>'
        f"{badge(issue.severity.value)}"
        f'<span class="where">{html.escape(where)}, found by {html.escape(source)}</span></div>'
        f'<div class="body">{html.escape(issue.explanation)}</div>{extras_html}</div>',
        unsafe_allow_html=True,
    )


def squad_cards(session=None) -> None:
    """The four agents side by side, with what each actually did."""
    cards = []
    for name in SQUAD:
        trace = session.trace_for(name) if session else None
        status = trace.status if trace else AgentStatus.WAITING
        if trace and status not in (AgentStatus.WAITING, AgentStatus.SKIPPED):
            facts_html = (
                f'<div class="facts"><span><b>{trace.duration_seconds:.1f}s</b> taken</span>'
                f"<span><b>{trace.findings_count}</b> found</span></div>"
            )
        elif status is AgentStatus.SKIPPED:
            facts_html = '<div class="facts"><span>Not needed this time</span></div>'
        else:
            facts_html = '<div class="facts"><span>Ready when you are</span></div>'
        cards.append(
            f'<div class="ds-agent {status.value}" style="--agent:{AGENT_COLOURS[name]}">'
            f'<div class="who"><span class="name">{AGENT_TITLES[name]}</span>'
            f'<span class="state {status.value}">{STATUS_WORDS[status]}</span></div>'
            f'<div class="role">{html.escape(AGENT_ROLES[name])}</div>'
            f"{facts_html}</div>"
        )
    st.markdown(f'<div class="ds-squad">{"".join(cards)}</div>', unsafe_allow_html=True)


def trail(session) -> None:
    """The route this review took through the squad."""
    stops = []
    for name in [*SQUAD, AgentName.EXPLAINER]:
        trace = session.trace_for(name)
        if trace is None:
            continue
        if name is AgentName.EXPLAINER and trace.status is AgentStatus.SKIPPED:
            continue
        stops.append(
            f'<span class="stop {trace.status.value}" style="--agent:{AGENT_COLOURS[name]}">'
            f"{AGENT_TITLES[name]}</span>"
        )
    if session.attempt_count > 1:
        stops.append(
            f'<span class="stop completed">Fix retried {session.attempt_count - 1} time(s)</span>'
        )
    st.markdown(
        f'<div class="ds-trail">{"<span class=arrow>→</span>".join(stops)}</div>',
        unsafe_allow_html=True,
    )


_VERDICT_HEADLINES = {
    VerificationStatus.VERIFIED: ("ok", "Fixed, and the tests agree"),
    VerificationStatus.FAILED: ("bad", "Not fixed yet"),
    VerificationStatus.NOT_VERIFIED: ("warn", "Reviewed, not tested"),
    VerificationStatus.NOT_CONFIGURED: ("warn", "Not set up yet"),
}


def verdict(session) -> None:
    """The headline result, worded so it cannot overclaim."""
    tone, headline = _VERDICT_HEADLINES.get(
        session.verification, ("warn", session.verification.value)
    )
    if session.intent and session.intent.intent.value == "code_explanation":
        tone, headline = "ok", "Here's what it does"
    st.markdown(
        f'<div class="ds-verdict {tone}"><div class="headline">{html.escape(headline)}</div>'
        f"<p>{html.escape(session.overall_assessment)}</p></div>",
        unsafe_allow_html=True,
    )


def render_retrieval(decision: RetrievalDecision) -> None:
    """One retrieval decision - including the decision not to look anything up."""
    who = AGENT_TITLES.get(decision.agent, decision.agent.value)
    if decision.should_retrieve and decision.passages:
        headline = f"{who} read {len(decision.passages)} note(s)"
    elif decision.should_retrieve:
        headline = f"{who} tried to read the notes, but found nothing"
    else:
        headline = f"{who} didn't need the notes"
    query = (
        f'<p style="margin-top:6px">Searched for: <i>{html.escape(decision.query)}</i></p>'
        if decision.query else ""
    )
    error = (
        f'<p style="margin-top:6px;color:var(--bad)">{html.escape(decision.error)}</p>'
        if decision.error else ""
    )
    st.markdown(
        f'<div class="ds-issue" style="--agent:{AGENT_COLOURS.get(decision.agent, "var(--faint)")}">'
        f'<div class="head"><span class="title">{html.escape(headline)}</span></div>'
        f'<div class="body">{html.escape(decision.reason)}</div>{query}{error}</div>',
        unsafe_allow_html=True,
    )
    for index, passage in enumerate(decision.passages, start=1):
        score = f", match {passage.score:.2f}" if passage.score is not None else ""
        with st.expander(f"{index}. {passage.source}{score}"):
            st.markdown(passage.text)
