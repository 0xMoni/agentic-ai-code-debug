"""Review orchestration and the reflection loop."""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Callable

from agents.bug_finder import BugFinderAgent
from agents.explainer import ExplainerAgent
from agents.fixer_agent import FixerAgent
from agents.style_agent import StyleAgent
from agents.test_writer import TestWriterAgent
from config.settings import Settings, get_settings
from models.schemas import (
    AgentName,
    BugReport,
    FixAttempt,
    FixResult,
    Intent,
    ReviewSession,
    StyleReport,
    TestRunResult,
    VerificationStatus,
)
from routing.intent_router import INTENT_LABELS, WORKFLOW_LABELS, IntentRouter
from sandbox.executor import SandboxExecutor

ProgressCallback = Callable[[str, str], None]
"""Called as (stage, message) so the UI can show live progress."""

SKIP_REASONS: dict[AgentName, str] = {
    AgentName.BUG_FINDER: "not part of this workflow - the request did not ask for defect detection",
    AgentName.STYLE_LINT: "not part of this workflow - the request did not ask about style",
    AgentName.FIXER: "not part of this workflow - the request did not ask for a correction",
    AgentName.TEST_WRITER: "not part of this workflow - the request did not ask for tests",
    AgentName.EXPLAINER: "not part of this workflow - the request was not an explanation request",
}


