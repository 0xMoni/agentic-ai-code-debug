"""Workflow orchestration and the reflection loop."""

from __future__ import annotations

import json

from models.schemas import (
    AgentName,
    AgentStatus,
    Intent,
    VerificationStatus,
)
from services.review_orchestrator import ReviewOrchestrator
from tests.conftest import (
    bug_report_json,
    fix_json,
    generated_tests_json,
    intent_json,
    style_json,
)

FIND_MAX = """def find_max(nums):
    maximum = 0
    for num in nums:
        if num > maximum:
            maximum = num
    return maximum
"""

GOOD_FIX = """def find_max(nums):
    maximum = nums[0]
    for num in nums:
        if num > maximum:
            maximum = num
    return maximum
"""

BAD_FIX = """def find_max(nums):
    maximum = 0
    for num in nums:
        if num > maximum:
            maximum = num
    return maximum
"""

NEGATIVE_TEST = generated_tests_json([
    (
        "test_all_negative",
        "def test_all_negative():\n"
        "    from submitted_code import find_max\n"
        "    assert find_max([-5, -2]) == -2\n",
    ),
])

PASSING_TEST = generated_tests_json([
    ("test_trivial", "def test_trivial():\n    assert True\n"),
])


def status_of(session, agent: AgentName) -> AgentStatus:
    trace = session.trace_for(agent)
    assert trace is not None, f"{agent} has no trace"
    return trace.status


def test_explanation_intent_runs_only_the_explainer(settings, patch_llm, no_retrieval):
    patch_llm(intent_json("code_explanation"), "## It returns the largest number")
    session = ReviewOrchestrator(settings).run(FIND_MAX, "explain this code")

    assert session.intent.intent is Intent.CODE_EXPLANATION
    assert session.explanation.startswith("## It returns")
    assert status_of(session, AgentName.EXPLAINER) is AgentStatus.COMPLETED
    for skipped in (
        AgentName.BUG_FINDER,
        AgentName.STYLE_LINT,
        AgentName.FIXER,
        AgentName.TEST_WRITER,
    ):
        assert status_of(session, skipped) is AgentStatus.SKIPPED
        assert session.trace_for(skipped).skip_reason
    assert session.bug_report is None
    assert session.final_fix is None
    assert session.verification is VerificationStatus.NOT_VERIFIED


def test_explanation_never_retrieves(settings, patch_llm):
    patch_llm(intent_json("code_explanation"), "## explanation")
    session = ReviewOrchestrator(settings).run(FIND_MAX, "explain this")
    explainer_retrievals = [
        r for r in session.retrievals if r.agent is AgentName.EXPLAINER
    ]
    assert explainer_retrievals
    assert all(not r.should_retrieve for r in explainer_retrievals)
    assert all(r.passages == [] for r in explainer_retrievals)


def test_style_intent_skips_the_bug_finder(settings, patch_llm, no_retrieval):
    patch_llm(intent_json("style_improvement"), style_json(), fix_json(GOOD_FIX))
    session = ReviewOrchestrator(settings).run(FIND_MAX, "improve readability")

    assert status_of(session, AgentName.STYLE_LINT) is AgentStatus.COMPLETED
    assert status_of(session, AgentName.FIXER) is AgentStatus.COMPLETED
    assert status_of(session, AgentName.BUG_FINDER) is AgentStatus.SKIPPED
    assert status_of(session, AgentName.TEST_WRITER) is AgentStatus.SKIPPED
    assert session.bug_report is None


def test_manual_intent_overrides_detection(settings, patch_llm, no_retrieval):
    model = patch_llm(style_json(), fix_json(GOOD_FIX))
    session = ReviewOrchestrator(settings).run(
        FIND_MAX,
        "find the bug",
        auto_intent=False,
        manual_intent=Intent.STYLE_IMPROVEMENT,
    )
    assert session.intent.intent is Intent.STYLE_IMPROVEMENT
    assert session.intent.method == "manual_override"
    assert status_of(session, AgentName.BUG_FINDER) is AgentStatus.SKIPPED
    assert model.call_count >= 1


def test_syntax_error_stops_the_workflow_early(settings, patch_llm, no_retrieval):
    patch_llm(intent_json("full_review"))
    session = ReviewOrchestrator(settings).run("def f(:\n    pass", "review everything")

    assert session.bug_report.syntax_valid is False
    assert session.verification is VerificationStatus.FAILED
    assert "not valid Python" in session.overall_assessment
    for skipped in (AgentName.STYLE_LINT, AgentName.FIXER, AgentName.TEST_WRITER):
        assert status_of(session, skipped) is AgentStatus.SKIPPED


