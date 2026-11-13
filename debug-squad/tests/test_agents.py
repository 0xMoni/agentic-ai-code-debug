"""The four agents' structured outputs and deterministic behaviour."""

from __future__ import annotations

import json

import pytest

from agents.base import check_syntax, top_level_callables
from agents.bug_finder import BugFinderAgent
from agents.fixer_agent import FixerAgent, make_unified_diff, summarise_failures
from agents.style_agent import SUBMITTED_CODE_RULES, StyleAgent, run_ruff
from agents.test_writer import TestWriterAgent
from models.schemas import (
    AgentStatus,
    BugReport,
    FindingSource,
    Intent,
    ReviewSession,
    Severity,
    TestOutcome,
    TestRunResult,
)
from tests.conftest import bug_report_json, fix_json, generated_tests_json, style_json

FIND_MAX = """def find_max(nums):
    maximum = 0
    for num in nums:
        if num > maximum:
            maximum = num
    return maximum
"""

FIXED_FIND_MAX = """def find_max(nums):
    if not nums:
        raise ValueError("find_max() received an empty sequence")
    maximum = nums[0]
    for num in nums:
        if num > maximum:
            maximum = num
    return maximum
"""


def session(code: str = FIND_MAX) -> ReviewSession:
    return ReviewSession(session_id="test", original_code=code, filename="submitted.py")


def test_syntax_check_accepts_valid_code():
    assert check_syntax(FIND_MAX) == (True, None)


def test_syntax_check_reports_line_and_column():
    ok, error = check_syntax("def f(:\n    pass")
    assert ok is False
    assert "line 1" in error


def test_top_level_callables_skips_private_names():
    code = "def public():\n    pass\ndef _private():\n    pass\nclass Thing:\n    pass\n"
    assert top_level_callables(code) == ["public", "Thing"]


def test_top_level_callables_on_unparseable_code():
    assert top_level_callables("def f(:") == []


def test_bug_finder_reports_syntax_errors_without_calling_the_llm(
    settings, patch_llm, no_retrieval
):
    model = patch_llm(bug_report_json())
    agent = BugFinderAgent(session("def f(:\n    pass"), settings)
    report = agent.run("def f(:\n    pass", Intent.FULL_REVIEW)

    assert report.syntax_valid is False
    assert report.issues[0].source is FindingSource.AST
    assert report.issues[0].severity is Severity.CRITICAL
    assert model.call_count == 0, "no LLM call should be made for unparseable code"
    assert "ast.parse" in agent.trace.tools_used


def test_bug_finder_parses_structured_findings(settings, patch_llm, no_retrieval):
    patch_llm(bug_report_json(2))
    agent = BugFinderAgent(session(), settings)
    report = agent.run(FIND_MAX, Intent.FULL_REVIEW)

    assert len(report.issues) == 2
    assert report.issues[0].issue_id == "BUG-01"
    assert all(i.source is FindingSource.LLM for i in report.issues)
    assert agent.trace.status is AgentStatus.COMPLETED
    assert agent.trace.findings_count == 2
    assert agent.trace.techniques[0].value == "few_shot"


def test_bug_finder_accepts_an_empty_issue_list(settings, patch_llm, no_retrieval):
    patch_llm(json.dumps({"issues": [], "summary": "Looks correct.", "syntax_valid": True}))
    report = BugFinderAgent(session(), settings).run(FIND_MAX, Intent.FULL_REVIEW)
    assert report.issues == []
    assert "correct" in report.summary.lower()


def test_bug_finder_normalises_nonsense_line_numbers(settings, patch_llm, no_retrieval):
    payload = json.loads(bug_report_json())
    payload["issues"][0]["line"] = 0
    patch_llm(json.dumps(payload))
    report = BugFinderAgent(session(), settings).run(FIND_MAX, Intent.FULL_REVIEW)
    assert report.issues[0].line is None


def test_ruff_runs_and_returns_real_rule_codes(settings):
    issues, error = run_ruff("def calc(a,b):\n    x=a+b\n    return x\n",
                             settings.python_executable)
    assert error is None
    codes = {issue.rule_code for issue in issues}
    assert "E231" in codes, "missing whitespace after comma should be detected"
    assert "RET504" in codes
    assert all(issue.source is FindingSource.RUFF for issue in issues)
    assert all(issue.confidence.value == "high" for issue in issues)


def test_ruff_reports_nothing_for_clean_code(settings):
    clean = '''def add(first: int, second: int) -> int:
    """Return the sum of two integers."""
    return first + second
'''
    issues, error = run_ruff(clean, settings.python_executable)
    assert error is None
    assert issues == []


def test_ruff_rule_selection_is_explicit():
    assert "N" in SUBMITTED_CODE_RULES, "naming rules matter for a style review"
    assert "F" in SUBMITTED_CODE_RULES


def test_style_agent_separates_ruff_facts_from_llm_opinion(
    settings, patch_llm, no_retrieval
):
    patch_llm(style_json())
    agent = StyleAgent(session(), settings)
    report = agent.run("def calc(a,b):\n    x=a+b\n    return x\n", Intent.STYLE_IMPROVEMENT)

    assert report.ruff_available is True
    assert report.ruff_issues, "Ruff findings must be present"
    assert report.suggestions, "LLM suggestions must be present"
    assert all(i.source is FindingSource.RUFF for i in report.ruff_issues)
    assert "ruff" in agent.trace.tools_used


