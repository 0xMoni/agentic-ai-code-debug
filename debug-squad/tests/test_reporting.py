"""Report generation and history persistence."""

from __future__ import annotations

import json

import pytest

from models.schemas import (
    AgentName,
    AgentStatus,
    AgentTrace,
    BugReport,
    Confidence,
    FindingSource,
    FixAttempt,
    FixResult,
    Intent,
    IntentDecision,
    Issue,
    ReviewSession,
    SandboxResult,
    Severity,
    StyleReport,
    TestCase,
    TestOutcome,
    TestRunResult,
    VerificationStatus,
)
from services.history_manager import HistoryManager
from services.report_generator import (
    generate_html_report,
    generate_json_report,
    report_filename,
)


def full_session(verification=VerificationStatus.VERIFIED) -> ReviewSession:
    return ReviewSession(
        session_id="abc123",
        filename="submitted.py",
        original_code="def find_max(nums):\n    maximum = 0\n    return maximum\n",
        user_request="review and fix everything",
        provider="groq",
        model="test-model",
        intent=IntentDecision(
            intent=Intent.FULL_REVIEW,
            confidence=Confidence.HIGH,
            method="llm_zero_shot",
            explanation="asked for a full review",
            agents=[AgentName.BUG_FINDER, AgentName.FIXER],
        ),
        bug_report=BugReport(
            issues=[
                Issue(
                    issue_id="BUG-01",
                    title="maximum starts at 0",
                    severity=Severity.HIGH,
                    line=2,
                    explanation="Wrong for negative input.",
                    reproduction="find_max([-5]) returns 0",
                    suggested_fix="Seed from the first element.",
                    confidence=Confidence.HIGH,
                )
            ],
            summary="One defect.",
        ),
        style_report=StyleReport(
            ruff_issues=[
                Issue(
                    issue_id="STYLE-01",
                    title="Missing whitespace after comma",
                    severity=Severity.INFO,
                    line=1,
                    explanation="E231 fired.",
                    source=FindingSource.RUFF,
                    rule_code="E231",
                )
            ],
            summary="One lint finding.",
        ),
        final_fix=FixResult(
            corrected_code="def find_max(nums):\n    return max(nums)\n",
            changes=["Use the builtin max()."],
            unified_diff="--- a/submitted.py\n+++ b/submitted.py\n@@ -1,3 +1,2 @@\n-    maximum = 0\n+    return max(nums)\n",
            addressed_issue_ids=["BUG-01"],
        ),
        final_test_run=TestRunResult(
            attempt=1,
            tests=[
                TestCase(
                    test_id="T1",
                    name="test_negatives",
                    category="boundary",
                    description="all-negative input",
                    code="def test_negatives():\n    assert True\n",
                    expected_behaviour="returns -2",
                    oracle="definition of maximum",
                    outcome=TestOutcome.PASSED,
                )
            ],
            sandbox=SandboxResult(
                executed=True, backend="seatbelt", exit_code=0,
                stdout="1 passed in 0.03s", duration_seconds=0.4,
            ),
            generated=1, executed=1, passed=1,
            test_file_source="def test_negatives():\n    assert True\n",
        ),
        traces=[
            AgentTrace(
                agent=AgentName.BUG_FINDER,
                status=AgentStatus.COMPLETED,
                duration_seconds=1.2,
                findings_count=1,
                summary="one defect",
                tools_used=["ast.parse"],
            ),
            AgentTrace(
                agent=AgentName.STYLE_LINT,
                status=AgentStatus.SKIPPED,
                skip_reason="not part of this workflow",
            ),
        ],
        verification=verification,
        overall_assessment="All 1 executed test(s) passed.",
        limitations=["Passing tests are evidence, not proof."],
    )


def test_report_covers_all_thirteen_required_sections():
    html = generate_html_report(full_session())
    for heading in [
        "Submitted code",
        "Detected intent and routing",
        "Agents invoked",
        "Bug findings",
        "Style findings",
        "Corrected code",
        "Code changes",
        "Generated test cases",
        "Actual execution results",
        "Reflection attempts",
        "Retrieved documentation",
        "Final verification status",
        "Remaining issues and limitations",
    ]:
        assert heading in html, f"missing section: {heading}"


def test_report_is_self_contained_html():
    html = generate_html_report(full_session())
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "src=\"http" not in html, "the report must not depend on remote assets"


def test_report_includes_the_real_findings_and_results():
    html = generate_html_report(full_session())
    assert "maximum starts at 0" in html
    assert "E231" in html
    assert "test_negatives" in html
    assert "definition of maximum" in html
    assert "seatbelt" in html