def test_empty_submission_is_refused_cleanly(settings):
    session = ReviewOrchestrator(settings).run("   \n  ", "review this")
    assert "empty" in session.overall_assessment.lower()
    assert session.verification is VerificationStatus.NOT_VERIFIED
    assert session.errors


def test_verified_only_when_tests_really_pass(settings, patch_llm, no_retrieval):
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(GOOD_FIX),
        NEGATIVE_TEST,
    )
    session = ReviewOrchestrator(settings).run(FIND_MAX, "review and fix everything")

    run = session.final_test_run
    assert run is not None
    if not run.sandbox.executed:
        assert session.verification is VerificationStatus.NOT_VERIFIED
        return

    assert run.all_passed is True
    assert session.verification is VerificationStatus.VERIFIED
    assert session.attempt_count == 1, "a passing first attempt must not loop"
    assert "passed" in session.overall_assessment


def test_reflection_loop_stops_at_max_attempts(settings, patch_llm, no_retrieval):
    """A Fixer that never actually fixes anything must not loop forever."""
    settings.max_fix_attempts = 3
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(BAD_FIX),
        NEGATIVE_TEST,
        fix_json(BAD_FIX),
        fix_json(BAD_FIX),
    )
    session = ReviewOrchestrator(settings).run(FIND_MAX, "review and fix everything")

    run = session.final_test_run
    if not run.sandbox.executed:
        assert session.verification is VerificationStatus.NOT_VERIFIED
        return

    assert session.attempt_count == 3, "must stop at exactly MAX_FIX_ATTEMPTS"
    assert session.verification is VerificationStatus.FAILED
    assert "NOT verified" in " ".join(session.limitations)
    assert "still fail" in session.overall_assessment

    reflection_prompts = [
        p for p in session.prompts if p.technique.value == "reflection"
    ]
    assert len(reflection_prompts) == 2, "two reflections between three attempts"
    assert "assert" in reflection_prompts[0].rendered_user_prompt, (
        "the reflection prompt must contain the real pytest output"
    )


def test_a_lower_attempt_limit_is_respected(settings, patch_llm, no_retrieval):
    settings.max_fix_attempts = 1
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(BAD_FIX),
        NEGATIVE_TEST,
    )
    session = ReviewOrchestrator(settings).run(FIND_MAX, "review everything")
    if not session.final_test_run.sandbox.executed:
        return
    assert session.attempt_count == 1
    assert session.verification is VerificationStatus.FAILED
    assert not [p for p in session.prompts if p.technique.value == "reflection"]


def test_reflection_fixes_it_on_the_second_attempt(settings, patch_llm, no_retrieval):
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(BAD_FIX),
        NEGATIVE_TEST,
        fix_json(GOOD_FIX),
    )
    session = ReviewOrchestrator(settings).run(FIND_MAX, "review and fix everything")
    if not session.final_test_run.sandbox.executed:
        return

    assert session.attempt_count == 2
    assert session.verification is VerificationStatus.VERIFIED
    assert session.attempts[0].verified is False
    assert session.attempts[1].verified is True
    assert session.attempts[0].reflection_notes
    assert "nums[0]" in session.final_fix.corrected_code


def test_tests_are_not_regenerated_between_attempts(settings, patch_llm, no_retrieval):
    """Fixed tests are what make a later pass meaningful."""
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(BAD_FIX),
        NEGATIVE_TEST,
        fix_json(GOOD_FIX),
    )
    session = ReviewOrchestrator(settings).run(FIND_MAX, "review everything")
    if not session.final_test_run.sandbox.executed:
        return
    sources = {attempt.test_run.test_file_source for attempt in session.attempts}
    assert len(sources) == 1, "the same test module must be reused across attempts"


def test_unavailable_sandbox_yields_not_verified(
    settings, patch_llm, no_retrieval, monkeypatch
):
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(GOOD_FIX),
        PASSING_TEST,
    )
    orchestrator = ReviewOrchestrator(settings)
    orchestrator.executor.backend.available = False
    orchestrator.executor.backend.description = "no sandbox available in this test"

    session = orchestrator.run(FIND_MAX, "review and fix everything")

    assert session.final_test_run.sandbox.executed is False
    assert session.final_test_run.passed == 0
    assert session.verification is VerificationStatus.NOT_VERIFIED, (
        "an unrun test suite is neither a pass nor a fail"
    )
    assert any("never executed" in item for item in session.limitations)
    assert "NOT executed" in session.overall_assessment
    assert session.attempt_count == 1, "no point reflecting when nothing ran"


