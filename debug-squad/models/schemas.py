"""Structured data contracts for Debug Squad."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """How serious a finding is."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Confidence(str, Enum):
    """How sure an agent is about a finding."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Intent(str, Enum):
    """The five supported user intents."""

    FULL_REVIEW = "full_review"
    DEBUGGING = "debugging"
    STYLE_IMPROVEMENT = "style_improvement"
    CODE_EXPLANATION = "code_explanation"
    TEST_GENERATION = "test_generation"


class AgentName(str, Enum):
    BUG_FINDER = "bug_finder"
    STYLE_LINT = "style_lint"
    FIXER = "fixer"
    TEST_WRITER = "test_writer"
    EXPLAINER = "explainer"


class AgentStatus(str, Enum):
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PromptTechnique(str, Enum):
    """Prompt-engineering techniques, surfaced in the Prompt Inspector."""

    ZERO_SHOT = "zero_shot"
    FEW_SHOT = "few_shot"
    REFLECTION = "reflection"
    REACT_TOOL_USE = "react_tool_use"


class TestOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    NOT_EXECUTED = "not_executed"


class VerificationStatus(str, Enum):
    """Used for both fix verification and the faculty compliance dashboard."""

    VERIFIED = "Verified"
    NOT_VERIFIED = "Not Yet Verified"
    FAILED = "Failed"
    NOT_CONFIGURED = "Not Configured"


class FindingSource(str, Enum):
    """Separates deterministic tool output from LLM opinion."""

    RUFF = "ruff"
    AST = "ast"
    LLM = "llm"


class Issue(BaseModel):
    """A single problem found in the submitted code."""

    issue_id: str = Field(description="Short stable id, e.g. BUG-01 or STYLE-03")
    title: str = Field(description="One-line summary of the problem")
    severity: Severity = Severity.MEDIUM
    line: int | None = Field(
        default=None, description="1-based line number, or null if not localisable"
    )
    explanation: str = Field(description="Why this is a problem")
    reproduction: str | None = Field(
        default=None, description="Concrete input that exposes the problem"
    )
    suggested_fix: str | None = Field(
        default=None, description="What to change, described in prose"
    )
    confidence: Confidence = Confidence.MEDIUM
    source: FindingSource = FindingSource.LLM
    rule_code: str | None = Field(
        default=None, description="Ruff rule code when source == ruff"
    )


class BugReport(BaseModel):
    """Output of the Bug-Finder agent."""

    issues: list[Issue] = Field(default_factory=list)
    summary: str = ""
    syntax_valid: bool = True
    syntax_error: str | None = None


class StyleSuggestion(BaseModel):
    """An LLM readability/maintainability suggestion (not a lint rule hit)."""

    suggestion_id: str
    title: str
    explanation: str
    line: int | None = None
    convention: str | None = Field(
        default=None, description="The Python convention this appeals to, e.g. PEP 8 naming"
    )


class StyleReport(BaseModel):
    """Output of the Style/Lint agent."""

    ruff_issues: list[Issue] = Field(default_factory=list)
    suggestions: list[StyleSuggestion] = Field(default_factory=list)
    summary: str = ""
    ruff_available: bool = True
    ruff_error: str | None = None


class FixResult(BaseModel):
    """Output of the Fixer agent."""

    corrected_code: str = ""
    changes: list[str] = Field(
        default_factory=list, description="Human-readable description of each change"
    )
    unified_diff: str = ""
    addressed_issue_ids: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Behaviour the agent could not determine and did not guess at",
    )
    attempt: int = 1
    syntax_valid: bool = True


class TestCase(BaseModel):
    """A single generated pytest test."""

    test_id: str
    name: str = Field(description="pytest function name, must start with test_")
    category: str = Field(description="normal | boundary | negative | edge")
    description: str
    code: str = Field(description="Full pytest function source")
    expected_behaviour: str = Field(
        description="What the test asserts and why that is correct"
    )
    oracle: str = Field(
        description="Where the expected result comes from: stated spec, "
        "mathematical definition, or an explicitly named assumption"
    )
    outcome: TestOutcome = TestOutcome.NOT_EXECUTED
    failure_message: str | None = None


class SandboxResult(BaseModel):
    """Raw result of one sandboxed pytest run."""

    executed: bool = False
    backend: str = "disabled"
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    unavailable_reason: str | None = None


class TestRunResult(BaseModel):
    """Output of the Test-Writer agent for one attempt."""

    attempt: int = 1
    tests: list[TestCase] = Field(default_factory=list)
    sandbox: SandboxResult = Field(default_factory=SandboxResult)
    generated: int = 0
    executed: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    test_file_source: str = ""

    @property
    def pass_rate(self) -> float | None:
        """Pass percentage, or None when nothing actually ran."""
        if self.executed == 0:
            return None
        return 100.0 * self.passed / self.executed

    @property
    def all_passed(self) -> bool:
        """True only if tests really ran and every one of them passed."""
        return self.executed > 0 and self.failed == 0 and self.passed == self.executed


class RetrievedPassage(BaseModel):
    text: str
    source: str
    chunk_index: int = 0
    score: float | None = Field(
        default=None, description="Similarity score; higher is more similar"
    )
    distance: float | None = Field(default=None, description="Raw Chroma distance")


class RetrievalDecision(BaseModel):
    """The observable record of one Agentic RAG decision."""

    agent: AgentName
    should_retrieve: bool
    reason: str
    query: str | None = None
    passages: list[RetrievedPassage] = Field(default_factory=list)
    error: str | None = None


class IntentDecision(BaseModel):
    intent: Intent
    confidence: Confidence = Confidence.MEDIUM
    explanation: str = ""
    agents: list[AgentName] = Field(default_factory=list)
    method: str = Field(
        default="llm_zero_shot",
        description="llm_zero_shot | keyword_fallback | manual_override",
    )
    user_request: str = ""


class PromptRecord(BaseModel):
    """Everything the Prompt Inspector needs for one LLM call."""

    agent: AgentName
    technique: PromptTechnique
    system_prompt: str = ""
    user_prompt_template: str = ""
    rendered_user_prompt: str = ""
    few_shot_examples: list[str] = Field(default_factory=list)
    retrieved_context: list[str] = Field(default_factory=list)
    output_parser: str = ""
    lcel_pipeline: str = ""
    raw_output: str = ""
    parsed_output: dict[str, Any] | None = None
    parse_repaired: bool = False
    model: str = ""
    provider: str = ""
    duration_seconds: float = 0.0
    error: str | None = None


class AgentTrace(BaseModel):
    """One agent's execution record, rendered on the AI Agents screen."""

    agent: AgentName
    status: AgentStatus = AgentStatus.WAITING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0.0
    summary: str = ""
    findings_count: int = 0
    events: list[str] = Field(default_factory=list)
    techniques: list[PromptTechnique] = Field(default_factory=list)
    tools_used: list[str] = Field(
        default_factory=list, description="ReAct-style tool invocations, e.g. ruff, sandbox"
    )
    error: str | None = None
    skip_reason: str | None = None

    def log(self, message: str) -> None:
        stamp = datetime.now(UTC).strftime("%H:%M:%S")
        self.events.append(f"[{stamp}] {message}")


