"""Shared pytest fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings  # noqa: E402
from tests.fake_llm import FakeChatModel  # noqa: E402


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    """Keep the suite independent of the developer's own .env."""
    from config.settings import get_settings

    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    get_settings.cache_clear()
    try:
        from ui.state import refresh_probes

        refresh_probes()
    except Exception:  # noqa: BLE001
        pass
    yield
    get_settings.cache_clear()


LLM_CONSUMERS = [
    "agents.bug_finder",
    "agents.style_agent",
    "agents.fixer_agent",
    "agents.test_writer",
    "agents.explainer",
    "routing.intent_router",
]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointed at a temp directory, with a dummy key present."""
    return Settings(
        provider="groq",
        groq_api_key="test-key-not-real",
        groq_model="test-model",
        max_fix_attempts=3,
        rag_enabled=True,
        chroma_persist_dir=str(tmp_path / "chroma"),
        sandbox_timeout_seconds=30,
        sandbox_cpu_seconds=15,
        project_root=PROJECT_ROOT,
    )


@pytest.fixture
def patch_llm(monkeypatch):
    """Returns a function: install(*responses) -> FakeChatModel."""

    def install(*responses: str) -> FakeChatModel:
        model = FakeChatModel(responses=list(responses), calls=[])
        for module_name in LLM_CONSUMERS:
            module = __import__(module_name, fromlist=["get_chat_model"])
            if hasattr(module, "get_chat_model"):
                monkeypatch.setattr(
                    module, "get_chat_model", lambda *a, **k: model, raising=True
                )
        return model

    return install


@pytest.fixture
def no_retrieval(monkeypatch):
    """Disable vector-store access so tests do not need an index built."""
    from models.schemas import RetrievalDecision

    def fake_policy(intent, agent, *, rag_enabled=True, settings=None):
        return RetrievalDecision(
            agent=agent,
            should_retrieve=False,
            reason="Retrieval stubbed out for this test.",
        )

    for module_name in (
        "agents.bug_finder",
        "agents.style_agent",
        "agents.fixer_agent",
        "agents.test_writer",
        "agents.explainer",
    ):
        module = __import__(module_name, fromlist=["policy_decision"])
        monkeypatch.setattr(module, "policy_decision", fake_policy, raising=True)


def bug_report_json(issues: int = 1) -> str:
    return json.dumps(
        {
            "issues": [
                {
                    "issue_id": f"BUG-{n:02d}",
                    "title": "maximum is initialised to 0",
                    "severity": "high",
                    "line": 2,
                    "explanation": "Fails for all-negative input.",
                    "reproduction": "find_max([-5, -2]) returns 0",
                    "suggested_fix": "Seed from the first element.",
                    "confidence": "high",
                    "source": "llm",
                }
                for n in range(1, issues + 1)
            ],
            "summary": f"{issues} issue(s) found.",
            "syntax_valid": True,
            "syntax_error": None,
        }
    )


def style_json() -> str:
    return json.dumps(
        {
            "suggestions": [
                {
                    "suggestion_id": "SUGG-01",
                    "title": "Rename `nums` to something more specific",
                    "explanation": "A clearer name documents intent.",
                    "line": 1,
                    "convention": "PEP 8 naming",
                }
            ],
            "summary": "One readability suggestion.",
        }
    )


def fix_json(code: str, changes: list[str] | None = None) -> str:
    return json.dumps(
        {
            "corrected_code": code,
            "changes": changes or ["Seed the accumulator from the first element."],
            "addressed_issue_ids": ["BUG-01"],
            "ambiguities": [],
        }
    )


def generated_tests_json(test_bodies: list[tuple[str, str]]) -> str:
    """test_bodies: list of (name, source)."""
    return json.dumps(
        {
            "tests": [
                {
                    "test_id": f"TEST-{index:02d}",
                    "name": name,
                    "category": "boundary",
                    "description": f"checks {name}",
                    "code": source,
                    "expected_behaviour": "as asserted",
                    "oracle": "definition of the function's purpose",
                }
                for index, (name, source) in enumerate(test_bodies, start=1)
            ],
            "notes": "",
        }
    )


def intent_json(intent: str, confidence: str = "high") -> str:
    return json.dumps(
        {
            "intent": intent,
            "confidence": confidence,
            "explanation": f"the request asks for {intent}",
        }
    )


def _build_session(verification: str, executed: bool):
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
        PromptRecord,
        PromptTechnique,
        RetrievalDecision,
        RetrievedPassage,
        ReviewSession,
        SandboxResult,
        Severity,
        StyleReport,
        StyleSuggestion,
        TestCase,
        TestOutcome,
        TestRunResult,
        VerificationStatus,
    )

    original = (
        "def find_max(nums):\n    maximum = 0\n    for num in nums:\n"
        "        if num > maximum:\n            maximum = num\n    return maximum\n"
    )
    corrected = (
        "def find_max(nums):\n    maximum = nums[0]\n    for num in nums:\n"
        "        if num > maximum:\n            maximum = num\n    return maximum\n"
    )

    test_case = TestCase(
        test_id="TEST-01",
        name="test_all_negative",
        category="boundary",
        description="all-negative input",
        code="def test_all_negative():\n    assert find_max([-5, -2]) == -2\n",
        expected_behaviour="returns -2",
        oracle="the definition of a maximum",
        outcome=TestOutcome.PASSED if executed else TestOutcome.NOT_EXECUTED,
        failure_message=None if executed else "no sandbox backend available",
    )

    run = TestRunResult(
        attempt=1,
        tests=[test_case],
        sandbox=SandboxResult(
            executed=executed,
            backend="seatbelt" if executed else "disabled",
            exit_code=0 if executed else None,
            stdout="1 passed in 0.02s" if executed else "",
            unavailable_reason=None if executed else "no sandbox backend available",
            duration_seconds=0.3,
        ),
        generated=1,
        executed=1 if executed else 0,
        passed=1 if executed else 0,
        test_file_source=test_case.code,
    )

    fix = FixResult(
        corrected_code=corrected,
        changes=["Seed the accumulator from the first element."],
        unified_diff=(
            "--- a/submitted.py\n+++ b/submitted.py\n@@ -1,3 +1,3 @@\n"
            "-    maximum = 0\n+    maximum = nums[0]\n"
        ),
        addressed_issue_ids=["BUG-01"],
    )

    return ReviewSession(
        session_id="uitest01",
        filename="find_max_logical_bug.py",
        original_code=original,
        user_request="review and fix everything",
        provider="groq",
        model="test-model",
        intent=IntentDecision(
            intent=Intent.FULL_REVIEW,
            confidence=Confidence.HIGH,
            method="llm_zero_shot",
            explanation="the request asked for a full review",
            agents=[
                AgentName.BUG_FINDER,
                AgentName.STYLE_LINT,
                AgentName.FIXER,
                AgentName.TEST_WRITER,
            ],
        ),
        bug_report=BugReport(
            issues=[
                Issue(
                    issue_id="BUG-01",
                    title="maximum is initialised to 0",
                    severity=Severity.HIGH,
                    line=2,
                    explanation="All-negative input returns 0.",
                    reproduction="find_max([-5, -2]) returns 0",
                    suggested_fix="Seed from the first element.",
                    confidence=Confidence.HIGH,
                )
            ],
            summary="One defect found.",
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
            suggestions=[
                StyleSuggestion(
                    suggestion_id="SUGG-01",
                    title="Add a docstring",
                    explanation="State the contract for empty input.",
                    line=1,
                    convention="PEP 257",
                )
            ],
            summary="One lint finding, one suggestion.",
        ),
        final_fix=fix,
        final_test_run=run,
        attempts=[
            FixAttempt(attempt=1, fix=fix, test_run=run, verified=executed)
        ],
        traces=[
            AgentTrace(
                agent=AgentName.BUG_FINDER,
                status=AgentStatus.COMPLETED,
                duration_seconds=1.1,
                findings_count=1,
                summary="One defect found.",
                tools_used=["ast.parse"],
                techniques=[PromptTechnique.FEW_SHOT],
                events=["[00:00:01] Parsing the submitted code with ast"],
            ),
            AgentTrace(
                agent=AgentName.STYLE_LINT,
                status=AgentStatus.COMPLETED,
                duration_seconds=0.9,
                findings_count=2,
                summary="One lint finding.",
                tools_used=["ruff"],
                techniques=[PromptTechnique.ZERO_SHOT],
                events=["[00:00:02] Invoking ruff"],
            ),
            AgentTrace(
                agent=AgentName.FIXER,
                status=AgentStatus.COMPLETED,
                duration_seconds=1.4,
                findings_count=1,
                summary="Produced a correction.",
                techniques=[PromptTechnique.ZERO_SHOT],
                events=["[00:00:03] Correcting against 1 bug finding"],
            ),
            AgentTrace(
                agent=AgentName.TEST_WRITER,
                status=AgentStatus.COMPLETED,
                duration_seconds=2.0,
                findings_count=0,
                summary="1 generated, 1 executed, 1 passed"
                if executed
                else "1 test generated but NOT executed",
                tools_used=["sandbox"],
                techniques=[PromptTechnique.ZERO_SHOT],
                events=["[00:00:04] Executing 1 test in the sandbox"],
            ),
        ],
        prompts=[
            PromptRecord(
                agent=AgentName.BUG_FINDER,
                technique=PromptTechnique.FEW_SHOT,
                system_prompt="You are the Bug-Finder agent...",
                user_prompt_template="Here are examples...\n{code}",
                rendered_user_prompt="Here are examples...\ndef find_max(nums):",
                few_shot_examples=["Example 1: def get_discount(price, is_member):"],
                output_parser="RepairingPydanticParser(PydanticOutputParser[BugReport])",
                lcel_pipeline="ChatPromptTemplate\n    |\nChatGroq\n    |\nParser",
                raw_output='{"issues": []}',
                parsed_output={"issues": []},
                model="test-model",
                provider="groq",
                duration_seconds=1.1,
            )
        ],
        retrievals=[
            RetrievalDecision(
                agent=AgentName.STYLE_LINT,
                should_retrieve=True,
                reason="A style request depends on documented conventions.",
                query="python naming conventions readability PEP 8",
                passages=[
                    RetrievedPassage(
                        text="Python Style Conventions - Naming\n\nFunctions use snake_case.",
                        source="python_style_conventions.md - Naming",
                        chunk_index=0,
                        distance=0.41,
                        score=0.59,
                    )
                ],
            ),
            RetrievalDecision(
                agent=AgentName.FIXER,
                should_retrieve=False,
                reason=(
                    "Skipped - the Fixer already receives the Bug-Finder and "
                    "Style/Lint findings."
                ),
            ),
        ],
        verification=VerificationStatus(verification),
        overall_assessment=(
            "The Bug-Finder reported 1 defect. All 1 executed test(s) passed."
            if executed
            else "1 test(s) were generated but NOT executed, so the correction "
            "is unverified."
        ),
        limitations=(
            ["'Verified' here means 1 generated test(s) passed. That is evidence, "
             "not proof of correctness."]
            if executed
            else ["The sandbox was unavailable, so the generated tests were never "
                  "executed."]
        ),
        duration_seconds=5.4,
    )


@pytest.fixture
def verified_session():
    return _build_session("Verified", executed=True)


@pytest.fixture
def unverified_session():
    return _build_session("Not Yet Verified", executed=False)


@pytest.fixture
def explanation_session():
    from models.schemas import (
        AgentName,
        AgentStatus,
        AgentTrace,
        Confidence,
        Intent,
        IntentDecision,
        RetrievalDecision,
        ReviewSession,
        VerificationStatus,
    )

    return ReviewSession(
        session_id="uitest02",
        filename="word_counter_clean.py",
        original_code="def count_words(text):\n    return len(text.split())\n",
        user_request="explain this code",
        provider="groq",
        model="test-model",
        intent=IntentDecision(
            intent=Intent.CODE_EXPLANATION,
            confidence=Confidence.HIGH,
            method="llm_zero_shot",
            explanation="the request asked for an explanation",
            agents=[AgentName.EXPLAINER],
        ),
        explanation="## What it does\n\nIt counts whitespace-separated words.",
        traces=[
            AgentTrace(
                agent=AgentName.EXPLAINER,
                status=AgentStatus.COMPLETED,
                duration_seconds=0.8,
                summary="Explanation produced (9 words).",
            ),
            *[
                AgentTrace(
                    agent=name,
                    status=AgentStatus.SKIPPED,
                    skip_reason="not part of this workflow - the request was an "
                    "explanation request",
                    summary="Skipped",
                )
                for name in (
                    AgentName.BUG_FINDER,
                    AgentName.STYLE_LINT,
                    AgentName.FIXER,
                    AgentName.TEST_WRITER,
                )
            ],
        ],
        retrievals=[
            RetrievalDecision(
                agent=AgentName.EXPLAINER,
                should_retrieve=False,
                reason="Skipped - explaining code needs only the code itself.",
            )
        ],
        verification=VerificationStatus.NOT_VERIFIED,
        overall_assessment="Explanation produced. Nothing was executed or verified.",
        limitations=["An explanation request does not run tests."],
    )
