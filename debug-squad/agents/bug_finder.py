"""Bug-Finder."""

from __future__ import annotations

from agents.base import BaseAgent, check_syntax
from chains.lcel_chains import StructuredChain
from config.llm_provider import get_chat_model
from models.schemas import (
    AgentName,
    BugReport,
    Confidence,
    FindingSource,
    Intent,
    Issue,
    PromptTechnique,
    Severity,
)
from prompts.examples import BUG_FINDER_FEW_SHOT, format_few_shot_block
from prompts.system_prompts import BUG_FINDER_SYSTEM
from prompts.user_prompts import BUG_FINDER_USER, format_retrieved_context
from rag.retriever import policy_decision


class BugFinderAgent(BaseAgent):
    name = AgentName.BUG_FINDER

    def run(self, code: str, intent: Intent) -> BugReport:
        self.begin()
        self.trace.log("Parsing the submitted code with ast")

        self.record_tool("ast.parse")
        syntax_ok, syntax_error = check_syntax(code)
        if not syntax_ok:
            self.trace.log(f"Syntax error found by ast: {syntax_error}")
            report = BugReport(
                issues=[
                    Issue(
                        issue_id="SYNTAX-01",
                        title="The file is not valid Python",
                        severity=Severity.CRITICAL,
                        explanation=(
                            f"Python's own parser rejected this file: {syntax_error}. "
                            "Nothing else can be analysed until it parses."
                        ),
                        suggested_fix="Correct the syntax error reported above.",
                        confidence=Confidence.HIGH,
                        source=FindingSource.AST,
                    )
                ],
                summary=f"The file does not parse: {syntax_error}",
                syntax_valid=False,
                syntax_error=syntax_error,
            )
            self.finish(
                "Stopped at the syntax check - the file is not valid Python.",
                findings=1,
            )
            return report

        self.trace.log("Syntax is valid; proceeding to analysis")

        decision = policy_decision(
            intent,
            self.name,
            rag_enabled=self.session.rag_enabled,
            settings=self.settings,
        )
        self.record_retrieval(decision)

        try:
            chain = StructuredChain(
                system_prompt=BUG_FINDER_SYSTEM,
                user_template=BUG_FINDER_USER,
                output_model=BugReport,
                model=get_chat_model(self.settings, json_mode=True),
                agent=self.name,
                technique=PromptTechnique.FEW_SHOT,
                provider=self.settings.provider,
                model_name=self.settings.model_name,
            )
        except Exception as exc:
            self.fail(str(exc))
            raise

        self.record_technique(PromptTechnique.FEW_SHOT)
        self.trace.log(
            f"Invoking the few-shot chain with {len(BUG_FINDER_FEW_SHOT)} examples"
        )

        try:
            report, record = chain.invoke(
                {
                    "code": code,
                    "few_shot_examples": format_few_shot_block(),
                    "syntax_note": "",
                    "retrieved_context": format_retrieved_context(decision.passages),
                }
            )
        except Exception as exc:
            self.fail(f"{type(exc).__name__}: {exc}")
            raise

        record.few_shot_examples = [
            f"Example {index + 1}: {code_sample.strip().splitlines()[0]}"
            for index, (code_sample, _) in enumerate(BUG_FINDER_FEW_SHOT)
        ]
        record.retrieved_context = [p.text for p in decision.passages]
        self.record_prompt(record)

        report.syntax_valid = True
        report.syntax_error = None
        for index, issue in enumerate(report.issues, start=1):
            if not issue.issue_id:
                issue.issue_id = f"BUG-{index:02d}"
            issue.source = FindingSource.LLM
            if issue.line is not None and issue.line < 1:
                issue.line = None

        count = len(report.issues)
        self.trace.log(f"Model reported {count} issue(s)")
        self.finish(
            report.summary.strip()
            or (
                f"{count} issue(s) found."
                if count
                else "No defects found in the submitted code."
            ),
            findings=count,
        )
        return report
