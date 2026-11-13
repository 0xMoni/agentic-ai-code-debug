"""Test-Writer."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.base import BaseAgent, top_level_callables
from chains.lcel_chains import StructuredChain
from config.llm_provider import get_chat_model
from models.schemas import (
    AgentName,
    Intent,
    PromptTechnique,
    SandboxResult,
    TestCase,
    TestOutcome,
    TestRunResult,
)
from prompts.system_prompts import TEST_WRITER_SYSTEM
from prompts.user_prompts import TEST_WRITER_USER, format_retrieved_context
from rag.retriever import policy_decision
from sandbox.executor import SandboxExecutor, build_test_module

DEFAULT_TEST_COUNT = 5


class _GeneratedTests(BaseModel):
    tests: list[TestCase] = Field(default_factory=list)
    notes: str = ""


class TestWriterAgent(BaseAgent):
    name = AgentName.TEST_WRITER

    def __init__(self, session, settings=None, executor: SandboxExecutor | None = None):
        super().__init__(session, settings)
        self.executor = executor or SandboxExecutor(self.settings)


    def generate(self, code: str, intent: Intent) -> tuple[list[TestCase], str]:
        """Generate tests from the code's intent."""
        callables = top_level_callables(code)
        self.trace.log(
            f"Importable callables detected: {', '.join(callables) or 'none'}"
        )

        decision = policy_decision(
            intent,
            self.name,
            rag_enabled=self.session.rag_enabled,
            settings=self.settings,
        )
        self.record_retrieval(decision)

        chain = StructuredChain(
            system_prompt=TEST_WRITER_SYSTEM,
            user_template=TEST_WRITER_USER,
            output_model=_GeneratedTests,
            model=get_chat_model(self.settings, json_mode=True),
            agent=self.name,
            technique=PromptTechnique.ZERO_SHOT,
            provider=self.settings.provider,
            model_name=self.settings.model_name,
        )
        self.record_technique(PromptTechnique.ZERO_SHOT)

        generated, record = chain.invoke(
            {
                "code": code,
                "callables": ", ".join(callables) or "(none found)",
                "target_count": DEFAULT_TEST_COUNT,
                "retrieved_context": format_retrieved_context(decision.passages),
            }
        )
        record.retrieved_context = [p.text for p in decision.passages]
        self.record_prompt(record)

        tests = self._normalise(generated.tests)
        module_source = build_test_module(tests)
        self.trace.log(f"Generated {len(tests)} test(s)")
        return tests, module_source

    def _normalise(self, tests: list[TestCase]) -> list[TestCase]:
        """Make ids unique and drop anything that is not a usable test."""
        cleaned: list[TestCase] = []
        seen: set[str] = set()
        for index, test in enumerate(tests, start=1):
            body = (test.code or "").strip()
            if not body or "def test" not in body:
                self.trace.log(
                    f"Discarded a generated entry with no test function: "
                    f"{test.name or '(unnamed)'}"
                )
                continue
            if not test.name.startswith("test_"):
                for line in body.splitlines():
                    if line.strip().startswith("def test"):
                        test.name = line.strip()[4:].split("(")[0].strip()
                        break
            if test.name in seen:
                test.name = f"{test.name}_{index}"
                body = body.replace(
                    f"def {test.name.rsplit('_', 1)[0]}(", f"def {test.name}(", 1
                )
                test.code = body
            seen.add(test.name)
            test.test_id = test.test_id or f"TEST-{index:02d}"
            test.outcome = TestOutcome.NOT_EXECUTED
            cleaned.append(test)
        return cleaned


    def execute(
        self, code: str, tests: list[TestCase], module_source: str, attempt: int = 1
    ) -> TestRunResult:
        """Run the tests against `code` in the sandbox and tally real results."""
        self.record_tool("sandbox")
        self.trace.log(
            f"Executing {len(tests)} test(s) in the sandbox "
            f"(backend: {self.executor.backend.name})"
        )

        if not tests:
            return TestRunResult(
                attempt=attempt,
                tests=[],
                generated=0,
                test_file_source=module_source,
                sandbox=SandboxResult(
                    executed=False,
                    backend=self.executor.backend.name,
                    unavailable_reason="No usable tests were generated.",
                ),
            )

        sandbox_result, annotated, _failures = self.executor.run_and_annotate(
            code, tests, module_source
        )

        counts = dict.fromkeys(TestOutcome, 0)
        for test in annotated:
            counts[test.outcome] += 1

        run = TestRunResult(
            attempt=attempt,
            tests=annotated,
            sandbox=sandbox_result,
            generated=len(annotated),
            executed=sum(
                counts[o]
                for o in (
                    TestOutcome.PASSED,
                    TestOutcome.FAILED,
                    TestOutcome.ERROR,
                    TestOutcome.SKIPPED,
                )
            ),
            passed=counts[TestOutcome.PASSED],
            failed=counts[TestOutcome.FAILED] + counts[TestOutcome.ERROR],
            skipped=counts[TestOutcome.SKIPPED],
            test_file_source=module_source,
        )

        if not sandbox_result.executed:
            self.trace.log(
                f"Sandbox unavailable - {sandbox_result.unavailable_reason}. "
                "Tests are recorded as NOT EXECUTED."
            )
        elif sandbox_result.timed_out:
            self.trace.log("The sandboxed run was killed by a resource limit.")
        else:
            self.trace.log(
                f"Result: {run.passed} passed, {run.failed} failed, "
                f"{run.skipped} skipped out of {run.executed} executed"
            )
        return run


    def run(self, code: str, intent: Intent, attempt: int = 1) -> TestRunResult:
        """Generate and execute in one step (the usual entry point)."""
        self.begin()
        try:
            tests, module_source = self.generate(code, intent)
        except Exception as exc:
            self.fail(f"{type(exc).__name__}: {exc}")
            raise

        run = self.execute(code, tests, module_source, attempt=attempt)

        if not run.sandbox.executed:
            summary = (
                f"{run.generated} test(s) generated but NOT executed - "
                f"{run.sandbox.unavailable_reason}"
            )
        else:
            rate = run.pass_rate
            summary = (
                f"{run.generated} generated, {run.executed} executed, "
                f"{run.passed} passed, {run.failed} failed"
                + (f" ({rate:.0f}% pass rate)" if rate is not None else "")
            )
        self.finish(summary, findings=run.failed)
        return run

    def rerun(
        self, code: str, previous: TestRunResult, attempt: int
    ) -> TestRunResult:
        """Re-execute the same tests against revised code."""
        self.begin()
        self.trace.log(
            f"Re-running the same {len(previous.tests)} test(s) against the "
            f"revised code (attempt {attempt})"
        )
        fresh = [test.model_copy(deep=True) for test in previous.tests]
        for test in fresh:
            test.outcome = TestOutcome.NOT_EXECUTED
            test.failure_message = None
        run = self.execute(code, fresh, previous.test_file_source, attempt=attempt)
        self.finish(
            f"Attempt {attempt}: {run.passed} passed, {run.failed} failed "
            f"of {run.executed} executed.",
            findings=run.failed,
        )
        return run