class ReviewOrchestrator:
    """Runs one review from submitted code to consolidated result."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.progress = progress or (lambda stage, message: None)
        self.executor = SandboxExecutor(self.settings)


    def _emit(self, stage: str, message: str) -> None:
        with contextlib.suppress(Exception):
            self.progress(stage, message)


    def run(
        self,
        code: str,
        user_request: str = "",
        *,
        filename: str = "submitted.py",
        auto_intent: bool = True,
        manual_intent: Intent | None = None,
        rag_enabled: bool = True,
    ) -> ReviewSession:
        """Execute a complete review."""
        started = time.perf_counter()

        session = ReviewSession(
            session_id=uuid.uuid4().hex[:12],
            filename=filename,
            original_code=code,
            user_request=user_request,
            provider=self.settings.provider,
            model=self.settings.model_name,
            rag_enabled=rag_enabled,
            auto_intent=auto_intent,
        )

        if not code.strip():
            session.errors.append("No code was submitted.")
            session.overall_assessment = "Nothing to review - the editor was empty."
            session.verification = VerificationStatus.NOT_VERIFIED
            return session

        self._emit("routing", "Detecting intent...")
        router = IntentRouter(self.settings)
        decision, record = router.route(
            code, user_request, auto=auto_intent, manual_intent=manual_intent
        )
        session.intent = decision
        if record:
            session.prompts.append(record)
        self._emit(
            "routing",
            f"Intent: {INTENT_LABELS[decision.intent]} "
            f"({decision.method}) -> {WORKFLOW_LABELS[decision.intent]}",
        )

        selected = set(decision.agents)

        for name in (
            AgentName.BUG_FINDER,
            AgentName.STYLE_LINT,
            AgentName.FIXER,
            AgentName.TEST_WRITER,
            AgentName.EXPLAINER,
        ):
            if name not in selected:
                self._skip(session, name)

        if decision.intent == Intent.CODE_EXPLANATION:
            self._emit("explainer", "Explaining the code...")
            agent = ExplainerAgent(session, self.settings)
            try:
                session.explanation = agent.run(code, user_request, decision.intent)
                session.overall_assessment = (
                    "Explanation produced. No changes were made to the code, and "
                    "nothing was executed or verified."
                )
            except Exception as exc:  # noqa: BLE001
                session.overall_assessment = (
                    f"The explanation could not be produced: {exc}"
                )
            session.verification = VerificationStatus.NOT_VERIFIED
            session.limitations.append(
                "An explanation request does not run tests, so nothing about "
                "the code's correctness has been verified."
            )
            session.duration_seconds = round(time.perf_counter() - started, 2)
            return session

        bug_report: BugReport | None = None
        if AgentName.BUG_FINDER in selected:
            self._emit("bug_finder", "Bug-Finder analysing the code...")
            try:
                bug_report = BugFinderAgent(session, self.settings).run(
                    code, decision.intent
                )
                session.bug_report = bug_report
                self._emit(
                    "bug_finder", f"Bug-Finder: {len(bug_report.issues)} issue(s)"
                )
            except Exception as exc:  # noqa: BLE001
                self._emit("bug_finder", f"Bug-Finder failed: {exc}")

        if bug_report and not bug_report.syntax_valid:
            for name in (AgentName.STYLE_LINT, AgentName.FIXER, AgentName.TEST_WRITER):
                if name in selected:
                    self._skip(
                        session,
                        name,
                        "the submitted file is not valid Python, so there is "
                        "nothing meaningful to analyse yet",
                    )
            session.overall_assessment = (
                f"The submitted file is not valid Python: {bug_report.syntax_error}. "
                "Fix the syntax error and submit it again."
            )
            session.verification = VerificationStatus.FAILED
            session.limitations.append(
                "Only the syntax check ran. No style review, correction or test "
                "execution was attempted."
            )
            session.duration_seconds = round(time.perf_counter() - started, 2)
            return session

        style_report: StyleReport | None = None
        if AgentName.STYLE_LINT in selected:
            self._emit("style_lint", "Style/Lint running Ruff...")
            try:
                style_report = StyleAgent(session, self.settings).run(
                    code, decision.intent
                )
                session.style_report = style_report
                self._emit(
                    "style_lint",
                    f"Style/Lint: {len(style_report.ruff_issues)} Ruff finding(s), "
                    f"{len(style_report.suggestions)} suggestion(s)",
                )
            except Exception as exc:  # noqa: BLE001
                self._emit("style_lint", f"Style/Lint failed: {exc}")

        fixer = FixerAgent(session, self.settings) if AgentName.FIXER in selected else None
        tester = (
            TestWriterAgent(session, self.settings, self.executor)
            if AgentName.TEST_WRITER in selected
            else None
        )

        upstream_failed = [
            name
            for name in (AgentName.BUG_FINDER, AgentName.STYLE_LINT)
            if name in selected
            and (trace := session.trace_for(name)) is not None
            and trace.status.value == "failed"
        ]
        have_findings = bool(
            (bug_report and bug_report.issues)
            or (style_report and (style_report.ruff_issues or style_report.suggestions))
        )
        if fixer and upstream_failed and not have_findings:
            failed_names = ", ".join(n.value for n in upstream_failed)
            fixer.skip(
                f"{failed_names} failed, so there were no findings to correct. "
                "Running the Fixer would wrongly report that no changes are needed."
            )
            session.limitations.append(
                f"The review is incomplete: {failed_names} failed. See the errors "
                "below - rerunning usually resolves a rate-limit failure."
            )
            fixer = None

        fix: FixResult | None = None
        if fixer:
            self._emit("fixer", "Fixer applying corrections...")
            try:
                fix = fixer.run(code, bug_report, style_report, decision.intent)
                session.final_fix = fix
                self._emit("fixer", f"Fixer: {len(fix.changes)} change(s)")
            except Exception as exc:  # noqa: BLE001
                self._emit("fixer", f"Fixer failed: {exc}")

        if tester:
            self._run_reflection_loop(session, code, fix, fixer, tester, decision.intent)
        elif fix:
            session.limitations.append(
                "No tests were generated for this request, so the correction has "
                "not been verified by execution."
            )
            session.verification = VerificationStatus.NOT_VERIFIED

        self._conclude(session)
        session.duration_seconds = round(time.perf_counter() - started, 2)
        return session


    def _run_reflection_loop(
        self,
        session: ReviewSession,
        original_code: str,
        fix: FixResult | None,
        fixer: FixerAgent | None,
        tester: TestWriterAgent,
        intent: Intent,
    ) -> None:
        max_attempts = max(1, self.settings.max_fix_attempts)

        self._emit("test_writer", "Test-Writer generating tests...")
        tester.begin()
        try:
            # Tests target the original code's intent, so they can genuinely fail the fix.
            tests, module_source = tester.generate(original_code, intent)
        except Exception as exc:  # noqa: BLE001
            tester.fail(f"{type(exc).__name__}: {exc}")
            self._emit("test_writer", f"Test generation failed: {exc}")
            session.verification = VerificationStatus.NOT_VERIFIED
            session.limitations.append(
                f"Tests could not be generated ({exc}), so nothing was verified."
            )
            return

        code_under_test = fix.corrected_code if fix and fix.syntax_valid else original_code
        attempt = 1
        test_run: TestRunResult | None = None

        while attempt <= max_attempts:
            self._emit(
                "test_lab",
                f"Attempt {attempt} of {max_attempts}: running {len(tests)} test(s) "
                f"in the {self.executor.backend.name} sandbox...",
            )
            # Same tests every attempt - otherwise a later pass would prove nothing.
            fresh = [t.model_copy(deep=True) for t in tests]
            test_run = tester.execute(
                code_under_test, fresh, module_source, attempt=attempt
            )
            session.final_test_run = test_run

            session.attempts.append(
                FixAttempt(
                    attempt=attempt,
                    fix=fix
                    or FixResult(
                        corrected_code=original_code, attempt=attempt, changes=[]
                    ),
                    test_run=test_run,
                    verified=test_run.all_passed,
                    reflection_notes="",
                )
            )

            if not test_run.sandbox.executed:
                tester.finish(
                    f"{test_run.generated} test(s) generated but NOT executed - "
                    f"{test_run.sandbox.unavailable_reason}",
                    findings=0,
                )
                self._emit(
                    "test_lab",
                    "Sandbox unavailable - tests recorded as NOT EXECUTED.",
                )
                session.verification = VerificationStatus.NOT_VERIFIED
                session.limitations.append(
                    "The sandbox was unavailable, so the generated tests were "
                    "never executed. The correction is unverified - it is not "
                    "known to be either right or wrong."
                )
                return

            if test_run.all_passed:
                tester.finish(
                    f"{test_run.generated} generated, {test_run.executed} executed, "
                    f"all passed on attempt {attempt}.",
                    findings=0,
                )
                self._emit(
                    "test_lab",
                    f"All {test_run.passed} executed test(s) passed on attempt "
                    f"{attempt}.",
                )
                session.verification = VerificationStatus.VERIFIED
                return

            if attempt >= max_attempts or fixer is None:
                reason = (
                    f"the maximum of {max_attempts} attempt(s) was reached"
                    if fixer
                    else "no Fixer agent is part of this workflow"
                )
                tester.finish(
                    f"{test_run.passed} passed, {test_run.failed} failed of "
                    f"{test_run.executed} executed after {attempt} attempt(s).",
                    findings=test_run.failed,
                )
                self._emit(
                    "test_lab",
                    f"{test_run.failed} test(s) still failing and {reason}.",
                )
                session.verification = VerificationStatus.FAILED
                session.limitations.append(
                    f"{test_run.failed} of {test_run.executed} executed test(s) "
                    f"still fail after {attempt} attempt(s). The correction is "
                    "NOT verified."
                )
                return

            self._emit(
                "fixer",
                f"{test_run.failed} test(s) failed - Fixer reflecting on the "
                f"actual output (attempt {attempt + 1} of {max_attempts})...",
            )
            attempt += 1
            try:
                fix = fixer.reflect(
                    original_code,
                    fix
                    or FixResult(corrected_code=code_under_test, attempt=attempt - 1),
                    test_run,
                    attempt,
                )
                session.final_fix = fix
                session.attempts[-1].reflection_notes = (
                    f"Tests failed, so the Fixer was given the real pytest output "
                    f"and produced revision {attempt}."
                )
                code_under_test = (
                    fix.corrected_code if fix.syntax_valid else code_under_test
                )
            except Exception as exc:  # noqa: BLE001
                tester.finish(
                    f"{test_run.passed} passed, {test_run.failed} failed; the "
                    "reflection step then failed.",
                    findings=test_run.failed,
                )
                self._emit("fixer", f"Reflection failed: {exc}")
                session.verification = VerificationStatus.FAILED
                session.limitations.append(
                    f"The reflection step failed ({exc}), so the remaining test "
                    "failures were never addressed."
                )
                return

        session.verification = VerificationStatus.FAILED


    def _skip(
        self, session: ReviewSession, name: AgentName, reason: str | None = None
    ) -> None:
        agent_classes = {
            AgentName.BUG_FINDER: BugFinderAgent,
            AgentName.STYLE_LINT: StyleAgent,
            AgentName.FIXER: FixerAgent,
            AgentName.EXPLAINER: ExplainerAgent,
        }
        if name == AgentName.TEST_WRITER:
            agent = TestWriterAgent(session, self.settings, self.executor)
        else:
            agent = agent_classes[name](session, self.settings)
        agent.skip(reason or SKIP_REASONS[name])


    def _conclude(self, session: ReviewSession) -> None:
        """Write the overall assessment, strictly from what actually happened."""
        issues = session.all_issues()
        critical = [i for i in issues if i.severity.value in ("critical", "high")]
        run = session.final_test_run

        parts: list[str] = []
        if session.bug_report is not None:
            count = len(session.bug_report.issues)
            parts.append(
                f"The Bug-Finder reported {count} defect(s)"
                + (f", {len(critical)} of them critical or high severity" if critical else "")
                + "."
            )
        if session.style_report is not None:
            parts.append(
                f"Ruff reported {len(session.style_report.ruff_issues)} violation(s) "
                f"and the Style agent added "
                f"{len(session.style_report.suggestions)} readability suggestion(s)."
            )
        if session.final_fix is not None:
            if session.final_fix.unified_diff:
                parts.append(
                    f"The Fixer produced a correction with "
                    f"{len(session.final_fix.changes)} described change(s)."
                )
            else:
                parts.append("The Fixer made no changes to the code.")

        if run is None:
            parts.append("No tests were run.")
        elif not run.sandbox.executed:
            parts.append(
                f"{run.generated} test(s) were generated but NOT executed "
                f"({run.sandbox.unavailable_reason}), so the correction is unverified."
            )
        elif run.all_passed:
            parts.append(
                f"All {run.passed} executed test(s) passed after "
                f"{session.attempt_count} attempt(s), so the fix is verified "
                "against those tests."
            )
        else:
            parts.append(
                f"{run.failed} of {run.executed} executed test(s) still fail after "
                f"{session.attempt_count} attempt(s), so the fix is NOT verified."
            )

        session.overall_assessment = " ".join(parts)

        if session.final_fix and session.final_fix.ambiguities:
            session.limitations.extend(session.final_fix.ambiguities)
        if session.style_report and not session.style_report.ruff_available:
            session.limitations.append(
                f"Ruff could not be run ({session.style_report.ruff_error}), so "
                "the deterministic lint findings are missing."
            )
        if not self.executor.backend.is_adequate and self.executor.available:
            session.limitations.append(
                f"Tests ran with the '{self.executor.backend.name}' backend, which "
                "does not provide network or filesystem isolation."
            )
        if run and run.sandbox.timed_out:
            session.limitations.append(
                "The sandboxed test run hit a resource limit and was killed, so "
                "some tests may not have reported a result."
            )
        if session.verification == VerificationStatus.VERIFIED and run:
            session.limitations.append(
                f"'Verified' here means {run.passed} generated test(s) passed. "
                "That is evidence, not proof of correctness."
            )
