"""Results & history - the latest review, and every review run before it."""

from __future__ import annotations

import html

import streamlit as st

from models.schemas import Intent, Severity, TestOutcome, VerificationStatus
from routing.intent_router import INTENT_LABELS
from services.history_manager import HistoryManager
from services.report_generator import (
    generate_html_report,
    generate_json_report,
    report_filename,
)
from ui import components as ui
from ui.state import PAGE_WORKSPACE, goto, require_session

SEVERITY_ORDER = [s.value for s in Severity]

VERDICT_WORDS = {
    VerificationStatus.VERIFIED.value: ("Fixed and tested", "ok"),
    VerificationStatus.FAILED.value: ("Not fixed yet", "bad"),
    VerificationStatus.NOT_VERIFIED.value: ("Not tested", "warn"),
    VerificationStatus.NOT_CONFIGURED.value: ("Not set up", "neutral"),
}


def render(settings) -> None:
    ui.page_header(
        "Results & history",
        "Your latest review, plus every piece of code you've had reviewed.",
    )

    manager = HistoryManager(settings)
    entries = manager.list_entries()

    latest_tab, history_tab = st.tabs(["Latest review", f"History ({len(entries)})"])
    with latest_tab:
        _latest(settings)
    with history_tab:
        _history(settings, manager, entries)


def _latest(settings) -> None:
    if not require_session("Nothing reviewed yet"):
        return

    session = st.session_state["session"]
    ui.verdict(session)
    ui.trail(session)

    run = session.final_test_run
    ui.facts(
        [
            (session.total_issues, "issues found"),
            (
                f"{run.passed}/{run.executed}" if run and run.executed else "—",
                "tests passed",
            ),
            (session.attempt_count or "—", "fix attempts"),
            (f"{session.duration_seconds:.0f}s", "time taken"),
        ]
    )

    labels = ["Issues", "The fix", "Tests", "Summary"]
    if session.explanation:
        labels.insert(0, "Explanation")
    tabs = dict(zip(labels, st.tabs(labels), strict=True))

    if "Explanation" in tabs:
        with tabs["Explanation"]:
            st.markdown(session.explanation)

    with tabs["Issues"]:
        _issues(session)
    with tabs["The fix"]:
        _fix(session)
    with tabs["Tests"]:
        _tests(session)
    with tabs["Summary"]:
        _summary(settings, session)


def _issues(session) -> None:
    issues = session.all_issues()
    suggestions = session.style_report.suggestions if session.style_report else []

    if not issues and not suggestions:
        ui.empty_state(
            "No issues found",
            "For code that's already correct, that's the right answer - "
            "the agents are told not to invent problems.",
        )
        return

    if issues:
        present = [s for s in SEVERITY_ORDER if any(i.severity.value == s for i in issues)]
        chosen = st.pills(
            "Show",
            options=present,
            default=present,
            selection_mode="multi",
            format_func=lambda s: f"{s.title()} ({sum(i.severity.value == s for i in issues)})",
            key="results_severity_filter",
            label_visibility="collapsed",
        )
        shown = [i for i in issues if i.severity.value in (chosen or [])]
        for issue in sorted(shown, key=lambda i: SEVERITY_ORDER.index(i.severity.value)):
            ui.render_issue(issue)
        if not shown:
            st.caption("Pick a severity above to see those issues.")

    if suggestions:
        st.markdown("#### Readability ideas")
        st.caption("Suggestions from the Style/Lint agent - opinions, not rule breaks.")
        for suggestion in suggestions:
            where = f"line {suggestion.line}" if suggestion.line else "whole file"
            convention = (
                f'<div class="extra">Based on: {html.escape(suggestion.convention)}</div>'
                if suggestion.convention else ""
            )
            st.markdown(
                f'<div class="ds-issue" style="--agent:var(--style)">'
                f'<div class="head"><span class="title">{html.escape(suggestion.title)}</span>'
                f'<span class="where">{html.escape(where)}</span></div>'
                f'<div class="body">{html.escape(suggestion.explanation)}</div>{convention}</div>',
                unsafe_allow_html=True,
            )


