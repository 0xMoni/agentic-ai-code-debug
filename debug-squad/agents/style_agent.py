"""Style/Lint."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from agents.base import BaseAgent
from chains.lcel_chains import StructuredChain
from config.llm_provider import get_chat_model
from models.schemas import (
    AgentName,
    Confidence,
    FindingSource,
    Intent,
    Issue,
    PromptTechnique,
    Severity,
    StyleReport,
    StyleSuggestion,
)
from prompts.system_prompts import STYLE_SYSTEM
from prompts.user_prompts import (
    STYLE_USER,
    format_retrieved_context,
    format_ruff_findings,
)
from rag.retriever import policy_decision


class _StyleSuggestions(BaseModel):
    """The slice of StyleReport the model is actually asked to produce."""

    suggestions: list[StyleSuggestion] = Field(default_factory=list)
    summary: str = ""

SUBMITTED_CODE_RULES = [
    "E",
    "W",
    "F",
    "N",
    "UP",
    "B",
    "C4",
    "SIM",
    "RET",
    "D200", "D201", "D202", "D205", "D400", "D403",
]

_SEVERITY_BY_PREFIX = {
    "F8": Severity.HIGH,
    "F4": Severity.LOW,
    "B": Severity.MEDIUM,
    "N": Severity.LOW,
    "E7": Severity.MEDIUM,
    "E9": Severity.CRITICAL,
    "RET": Severity.LOW,
    "SIM": Severity.LOW,
    "C4": Severity.LOW,
    "UP": Severity.LOW,
    "D": Severity.INFO,
    "W": Severity.INFO,
    "E": Severity.INFO,
}


def _severity_for(code: str) -> Severity:
    for prefix in sorted(_SEVERITY_BY_PREFIX, key=len, reverse=True):
        if code.startswith(prefix):
            return _SEVERITY_BY_PREFIX[prefix]
    return Severity.LOW


def run_ruff(code: str, python_executable: str) -> tuple[list[Issue], str | None]:
    """Run Ruff over the submitted code."""
    with tempfile.TemporaryDirectory(prefix="debugsquad-ruff-") as directory:
        path = Path(directory) / "submitted_code.py"
        path.write_text(code, encoding="utf-8")

        command = [
            python_executable, "-m", "ruff", "check",
            "--output-format", "json",
            "--no-cache",
            "--isolated",
            "--preview",
            "--select", ",".join(SUBMITTED_CODE_RULES),
            str(path),
        ]
        try:
            completed = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=30
            )
        except subprocess.TimeoutExpired:
            return [], "Ruff timed out after 30 seconds."
        except OSError as exc:
            return [], f"Ruff could not be started: {exc}"

        if completed.returncode not in (0, 1):
            detail = (completed.stderr or completed.stdout or "").strip()[:400]
            return [], f"Ruff exited with code {completed.returncode}: {detail}"

        try:
            raw = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            return [], f"Ruff output could not be parsed: {exc}"

    issues: list[Issue] = []
    for index, entry in enumerate(raw, start=1):
        rule_code = entry.get("code") or "RUFF"
        location = entry.get("location") or {}
        fix = entry.get("fix") or {}
        issues.append(
            Issue(
                issue_id=f"STYLE-{index:02d}",
                title=f"{entry.get('message', 'Style violation')}",
                severity=_severity_for(rule_code),
                line=location.get("row"),
                explanation=(
                    f"Ruff rule {rule_code} fired at line {location.get('row', '?')}, "
                    f"column {location.get('column', '?')}: "
                    f"{entry.get('message', '')}"
                ),
                suggested_fix=fix.get("message") or None,
                confidence=Confidence.HIGH,
                source=FindingSource.RUFF,
                rule_code=rule_code,
            )
        )
    return issues, None


class StyleAgent(BaseAgent):
    name = AgentName.STYLE_LINT

    def run(self, code: str, intent: Intent) -> StyleReport:
        self.begin()

        self.trace.log("Invoking ruff (deterministic linter)")
        self.record_tool("ruff")
        ruff_issues, ruff_error = run_ruff(code, self.settings.python_executable)
        if ruff_error:
            self.trace.log(f"Ruff unavailable: {ruff_error}")
        else:
            self.trace.log(f"Ruff reported {len(ruff_issues)} violation(s)")

        decision = policy_decision(
            intent,
            self.name,
            rag_enabled=self.session.rag_enabled,
            settings=self.settings,
        )
        self.record_retrieval(decision)

        report = StyleReport(
            ruff_issues=ruff_issues,
            ruff_available=ruff_error is None,
            ruff_error=ruff_error,
        )

        try:
            chain = StructuredChain(
                system_prompt=STYLE_SYSTEM,
                user_template=STYLE_USER,
                output_model=_StyleSuggestions,
                model=get_chat_model(self.settings, json_mode=True),
                agent=self.name,
                technique=PromptTechnique.ZERO_SHOT,
                provider=self.settings.provider,
                model_name=self.settings.model_name,
            )
            self.record_technique(PromptTechnique.ZERO_SHOT)
            suggestions, record = chain.invoke(
                {
                    "code": code,
                    "ruff_findings": format_ruff_findings(ruff_issues),
                    "retrieved_context": format_retrieved_context(decision.passages),
                }
            )
            record.retrieved_context = [p.text for p in decision.passages]
            self.record_prompt(record)

            report.suggestions = suggestions.suggestions
            for index, suggestion in enumerate(report.suggestions, start=1):
                if not suggestion.suggestion_id:
                    suggestion.suggestion_id = f"SUGG-{index:02d}"
            report.summary = suggestions.summary.strip()
            self.trace.log(f"Model added {len(report.suggestions)} suggestion(s)")
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            self.trace.log(f"LLM style review failed: {message}")
            self.session.errors.append(f"{self.name.value} (LLM part): {message}")
            report.summary = (
                f"Ruff found {len(ruff_issues)} violation(s). The LLM readability "
                f"review could not be completed ({message})."
            )
            self.finish(report.summary, findings=len(ruff_issues))
            return report

        if not report.summary:
            report.summary = (
                f"Ruff: {len(ruff_issues)} violation(s). "
                f"LLM: {len(report.suggestions)} readability suggestion(s)."
            )
        self.finish(report.summary, findings=len(ruff_issues) + len(report.suggestions))
        return report

