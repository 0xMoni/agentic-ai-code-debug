"""Test lab - the generated tests, what really ran, and how the fix got there."""

from __future__ import annotations

import html

import streamlit as st

from models.schemas import TestOutcome
from ui import components as ui
from ui.state import backend, require_session

_OUTCOME_TONE = {
    TestOutcome.PASSED: "ok",
    TestOutcome.FAILED: "bad",
    TestOutcome.ERROR: "bad",
    TestOutcome.SKIPPED: "warn",
    TestOutcome.NOT_EXECUTED: "neutral",
}


def render(settings) -> None:
    ui.page_header(
        "Test lab",
        "The tests that were generated, what the sandbox actually did with them, "
        "and the reflection timeline if there was one.",
    )

    sandbox = backend(settings)
    ui.chip_row(
        [
            ui.chip(f"Backend: {sandbox.name}", "ok" if sandbox.is_adequate else
                    ("warn" if sandbox.available else "bad")),
            ui.chip(
                "Network denied" if sandbox.network_blocked else "Network NOT blocked",
                "ok" if sandbox.network_blocked else "bad",
            ),
            ui.chip(
                "Filesystem confined" if sandbox.filesystem_confined
                else "Filesystem NOT confined",
                "ok" if sandbox.filesystem_confined else "bad",
            ),
            ui.chip(f"Timeout {settings.sandbox_timeout_seconds}s", "neutral"),
            ui.chip(f"CPU {settings.sandbox_cpu_seconds}s", "neutral"),
            ui.chip(f"Memory {settings.sandbox_memory_mb}MB", "neutral"),
        ]
    )

    if not sandbox.is_adequate:
        ui.note(
            f"<b>{html.escape(sandbox.badge)}.</b> "
            f"{html.escape(sandbox.description)}"
            + (
                "<br>Tests will be reported as <b>Not Executed</b> rather than "
                "assumed to pass."
                if not sandbox.available
                else ""
            ),
            "warn" if sandbox.available else "bad",
        )

    if not require_session("No tests have been run yet."):
        return

    session = st.session_state["session"]
    run = session.final_test_run

    if run is None:
        ui.empty_state(
            "This workflow did not generate tests.",
            "Test generation runs for a full review or a test-generation request.",
        )
        return

    rate = run.pass_rate
    ui.facts(
        [
            (run.generated, "written"),
            (run.executed, "actually run"),
            (run.passed, "passed"),
            (run.failed, "failed"),
            (run.skipped, "skipped"),
            (f"{rate:.0f}%" if rate is not None else "—", "pass rate"),
        ]
    )

    if not run.sandbox.executed:
        ui.note(
            f"<b>These tests were NOT executed.</b><br>"
            f"{html.escape(run.sandbox.unavailable_reason or 'The sandbox was unavailable.')}"
            "<br>No claim is made about whether they would have passed.",
            "warn",
        )
    elif run.sandbox.timed_out:
        ui.note(f"<b>Resource limit hit.</b><br>{html.escape(run.sandbox.stderr)}", "bad")

    if session.attempts:
        st.markdown("### How the fix got there")
        stops = []
        for attempt in session.attempts:
            if not attempt.test_run.sandbox.executed:
                state, label = "skipped", f"Try {attempt.attempt}: not run"
            elif attempt.verified:
                state, label = "completed", f"Try {attempt.attempt}: all passed"
            else:
                state, label = "failed", f"Try {attempt.attempt}: {attempt.test_run.failed} failed"
            colour = "var(--ok)" if state == "completed" else "var(--bad)"
            stops.append(
                f'<span class="stop {state}" style="--agent:{colour}">{html.escape(label)}</span>'
            )
            if attempt.reflection_notes:
                stops.append(
                    '<span class="stop completed" style="--agent:var(--fixer)">'
                    "Fixer read the failures and revised</span>"
                )
        st.markdown(
            f'<div class="ds-trail">{"<span class=arrow>→</span>".join(stops)}</div>',
            unsafe_allow_html=True,
        )

        if session.attempt_count >= settings.max_fix_attempts and not run.all_passed:
            ui.note(
                f"<b>The loop stopped at the configured maximum of "
                f"{settings.max_fix_attempts} attempt(s).</b> Remaining failures "
                "were not fixed, and the review says so rather than claiming success.",
                "warn",
            )

    st.markdown("---")
    st.markdown("### Generated test cases")

    if not run.tests:
        ui.empty_state("No usable tests were generated for this code.")
    for test in run.tests:
        tone = _OUTCOME_TONE[test.outcome]
        label = ui.OUTCOME_LABELS[test.outcome]
        with st.expander(
            f"{test.name} — {label}  ·  {test.category}",
            expanded=test.outcome in (TestOutcome.FAILED, TestOutcome.ERROR),
        ):
            ui.chip_row([ui.chip(label, tone), ui.chip(test.category, "accent")])
            ui.kv_block(
                [
                    ("What it checks", test.description),
                    ("Expected behaviour", test.expected_behaviour),
                    ("Oracle (where the expectation came from)", test.oracle),
                ]
            )
            st.markdown("**Test source**")
            st.code(test.code, language="python")
            if test.failure_message:
                st.markdown("**Actual output from pytest**")
                st.code(test.failure_message, language="text")

    st.markdown("---")
    st.markdown("### Raw execution evidence")
    rows = [
        ("Sandbox backend", run.sandbox.backend),
        ("Executed", "yes" if run.sandbox.executed else "no"),
        (
            "Exit code",
            str(run.sandbox.exit_code) if run.sandbox.exit_code is not None else "n/a",
        ),
        ("Duration", f"{run.sandbox.duration_seconds:.2f}s"),
        ("Killed by a limit", "yes" if run.sandbox.timed_out else "no"),
    ]
    ui.kv_block(rows, mono=True)

    if run.sandbox.stdout:
        with st.expander("pytest stdout", expanded=True):
            st.code(run.sandbox.stdout, language="text")
    if run.sandbox.stderr:
        with st.expander("stderr", expanded=False):
            st.code(run.sandbox.stderr, language="text")

    with st.expander("The exact test module that was executed", expanded=False):
        st.code(run.test_file_source, language="python")
        st.download_button(
            "Download the test module",
            data=run.test_file_source.encode("utf-8"),
            file_name=f"test_{session.filename}",
            mime="text/x-python",
        )
