"""Intent detection and workflow routing."""

from __future__ import annotations

import pytest

from models.schemas import AgentName, Confidence, Intent
from routing.intent_router import (
    WORKFLOWS,
    IntentRouter,
    agents_for,
    keyword_intent,
)
from tests.conftest import intent_json


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("Find the bug in this code", Intent.DEBUGGING),
        ("why is this broken", Intent.DEBUGGING),
        ("it crashes on empty input", Intent.DEBUGGING),
        ("Improve readability", Intent.STYLE_IMPROVEMENT),
        ("fix the naming and formatting", Intent.STYLE_IMPROVEMENT),
        ("refactor this please", Intent.STYLE_IMPROVEMENT),
        ("Explain this code", Intent.CODE_EXPLANATION),
        ("what does this do?", Intent.CODE_EXPLANATION),
        ("walk me through it", Intent.CODE_EXPLANATION),
        ("Generate edge-case tests", Intent.TEST_GENERATION),
        ("write pytest tests for this", Intent.TEST_GENERATION),
        ("Review and fix everything", Intent.FULL_REVIEW),
        ("full review please", Intent.FULL_REVIEW),
    ],
)
def test_keyword_routing_matches_the_documented_examples(request_text, expected):
    intent, _confidence, explanation = keyword_intent(request_text)
    assert intent is expected
    assert explanation


def test_empty_request_defaults_to_full_review():
    intent, confidence, explanation = keyword_intent("")
    assert intent is Intent.FULL_REVIEW
    assert confidence is Confidence.MEDIUM
    assert "No instruction" in explanation


def test_unrecognised_request_falls_back_with_low_confidence():
    intent, confidence, _ = keyword_intent("please make it nicer somehow")
    assert intent is Intent.FULL_REVIEW
    assert confidence is Confidence.LOW


def test_every_intent_has_a_workflow():
    for intent in Intent:
        assert WORKFLOWS[intent], f"{intent} has no agents"


def test_workflows_select_different_agents_per_intent():
    """Routing must actually change which agents run, not just a label."""
    assert agents_for(Intent.FULL_REVIEW) == [
        AgentName.BUG_FINDER,
        AgentName.STYLE_LINT,
        AgentName.FIXER,
        AgentName.TEST_WRITER,
    ]
    assert agents_for(Intent.CODE_EXPLANATION) == [AgentName.EXPLAINER]
    assert agents_for(Intent.TEST_GENERATION) == [AgentName.TEST_WRITER]
    assert AgentName.BUG_FINDER not in agents_for(Intent.STYLE_IMPROVEMENT)
    assert AgentName.STYLE_LINT not in agents_for(Intent.DEBUGGING)


def test_agents_for_returns_a_copy():
    first = agents_for(Intent.FULL_REVIEW)
    first.clear()
    assert agents_for(Intent.FULL_REVIEW), "WORKFLOWS was mutated by a caller"


def test_manual_override_is_recorded_as_such(settings):
    decision = IntentRouter(settings).manual(Intent.TEST_GENERATION, "ignored")
    assert decision.method == "manual_override"
    assert decision.intent is Intent.TEST_GENERATION
    assert decision.agents == [AgentName.TEST_WRITER]
    assert decision.confidence is Confidence.HIGH


def test_llm_detection_used_when_available(settings, patch_llm):
    patch_llm(intent_json("style_improvement"))
    decision, record = IntentRouter(settings).detect("def f(): pass", "tidy this up")
    assert decision.method == "llm_zero_shot"
    assert decision.intent is Intent.STYLE_IMPROVEMENT
    assert record is not None
    assert record.technique.value == "zero_shot"
    assert record.system_prompt
    assert record.rendered_user_prompt


def test_llm_failure_falls_back_to_keywords(settings, patch_llm):
    patch_llm("this is not json and never will be")
    decision, record = IntentRouter(settings).detect("def f(): pass", "find the bug")
    assert decision.method == "keyword_fallback"
    assert decision.intent is Intent.DEBUGGING
    assert "LLM classification failed" in decision.explanation
    assert record is None


def test_missing_credentials_falls_back_without_crashing():
    from config.settings import Settings

    unconfigured = Settings(provider="groq", groq_api_key="")
    decision, record = IntentRouter(unconfigured).detect("x = 1", "explain this")
    assert decision.method == "keyword_fallback"
    assert decision.intent is Intent.CODE_EXPLANATION
    assert record is None
