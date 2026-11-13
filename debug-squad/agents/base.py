"""Shared agent scaffolding."""

from __future__ import annotations

import ast
import time
from datetime import UTC, datetime

from config.settings import Settings, get_settings
from models.schemas import (
    AgentName,
    AgentStatus,
    AgentTrace,
    PromptRecord,
    PromptTechnique,
    RetrievalDecision,
    ReviewSession,
)


class BaseAgent:
    """Common lifecycle for the four agents and the explanation handler."""

    name: AgentName

    def __init__(
        self, session: ReviewSession, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.trace = AgentTrace(agent=self.name, summary="", status=AgentStatus.WAITING)
        self._started = 0.0


    def begin(self) -> AgentTrace:
        self.trace.status = AgentStatus.RUNNING
        self.trace.started_at = datetime.now(UTC)
        self._started = time.perf_counter()
        self.session.traces = [
            t for t in self.session.traces if t.agent != self.name
        ] + [self.trace]
        return self.trace

    def finish(self, summary: str, findings: int = 0) -> AgentTrace:
        self.trace.status = AgentStatus.COMPLETED
        self.trace.finished_at = datetime.now(UTC)
        self.trace.duration_seconds = round(time.perf_counter() - self._started, 3)
        self.trace.summary = summary
        self.trace.findings_count = findings
        return self.trace

    def fail(self, error: str) -> AgentTrace:
        self.trace.status = AgentStatus.FAILED
        self.trace.finished_at = datetime.now(UTC)
        self.trace.duration_seconds = round(time.perf_counter() - self._started, 3)
        self.trace.error = error
        self.trace.summary = "Failed"
        self.trace.log(f"FAILED: {error}")
        self.session.errors.append(f"{self.name.value}: {error}")
        return self.trace

    def skip(self, reason: str) -> AgentTrace:
        self.trace.status = AgentStatus.SKIPPED
        self.trace.skip_reason = reason
        self.trace.summary = "Skipped"
        self.trace.log(f"Skipped - {reason}")
        self.session.traces = [
            t for t in self.session.traces if t.agent != self.name
        ] + [self.trace]
        return self.trace


    def record_prompt(self, record: PromptRecord) -> None:
        self.session.prompts.append(record)
        if record.technique not in self.trace.techniques:
            self.trace.techniques.append(record.technique)

    def record_technique(self, technique: PromptTechnique) -> None:
        if technique not in self.trace.techniques:
            self.trace.techniques.append(technique)

    def record_tool(self, tool: str) -> None:
        """ReAct-style tool use: the agent decided to call a real tool."""
        if tool not in self.trace.tools_used:
            self.trace.tools_used.append(tool)

    def record_retrieval(self, decision: RetrievalDecision) -> None:
        self.session.retrievals.append(decision)
        if decision.should_retrieve and decision.passages:
            self.record_tool("knowledge_base")
            self.trace.log(
                f"Retrieved {len(decision.passages)} passage(s) for "
                f"query {decision.query!r}"
            )
        elif decision.should_retrieve and decision.error:
            self.trace.log(f"Retrieval attempted but failed: {decision.error}")
        else:
            self.trace.log(f"Retrieval skipped - {decision.reason}")


def check_syntax(code: str) -> tuple[bool, str | None]:
    """Parse the code with `ast`."""
    try:
        ast.parse(code)
        return True, None
    except SyntaxError as exc:
        location = f"line {exc.lineno}" + (
            f", column {exc.offset}" if exc.offset else ""
        )
        return False, f"{exc.msg} ({location})"
    except ValueError as exc:
        return False, str(exc)


def top_level_callables(code: str) -> list[str]:
    """Names the Test-Writer can import from the submitted module."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ) and not node.name.startswith("_"):
            names.append(node.name)
    return names
