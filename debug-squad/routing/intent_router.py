"""Intent detection and workflow routing."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from chains.lcel_chains import StructuredChain
from config.llm_provider import get_chat_model
from config.settings import Settings, get_settings
from models.schemas import (
    AgentName,
    Confidence,
    Intent,
    IntentDecision,
    PromptRecord,
    PromptTechnique,
)
from prompts.system_prompts import INTENT_ROUTER_SYSTEM
from prompts.user_prompts import INTENT_ROUTER_USER, code_preview

WORKFLOWS: dict[Intent, list[AgentName]] = {
    Intent.FULL_REVIEW: [
        AgentName.BUG_FINDER,
        AgentName.STYLE_LINT,
        AgentName.FIXER,
        AgentName.TEST_WRITER,
    ],
    Intent.DEBUGGING: [AgentName.BUG_FINDER, AgentName.FIXER],
    Intent.STYLE_IMPROVEMENT: [AgentName.STYLE_LINT, AgentName.FIXER],
    Intent.CODE_EXPLANATION: [AgentName.EXPLAINER],
    Intent.TEST_GENERATION: [AgentName.TEST_WRITER],
}

WORKFLOW_LABELS: dict[Intent, str] = {
    Intent.FULL_REVIEW: "Complete multi-agent workflow",
    Intent.DEBUGGING: "Bug-Finder then Fixer",
    Intent.STYLE_IMPROVEMENT: "Style/Lint then Fixer",
    Intent.CODE_EXPLANATION: "Explanation handler only",
    Intent.TEST_GENERATION: "Test-Writer and sandbox execution",
}

INTENT_LABELS: dict[Intent, str] = {
    Intent.FULL_REVIEW: "Full Code Review",
    Intent.DEBUGGING: "Debugging",
    Intent.STYLE_IMPROVEMENT: "Style Improvement",
    Intent.CODE_EXPLANATION: "Code Explanation",
    Intent.TEST_GENERATION: "Test Generation",
}


def agents_for(intent: Intent) -> list[AgentName]:
    return list(WORKFLOWS.get(intent, WORKFLOWS[Intent.FULL_REVIEW]))


class IntentClassification(BaseModel):
    """What the model is asked to return."""

    intent: Intent = Field(description="One of the five supported intents")
    confidence: Confidence = Field(description="How sure you are")
    explanation: str = Field(description="One sentence on why you chose it")


KEYWORD_RULES: list[tuple[Intent, list[str]]] = [
    (
        Intent.FULL_REVIEW,
        [
            r"\breview (and fix )?everything\b",
            r"\bfull review\b",
            r"\bcomplete review\b",
            r"\breview this code\b",
            r"\bcheck everything\b",
            r"\bfix everything\b",
        ],
    ),
    (
        Intent.TEST_GENERATION,
        [
            r"\btests?\b",
            r"\bunit test",
            r"\bpytest\b",
            r"\btest cases?\b",
            r"\bedge[- ]case tests?\b",
            r"\bwrite tests?\b",
        ],
    ),
    (
        Intent.CODE_EXPLANATION,
        [
            r"\bexplain\b",
            r"\bwhat does (this|the code|it) do\b",
            r"\bhow does (this|it) work\b",
            r"\bwalk me through\b",
            r"\bdescribe what\b",
            r"\bunderstand (this|the) code\b",
        ],
    ),
    (
        Intent.STYLE_IMPROVEMENT,
        [
            r"\breadab",
            r"\bstyle\b",
            r"\bclean(er)? ?(up|code)?\b",
            r"\bformat",
            r"\bnaming\b",
            r"\blint",
            r"\bpep ?8\b",
            r"\brefactor\b",
            r"\btidy\b",
            r"\bmaintainab",
        ],
    ),
    (
        Intent.DEBUGGING,
        [
            r"\bbugs?\b",
            r"\bdebug\b",
            r"\berror\b",
            r"\bbroken\b",
            r"\bcrash",
            r"\bfails?\b",
            r"\bwrong\b",
            r"\bexception\b",
            r"\bwhy (does|is).*(not work|failing|wrong)\b",
            r"\bfind the (bug|problem|issue)\b",
            r"\bnot working\b",
        ],
    ),
]


def keyword_intent(user_request: str) -> tuple[Intent, Confidence, str]:
    """Deterministic intent detection."""
    text = (user_request or "").lower().strip()

    if not text:
        return (
            Intent.FULL_REVIEW,
            Confidence.MEDIUM,
            "No instruction was given, so the default full review was selected.",
        )

    matched: list[tuple[Intent, str]] = []
    for intent, patterns in KEYWORD_RULES:
        for pattern in patterns:
            if re.search(pattern, text):
                matched.append((intent, pattern))
                break

    if not matched:
        return (
            Intent.FULL_REVIEW,
            Confidence.LOW,
            f"No routing keyword matched {user_request.strip()!r}, so the "
            "default full review was selected.",
        )

    intent, pattern = matched[0]
    if len(matched) == 1:
        confidence = Confidence.HIGH
        note = f"matched the pattern /{pattern}/"
    else:
        confidence = Confidence.MEDIUM
        others = ", ".join(i.value for i, _ in matched[1:])
        note = (
            f"matched /{pattern}/ first; the request also hinted at {others}, "
            "so the more specific intent won"
        )
    return (
        intent,
        confidence,
        f"Keyword routing: the request {note}.",
    )


class IntentRouter:
    """Detects intent and selects the workflow."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()


    def manual(self, intent: Intent, user_request: str = "") -> IntentDecision:
        """The user picked the intent themselves in the workspace."""
        return IntentDecision(
            intent=intent,
            confidence=Confidence.HIGH,
            method="manual_override",
            user_request=user_request,
            agents=agents_for(intent),
            explanation=(
                f"Intent set manually to '{INTENT_LABELS[intent]}'. Automatic "
                "detection was bypassed, so the workflow is "
                f"{WORKFLOW_LABELS[intent]}."
            ),
        )

    def fallback(self, user_request: str, note: str = "") -> IntentDecision:
        """Deterministic routing, with an optional note on why it was used."""
        intent, confidence, explanation = keyword_intent(user_request)
        if note:
            explanation = f"{explanation} ({note})"
        return IntentDecision(
            intent=intent,
            confidence=confidence,
            method="keyword_fallback",
            user_request=user_request,
            agents=agents_for(intent),
            explanation=explanation,
        )

    def build_chain(self) -> StructuredChain:
        """The zero-shot LCEL classification chain."""
        return StructuredChain(
            system_prompt=INTENT_ROUTER_SYSTEM,
            user_template=INTENT_ROUTER_USER,
            output_model=IntentClassification,
            model=get_chat_model(self.settings, json_mode=True),
            agent=AgentName.BUG_FINDER,
            technique=PromptTechnique.ZERO_SHOT,
            provider=self.settings.provider,
            model_name=self.settings.model_name,
        )

    def detect(
        self, code: str, user_request: str = ""
    ) -> tuple[IntentDecision, PromptRecord | None]:
        """Detect intent via the LLM, falling back to keywords on any failure."""
        try:
            chain = self.build_chain()
        except Exception as exc:  # noqa: BLE001
            return (
                self.fallback(
                    user_request,
                    note=f"the LLM was unavailable: {type(exc).__name__}",
                ),
                None,
            )

        try:
            classification, record = chain.invoke(
                {
                    "user_request": user_request.strip()
                    or "(no instruction given - treat as a general review)",
                    "code_preview": code_preview(code),
                }
            )
        except Exception as exc:  # noqa: BLE001
            return (
                self.fallback(
                    user_request,
                    note=(
                        "LLM classification failed, so deterministic routing was "
                        f"used instead: {type(exc).__name__}"
                    ),
                ),
                None,
            )

        decision = IntentDecision(
            intent=classification.intent,
            confidence=classification.confidence,
            method="llm_zero_shot",
            user_request=user_request,
            agents=agents_for(classification.intent),
            explanation=(
                f"{classification.explanation.strip().rstrip('.')}. "
                f"Routed to: {WORKFLOW_LABELS[classification.intent]}."
            ),
        )
        record.technique = PromptTechnique.ZERO_SHOT
        return decision, record

    def route(
        self,
        code: str,
        user_request: str = "",
        *,
        auto: bool = True,
        manual_intent: Intent | None = None,
    ) -> tuple[IntentDecision, PromptRecord | None]:
        """Entry point used by the orchestrator."""
        if not auto and manual_intent is not None:
            return self.manual(manual_intent, user_request), None
        return self.detect(code, user_request)