def _fix(session) -> None:
    fix = session.final_fix
    if fix is None:
        ui.empty_state(
            "No fix for this one",
            "The Fixer only steps in when you ask for bugs or style to be fixed.",
        )
        return

    if not fix.syntax_valid:
        ui.note(
            "<b>The suggested fix wasn't valid Python</b>, so it was thrown away "
            "and your original code is shown on both sides.",
            "bad",
        )
    if fix.changes:
        st.markdown("#### What changed")
        for change in fix.changes:
            st.markdown(f"- {change}")
    if fix.ambiguities:
        ui.note(
            "<b>Judgement calls the Fixer flagged instead of guessing</b><br>"
            + "<br>".join(f"• {html.escape(a)}" for a in fix.ambiguities),
            "warn",
        )

    view = st.segmented_control(
        "View",
        ["Side by side", "Just the changes"],
        default="Side by side",
        key="fix_view_mode",
        label_visibility="collapsed",
    )
    if view == "Just the changes":
        if fix.unified_diff:
            st.code(fix.unified_diff, language="diff")
        else:
            st.caption("The fixed code is identical to the original.")
    else:
        before, after = st.columns(2)
        with before:
            st.markdown("#### Before")
            st.code(session.original_code, language="python", line_numbers=True)
        with after:
            st.markdown("#### After")
            st.code(fix.corrected_code or session.original_code,
                    language="python", line_numbers=True)

    left, right, _ = st.columns([1, 1, 1.4])
    left.download_button(
        "Download fixed code",
        data=(fix.corrected_code or session.original_code).encode("utf-8"),
        file_name=f"fixed_{session.filename}",
        mime="text/x-python",
        width="stretch",
    )
    if fix.unified_diff:
        right.download_button(
            "Download the diff",
            data=fix.unified_diff.encode("utf-8"),
            file_name=f"{session.filename}.diff",
            mime="text/x-diff",
            width="stretch",
        )


def _tests(session) -> None:
    run = session.final_test_run
    if run is None:
        ui.empty_state(
            "No tests for this one",
            "Tests are written for a full review or when you ask for tests.",
        )
        return

    if not run.sandbox.executed:
        ui.note(
            f"<b>These tests were written but not run.</b> "
            f"{html.escape(run.sandbox.unavailable_reason or '')} "
            "So nothing about whether they'd pass is being claimed.",
            "warn",
        )
    elif run.sandbox.timed_out:
        ui.note(f"<b>The test run was stopped.</b> {html.escape(run.sandbox.stderr)}", "bad")

    if len(session.attempts) > 1:
        steps = " → ".join(
            f"try {a.attempt}: {a.test_run.passed} passed, {a.test_run.failed} failed"
            for a in session.attempts
        )
        ui.note(f"<b>The Fixer had another go after seeing the failures.</b><br>{steps}", "ok")

    for test in run.tests:
        label = ui.OUTCOME_LABELS[test.outcome]
        with st.expander(
            f"{test.name} · {label}",
            expanded=test.outcome in (TestOutcome.FAILED, TestOutcome.ERROR),
        ):
            st.markdown(
                f"{ui.badge(label, test.outcome.value)} &nbsp; {html.escape(test.description)}",
                unsafe_allow_html=True,
            )
            st.markdown(f"**Expected:** {test.expected_behaviour}")
            st.markdown(f"**Why that's right:** {test.oracle}")
            st.code(test.code, language="python")
            if test.failure_message:
                st.markdown("**What pytest said**")
                st.code(test.failure_message, language="text")

    if run.sandbox.stdout:
        with st.expander("Full pytest output"):
            st.code(run.sandbox.stdout, language="text")