class FixAttempt(BaseModel):
    """One iteration of the reflection loop."""

    attempt: int
    fix: FixResult
    test_run: TestRunResult
    reflection_notes: str = ""
    verified: bool = False


class ReviewSession(BaseModel):
    """A complete review."""

    session_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    filename: str = "submitted.py"
    original_code: str = ""
    user_request: str = ""

    provider: str = ""
    model: str = ""
    rag_enabled: bool = True
    auto_intent: bool = True

    intent: IntentDecision | None = None
    bug_report: BugReport | None = None
    style_report: StyleReport | None = None
    explanation: str | None = None
    final_fix: FixResult | None = None
    final_test_run: TestRunResult | None = None

    attempts: list[FixAttempt] = Field(default_factory=list)
    traces: list[AgentTrace] = Field(default_factory=list)
    prompts: list[PromptRecord] = Field(default_factory=list)
    retrievals: list[RetrievalDecision] = Field(default_factory=list)

    verification: VerificationStatus = VerificationStatus.NOT_VERIFIED
    overall_assessment: str = ""
    limitations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


    def trace_for(self, agent: AgentName) -> AgentTrace | None:
        for trace in self.traces:
            if trace.agent == agent:
                return trace
        return None

    def all_issues(self) -> list[Issue]:
        issues: list[Issue] = []
        if self.bug_report:
            issues.extend(self.bug_report.issues)
        if self.style_report:
            issues.extend(self.style_report.ruff_issues)
        return issues

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def total_issues(self) -> int:
        return len(self.all_issues())