def test_verified_sessions_still_state_their_limits(settings, patch_llm, no_retrieval):
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(GOOD_FIX),
        PASSING_TEST,
    )
    session = ReviewOrchestrator(settings).run(FIND_MAX, "review everything")
    if session.verification is not VerificationStatus.VERIFIED:
        return
    assert any("evidence, not proof" in item for item in session.limitations)


def test_every_llm_call_is_recorded_for_the_inspector(
    settings, patch_llm, no_retrieval
):
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(GOOD_FIX),
        PASSING_TEST,
    )
    session = ReviewOrchestrator(settings).run(FIND_MAX, "review everything")

    assert session.prompts, "no prompts were recorded"
    for record in session.prompts:
        assert record.system_prompt
        assert record.user_prompt_template
        assert record.lcel_pipeline
        assert record.model
        assert settings.groq_api_key not in record.rendered_user_prompt
        assert settings.groq_api_key not in record.system_prompt

    techniques = {record.technique.value for record in session.prompts}
    assert "zero_shot" in techniques
    assert "few_shot" in techniques


def test_traces_record_real_durations_and_tools(settings, patch_llm, no_retrieval):
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(GOOD_FIX),
        PASSING_TEST,
    )
    session = ReviewOrchestrator(settings).run(FIND_MAX, "review everything")

    completed = [t for t in session.traces if t.status is AgentStatus.COMPLETED]
    assert completed
    for trace in completed:
        assert trace.duration_seconds >= 0
        assert trace.started_at is not None
        assert trace.events, f"{trace.agent} logged no events"

    style = session.trace_for(AgentName.STYLE_LINT)
    assert "ruff" in style.tools_used
    bug = session.trace_for(AgentName.BUG_FINDER)
    assert "ast.parse" in bug.tools_used


def test_session_serialises_without_secrets(settings, patch_llm, no_retrieval):
    patch_llm(intent_json("code_explanation"), "## explanation")
    session = ReviewOrchestrator(settings).run(FIND_MAX, "explain this")
    blob = json.dumps(session.model_dump(mode="json"))
    assert settings.groq_api_key not in blob
    assert "GROQ_API_KEY" not in blob


def test_test_writer_trace_is_completed_not_left_waiting(
    settings, patch_llm, no_retrieval
):
    """The loop drives generate()/execute() directly, bypassing run()."""
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(GOOD_FIX),
        PASSING_TEST,
    )
    session = ReviewOrchestrator(settings).run(FIND_MAX, "review everything")
    trace = session.trace_for(AgentName.TEST_WRITER)
    assert trace is not None
    assert trace.status is AgentStatus.COMPLETED, (
        f"Test-Writer left in state {trace.status.value}"
    )
    assert trace.summary, "the Test-Writer recorded no summary"
    assert trace.duration_seconds >= 0
    assert "sandbox" in trace.tools_used


def test_test_writer_trace_completes_when_the_sandbox_is_unavailable(
    settings, patch_llm, no_retrieval
):
    patch_llm(
        intent_json("full_review"),
        bug_report_json(),
        style_json(),
        fix_json(GOOD_FIX),
        PASSING_TEST,
    )
    orchestrator = ReviewOrchestrator(settings)
    orchestrator.executor.backend.available = False
    orchestrator.executor.backend.description = "no sandbox in this test"
    session = orchestrator.run(FIND_MAX, "review everything")

    trace = session.trace_for(AgentName.TEST_WRITER)
    assert trace.status is AgentStatus.COMPLETED
    assert "NOT executed" in trace.summary


def test_fixer_is_skipped_when_the_bug_finder_failed(settings, patch_llm, no_retrieval):
    """A failed analyst must not lead to a confident 'no changes needed'."""
    model = patch_llm(intent_json("debugging"))
    real = model._generate
    calls = {"n": 0}

    def fail_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("upstream exploded")
        return real(*args, **kwargs)

    object.__setattr__(model, "_generate", fail_second)
    session = ReviewOrchestrator(settings).run(FIND_MAX, "find the bug")

    assert status_of(session, AgentName.BUG_FINDER) is AgentStatus.FAILED
    assert status_of(session, AgentName.FIXER) is AgentStatus.SKIPPED
    assert "failed" in session.trace_for(AgentName.FIXER).skip_reason
    assert session.final_fix is None
    assert session.verification is VerificationStatus.NOT_VERIFIED
    assert any("incomplete" in item for item in session.limitations)
