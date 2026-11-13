"""Fixer."""

from __future__ import annotations

import difflib

from pydantic import BaseModel, Field

from agents.base import BaseAgent, check_syntax
from chains.lcel_chains import StructuredChain
from config.llm_provider import get_chat_model
from models.schemas import (
    AgentName,
    BugReport,
    FixResult,
    Intent,
    PromptTechnique,
    StyleReport,
    TestOutcome,
    TestRunResult,
)
from prompts.system_prompts import FIXER_REFLECTION_SYSTEM, FIXER_SYSTEM
from prompts.user_prompts import (
    FIXER_REFLECTION_USER,
    FIXER_USER,
    format_issue_list,
    format_style_suggestions,
)
from rag.retriever import policy_decision


class _FixerOutput(BaseModel):
    """What the Fixer model is asked to return."""

    corrected_code: str = Field(description="The complete corrected file")
    changes: list[str] = Field(
        default_factory=list, description="One line per meaningful change"
    )
    addressed_issue_ids: list[str] = Field(
        default_factory=list, description="Ids of the findings actually fixed"
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Behaviour that was unclear and was NOT guessed at",
    )


def make_unified_diff(original: str, corrected: str, filename: str) -> str:
    """A standard unified diff, generated deterministically by difflib."""
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        corrected.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    )
    return "".join(diff)


def summarise_failures(test_run: TestRunResult, limit: int = 4) -> str:
    """Render real failing-test details for the reflection prompt."""
    failing = [
        test
        for test in test_run.tests
        if test.outcome in (TestOutcome.FAILED, TestOutcome.ERROR)
    ]
    if not failing:
        return "  (no individual failures were reported)"

    parts: list[str] = []
    for test in failing[:limit]:
        parts.append(
            f"  - {test.name} [{test.outcome.value}]\n"
            f"    what it checks: {test.description}\n"
            f"    oracle: {test.oracle}\n"
            f"    pytest said:\n{_indent(test.failure_message or '(no message)', 6)}"
        )
    if len(failing) > limit:
        parts.append(f"  ... and {len(failing) - limit} more failing test(s)")
    return "\n".join(parts)


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(f"{pad}{line}" for line in text.splitlines()[:25])


class FixerAgent(BaseAgent):
    name = AgentName.FIXER


    def run(
        self,
        code: str,
        bug_report: BugReport | None,
        style_report: StyleReport | None,
        intent: Intent,
    ) -> FixResult:
        self.begin()

        decision = policy_decision(
            intent,
            self.name,
            rag_enabled=self.session.rag_enabled,
            settings=self.settings,
        )
        self.record_retrieval(decision)

        bug_issues = bug_report.issues if bug_report else []
        style_issues = style_report.ruff_issues if style_report else []
        suggestions = style_report.suggestions if style_report else []

        if not bug_issues and not style_issues and not suggestions:
            self.trace.log("Nothing to fix - no findings were reported")
            result = FixResult(
                corrected_code=code,
                changes=[],
                unified_diff="",
                attempt=1,
                syntax_valid=True,
            )
            self.finish("No changes needed - no findings to act on.")
            return result

        self.trace.log(
            f"Correcting against {len(bug_issues)} bug finding(s), "
            f"{len(style_issues)} lint finding(s), "
            f"{len(suggestions)} readability suggestion(s)"
        )

        chain = StructuredChain(
            system_prompt=FIXER_SYSTEM,
            user_template=FIXER_USER,
            output_model=_FixerOutput,
            model=get_chat_model(self.settings, json_mode=True),
            agent=self.name,
            technique=PromptTechnique.ZERO_SHOT,
            provider=self.settings.provider,
            model_name=self.settings.model_name,
        )
        self.record_technique(PromptTechnique.ZERO_SHOT)

        try:
            output, record = chain.invoke(
                {
                    "code": code,
                    "bug_findings": format_issue_list(bug_issues),
                    "style_findings": (
                        format_issue_list(style_issues)
                        + "\n"
                        + format_style_suggestions(suggestions)
                    ),
                }
            )
        except Exception as exc:
            self.fail(f"{type(exc).__name__}: {exc}")
            raise

        self.record_prompt(record)
        result = self._finalise(output, code, attempt=1)
        self.finish(
            f"Produced a correction addressing "
            f"{len(result.addressed_issue_ids)} finding(s).",
            findings=len(result.changes),
        )
        return result


    def reflect(
        self,
        original_code: str,
        previous_fix: FixResult,
        test_run: TestRunResult,
        attempt: int,
    ) -> FixResult:
        """Revise the previous fix using the real test output."""
        self.begin()
        self.record_technique(PromptTechnique.REFLECTION)
        self.trace.log(
            f"Reflection attempt {attempt}: "
            f"{test_run.failed} test(s) failed, {test_run.passed} passed"
        )

        chain = StructuredChain(
            system_prompt=FIXER_REFLECTION_SYSTEM,
            user_template=FIXER_REFLECTION_USER,
            output_model=_FixerOutput,
            model=get_chat_model(self.settings, json_mode=True),
            agent=self.name,
            technique=PromptTechnique.REFLECTION,
            provider=self.settings.provider,
            model_name=self.settings.model_name,
        )

        try:
            output, record = chain.invoke(
                {
                    "attempt": attempt,
                    "max_attempts": self.settings.max_fix_attempts,
                    "previous_code": previous_fix.corrected_code or original_code,
                    "test_source": test_run.test_file_source,
                    "test_output": (test_run.sandbox.stdout or "(no output)")[-3000:],
                    "passed": test_run.passed,
                    "failed": test_run.failed,
                    "failure_details": summarise_failures(test_run),
                }
            )
        except Exception as exc:
            self.fail(f"{type(exc).__name__}: {exc}")
            raise

        self.record_prompt(record)
        result = self._finalise(output, original_code, attempt=attempt)
        self.finish(
            f"Revised the fix after reading the failures from attempt {attempt - 1}.",
            findings=len(result.changes),
        )
        return result


    def _finalise(self, output: _FixerOutput, original: str, attempt: int) -> FixResult:
        """Validate the model's code and compute the diff deterministically."""
        corrected = (output.corrected_code or "").strip()
        if corrected:
            corrected += "\n"

        syntax_ok, syntax_error = check_syntax(corrected) if corrected else (False, "empty")
        if not syntax_ok:
            self.trace.log(
                f"The proposed correction does not parse ({syntax_error}); "
                "keeping the original code and flagging it"
            )
            return FixResult(
                corrected_code=original,
                changes=[],
                unified_diff="",
                addressed_issue_ids=[],
                ambiguities=[
                    f"The model's proposed correction was not valid Python "
                    f"({syntax_error}), so it was rejected and the original code "
                    "was kept. Nothing has been verified."
                ],
                attempt=attempt,
                syntax_valid=False,
            )

        diff = make_unified_diff(original, corrected, self.session.filename)
        if not diff:
            self.trace.log("The correction is byte-identical to the original")

        return FixResult(
            corrected_code=corrected,
            changes=[c.strip() for c in output.changes if c.strip()],
            unified_diff=diff,
            addressed_issue_ids=output.addressed_issue_ids,
            ambiguities=[a.strip() for a in output.ambiguities if a.strip()],
            attempt=attempt,
            syntax_valid=True,
        )