def _summary(settings, session) -> None:
    rows = [
        ("File", session.filename),
        ("Your request", session.user_request or "(none)"),
        ("Handled as", INTENT_LABELS.get(session.intent.intent, "?") if session.intent else "?"),
        ("AI model", f"{session.model} on {session.provider}"),
        ("Reference notes", "used when helpful" if session.rag_enabled else "turned off"),
    ]
    ui.kv_block(rows)

    if session.limitations:
        st.markdown("#### Worth knowing")
        for item in session.limitations:
            st.markdown(f"- {item}")
    if session.errors:
        st.markdown("#### Problems along the way")
        for item in session.errors:
            st.markdown(f"- `{item[:400]}`")

    st.markdown("#### Take it with you")
    left, right, _ = st.columns([1, 1, 1.4])
    try:
        left.download_button(
            "Download the report",
            data=generate_html_report(session, max_attempts=settings.max_fix_attempts).encode("utf-8"),
            file_name=report_filename(session, "html"),
            mime="text/html",
            type="primary",
            width="stretch",
        )
    except Exception as exc:  # noqa: BLE001
        left.error(f"The report couldn't be built: {exc}")
    right.download_button(
        "Download raw data",
        data=generate_json_report(session).encode("utf-8"),
        file_name=report_filename(session, "json"),
        mime="application/json",
        width="stretch",
    )


def _history(settings, manager: HistoryManager, entries) -> None:
    if not entries:
        ui.empty_state(
            "No saved reviews yet",
            "Every review you run is saved here automatically, with the code you sent.",
        )
        return

    st.caption(f"Saved on this computer in {manager.directory.name}/. Newest first.")

    for entry in entries:
        verdict, tone = VERDICT_WORDS.get(entry.verification, (entry.verification, "neutral"))
        try:
            task = INTENT_LABELS[Intent(entry.intent)]
        except ValueError:
            task = entry.intent

        with st.expander(f"{entry.filename}  ·  {entry.created_display}  ·  {verdict}"):
            loaded = manager.load(entry.session_id)
            if loaded is None:
                ui.note("This saved review couldn't be read back.", "warn")
                continue

            ui.chip_row(
                [
                    ui.chip(verdict, tone),
                    ui.chip(task, "accent"),
                    ui.chip(f"{entry.issues} issue(s)", "neutral"),
                ]
            )
            if loaded.user_request:
                st.markdown(f"**You asked:** {loaded.user_request}")

            st.markdown("**The code you ran**")
            st.code(loaded.original_code, language="python", line_numbers=True)

            if loaded.final_fix and loaded.final_fix.unified_diff:
                st.markdown("**What the Fixer changed**")
                st.code(loaded.final_fix.unified_diff, language="diff")

            a, b, c, d = st.columns(4)
            if a.button("Open this review", key=f"open_{entry.session_id}", width="stretch"):
                st.session_state["session"] = loaded
                st.rerun()
            if b.button("Edit this code", key=f"edit_{entry.session_id}", width="stretch"):
                st.session_state["code"] = loaded.original_code
                st.session_state["filename"] = loaded.filename
                st.session_state["user_request"] = loaded.user_request
                st.session_state["session"] = loaded
                goto(PAGE_WORKSPACE)
            try:
                c.download_button(
                    "Report",
                    data=generate_html_report(
                        loaded, max_attempts=settings.max_fix_attempts
                    ).encode("utf-8"),
                    file_name=report_filename(loaded, "html"),
                    mime="text/html",
                    key=f"html_{entry.session_id}",
                    width="stretch",
                )
            except Exception as exc:  # noqa: BLE001
                c.error(f"Report failed: {exc}")
            if d.button("Delete", key=f"del_{entry.session_id}", width="stretch"):
                manager.delete(entry.session_id)
                current = st.session_state.get("session")
                if current is not None and current.session_id == entry.session_id:
                    st.session_state["session"] = None
                st.rerun()