def test_style_agent_degrades_to_ruff_only_when_the_llm_fails(
    settings, patch_llm, no_retrieval
):
    patch_llm("not json at all", "still not json")
    agent = StyleAgent(session(), settings)
    report = agent.run("def calc(a,b):\n    x=a+b\n", Intent.STYLE_IMPROVEMENT)

    assert report.ruff_issues, "Ruff findings survive an LLM failure"
    assert report.suggestions == []
    assert "could not be completed" in report.summary
    assert agent.trace.status is AgentStatus.COMPLETED


def test_unified_diff_is_generated_deterministically():
    diff = make_unified_diff(FIND_MAX, FIXED_FIND_MAX, "submitted.py")
    assert diff.startswith("--- a/submitted.py")
    assert "-    maximum = 0" in diff
    assert "+    maximum = nums[0]" in diff


def test_identical_code_produces_an_empty_diff():
    assert make_unified_diff(FIND_MAX, FIND_MAX, "f.py") == ""


def test_fixer_produces_a_correction_and_diff(settings, patch_llm, no_retrieval):
    patch_llm(fix_json(FIXED_FIND_MAX))
    agent = FixerAgent(session(), settings)
    report = BugReport(**json.loads(bug_report_json()))
    result = agent.run(FIND_MAX, report, None, Intent.DEBUGGING)

    assert result.syntax_valid is True
    assert "nums[0]" in result.corrected_code
    assert result.unified_diff
    assert result.addressed_issue_ids == ["BUG-01"]
    assert result.attempt == 1


def test_fixer_rejects_a_correction_that_does_not_parse(
    settings, patch_llm, no_retrieval
):
    patch_llm(fix_json("def find_max(nums:\n    return 1"))
    agent = FixerAgent(session(), settings)
    report = BugReport(**json.loads(bug_report_json()))
    result = agent.run(FIND_MAX, report, None, Intent.DEBUGGING)

    assert result.syntax_valid is False
    assert result.corrected_code == FIND_MAX, "the original must be preserved"
    assert result.unified_diff == ""
    assert any("not valid Python" in a for a in result.ambiguities)


def test_fixer_makes_no_changes_when_there_are_no_findings(
    settings, patch_llm, no_retrieval
):
    model = patch_llm(fix_json(FIXED_FIND_MAX))
    agent = FixerAgent(session(), settings)
    result = agent.run(FIND_MAX, BugReport(), None, Intent.FULL_REVIEW)

    assert result.corrected_code == FIND_MAX
    assert result.changes == []
    assert model.call_count == 0, "nothing to fix means no model call"


def test_fixer_does_not_retrieve_from_the_knowledge_base(settings, patch_llm):
    """The Fixer works from findings; claiming RAG here would be dishonest."""
    from models.schemas import AgentName
    from rag.retriever import RETRIEVAL_POLICY

    for intent in (Intent.FULL_REVIEW, Intent.DEBUGGING, Intent.STYLE_IMPROVEMENT):
        assert RETRIEVAL_POLICY[(intent, AgentName.FIXER)] is None


def test_failure_summary_includes_the_real_pytest_message():
    run = TestRunResult(**json.loads(json.dumps({
        "attempt": 1,
        "tests": [{
            "test_id": "T1", "name": "test_negatives", "category": "boundary",
            "description": "all-negative input", "code": "def test_negatives(): pass",
            "expected_behaviour": "returns -2", "oracle": "definition of maximum",
            "outcome": "failed",
            "failure_message": "E   assert 0 == -2",
        }],
    })))
    summary = summarise_failures(run)
    assert "test_negatives" in summary
    assert "assert 0 == -2" in summary
    assert "definition of maximum" in summary


def test_test_writer_generates_and_runs_tests(settings, patch_llm, no_retrieval):
    patch_llm(generated_tests_json([
        ("test_positive", "def test_positive():\n    from submitted_code import find_max\n    assert find_max([1, 5, 3]) == 5\n"),
        ("test_all_negative", "def test_all_negative():\n    from submitted_code import find_max\n    assert find_max([-5, -2]) == -2\n"),
    ]))
    agent = TestWriterAgent(session(), settings)
    run = agent.run(FIND_MAX, Intent.TEST_GENERATION)

    assert run.generated == 2
    if run.sandbox.executed:
        assert run.passed == 1
        assert run.failed == 1
        assert run.pass_rate == pytest.approx(50.0)
        failing = [t for t in run.tests if t.outcome is TestOutcome.FAILED]
        assert failing[0].name == "test_all_negative"
        assert "assert" in (failing[0].failure_message or "")
    else:
        assert all(t.outcome is TestOutcome.NOT_EXECUTED for t in run.tests)
        assert run.passed == 0
    assert "sandbox" in agent.trace.tools_used


def test_test_writer_discards_entries_with_no_test_function(
    settings, patch_llm, no_retrieval
):
    patch_llm(generated_tests_json([
        ("test_real", "def test_real():\n    assert True\n"),
        ("not_a_test", "x = 1\n"),
    ]))
    agent = TestWriterAgent(session(), settings)
    tests, _source = agent.generate(FIND_MAX, Intent.TEST_GENERATION)
    assert [t.name for t in tests] == ["test_real"]


def test_unexecuted_tests_are_never_counted_as_passed(settings, patch_llm, no_retrieval):
    patch_llm(generated_tests_json([("test_x", "def test_x():\n    assert True\n")]))
    agent = TestWriterAgent(session(), settings)
    agent.executor.backend.available = False
    agent.executor.backend.description = "no backend in this test"
    run = agent.run(FIND_MAX, Intent.TEST_GENERATION)

    assert run.sandbox.executed is False
    assert run.passed == 0
    assert run.executed == 0
    assert run.all_passed is False
    assert run.pass_rate is None
    assert all(t.outcome is TestOutcome.NOT_EXECUTED for t in run.tests)
    assert "NOT executed" in agent.trace.summary