def test_report_escapes_html_in_user_code():
    session = full_session()
    session.original_code = "x = '<script>alert(1)</script>'\n"
    html = generate_html_report(session)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_report_states_plainly_when_nothing_was_verified():
    session = full_session(VerificationStatus.NOT_VERIFIED)
    session.final_test_run.sandbox.executed = False
    session.final_test_run.sandbox.unavailable_reason = "no sandbox backend"
    session.final_test_run.passed = 0
    session.final_test_run.executed = 0
    html = generate_html_report(session)
    assert "not executed" in html.lower()
    assert "no sandbox backend" in html
    assert "neither confirmed nor refuted" in html


def test_failed_verification_says_so():
    html = generate_html_report(full_session(VerificationStatus.FAILED))
    assert "should not be treated as working" in html


def test_report_always_includes_the_evidence_caveat():
    for status in VerificationStatus:
        html = generate_html_report(full_session(status))
        assert "not a proof of correctness" in html


def test_diff_is_colourised_and_escaped():
    session = full_session()
    session.final_fix.unified_diff = "-    x = '<b>'\n+    y = 2\n"
    html = generate_html_report(session)
    assert 'class="del"' in html
    assert 'class="add"' in html
    assert "&lt;b&gt;" in html


def test_reflection_table_reflects_real_attempts():
    session = full_session()
    session.attempts = [
        FixAttempt(
            attempt=1, fix=session.final_fix,
            test_run=TestRunResult(attempt=1, executed=2, passed=1, failed=1),
            verified=False, reflection_notes="revised after reading the failure",
        ),
        FixAttempt(
            attempt=2, fix=session.final_fix,
            test_run=TestRunResult(attempt=2, executed=2, passed=2),
            verified=True,
        ),
    ]
    html = generate_html_report(session, max_attempts=3)
    assert "2 attempt(s) were made, with a configured maximum of 3" in html
    assert "revised after reading the failure" in html


def test_json_report_round_trips():
    payload = json.loads(generate_json_report(full_session()))
    assert payload["session_id"] == "abc123"
    assert payload["bug_report"]["issues"][0]["issue_id"] == "BUG-01"


def test_report_filename_is_safe_and_dated():
    name = report_filename(full_session())
    assert name.startswith("debug-squad-review-")
    assert name.endswith(".html")
    assert "/" not in name and " " not in name


def test_history_saves_and_reloads_a_session(settings):
    manager = HistoryManager(settings)
    manager.directory = settings.project_root / "history-test-tmp"
    try:
        path = manager.save(full_session())
        assert path.exists()

        entries = manager.list_entries()
        assert len(entries) == 1
        assert entries[0].session_id == "abc123"
        assert entries[0].verification == "Verified"
        assert entries[0].issues == 2

        reloaded = manager.load("abc123")
        assert reloaded is not None
        assert reloaded.bug_report.issues[0].title == "maximum starts at 0"
        assert reloaded.final_test_run.passed == 1

        assert manager.delete("abc123") is True
        assert manager.load("abc123") is None
    finally:
        import shutil
        shutil.rmtree(manager.directory, ignore_errors=True)


def test_history_refuses_to_write_a_secret(settings):
    manager = HistoryManager(settings)
    manager.directory = settings.project_root / "history-test-tmp2"
    session = full_session()
    session.user_request = f"my key is {settings.groq_api_key}"
    try:
        with pytest.raises(ValueError, match="API key"):
            manager.save(session)
        assert not list(manager.directory.glob("*.json")) if manager.directory.exists() else True
    finally:
        import shutil
        shutil.rmtree(manager.directory, ignore_errors=True)


def test_history_skips_corrupt_files(settings, tmp_path):
    manager = HistoryManager(settings)
    manager.directory = tmp_path / "history"
    manager.directory.mkdir()
    (manager.directory / "review-broken.json").write_text("{not json")
    manager.save(full_session())
    entries = manager.list_entries()
    assert [e.session_id for e in entries] == ["abc123"]


def test_empty_history_is_not_an_error(settings, tmp_path):
    manager = HistoryManager(settings)
    manager.directory = tmp_path / "does-not-exist"
    assert manager.list_entries() == []
    assert manager.count() == 0


def test_history_guard_ignores_implausibly_short_keys(settings, tmp_path):
    """A one-character placeholder key must not block every save."""
    from config.settings import Settings

    short_key = Settings(provider="groq", groq_api_key="k", project_root=settings.project_root)
    manager = HistoryManager(short_key)
    manager.directory = tmp_path / "history"
    path = manager.save(full_session())
    assert path.exists()


def test_history_still_refuses_a_realistic_key(settings, tmp_path):
    from config.settings import Settings

    real_looking = "gsk_" + "a" * 48
    configured = Settings(
        provider="groq", groq_api_key=real_looking, project_root=settings.project_root
    )
    manager = HistoryManager(configured)
    manager.directory = tmp_path / "history"
    session = full_session()
    session.user_request = f"my key is {real_looking}"
    with pytest.raises(ValueError, match="API key"):
        manager.save(session)
