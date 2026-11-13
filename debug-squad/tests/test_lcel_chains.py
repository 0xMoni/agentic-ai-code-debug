"""LCEL pipelines and structured output parsing."""

from __future__ import annotations

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from chains.lcel_chains import (
    RepairingPydanticParser,
    StructuredChain,
    TextChain,
    _extract_json_object,
    compact_format_instructions,
)
from models.schemas import AgentName, BugReport, PromptTechnique
from tests.conftest import bug_report_json
from tests.fake_llm import fake_model

TEMPLATE = "Analyse this:\n{code}\n{format_instructions}"


def build(model, allow_repair: bool = True) -> StructuredChain:
    return StructuredChain(
        system_prompt="You are a test agent.",
        user_template=TEMPLATE,
        output_model=BugReport,
        model=model,
        agent=AgentName.BUG_FINDER,
        technique=PromptTechnique.FEW_SHOT,
        provider="test",
        model_name="test-model",
        allow_llm_repair=allow_repair,
    )


def test_the_chain_is_a_real_lcel_runnable():
    chain = build(fake_model(bug_report_json()))
    assert isinstance(chain.chain, Runnable)
    assert isinstance(chain.prompt, ChatPromptTemplate)
    assert isinstance(chain.parser, PydanticOutputParser)
    assert chain.source_expression == "self.chain = self.prompt | self.model | self.parser"


def test_prompt_has_both_system_and_human_messages():
    chain = build(fake_model(bug_report_json()))
    messages = chain.prompt.format_messages(
        code="x = 1", format_instructions="(schema)"
    )
    assert [m.type for m in messages] == ["system", "human"]
    assert "You are a test agent." in messages[0].content
    assert "x = 1" in messages[1].content


def test_text_chain_uses_a_string_output_parser():
    chain = TextChain(
        system_prompt="s",
        user_template="{code} {user_request}",
        model=fake_model("## It adds two numbers"),
        agent=AgentName.EXPLAINER,
        technique=PromptTechnique.ZERO_SHOT,
    )
    text, record = chain.invoke({"code": "x", "user_request": "explain"})
    assert text.startswith("## It adds")
    assert record.output_parser == "StrOutputParser()"
    assert "StrOutputParser" in chain.pipeline_description


def test_clean_json_parses_with_one_model_call():
    model = fake_model(bug_report_json(2))
    report, record = build(model).invoke({"code": "x = 1"})
    assert len(report.issues) == 2
    assert model.call_count == 1
    assert record.parse_repaired is False
    assert record.parsed_output is not None


def test_markdown_fenced_json_is_recovered():
    payload = f"Here you go:\n```json\n{bug_report_json()}\n```"
    model = fake_model(payload)
    report, _ = build(model).invoke({"code": "x = 1"})
    assert len(report.issues) == 1
    assert model.call_count == 1, "recovery must not cost an extra model call"


def test_json_embedded_in_prose_is_recovered_locally():
    payload = f"Sure! The analysis is {bug_report_json()} Hope that helps."
    model = fake_model(payload)
    report, record = build(model).invoke({"code": "x = 1"})
    assert len(report.issues) == 1
    assert model.call_count == 1
    assert record.parse_repaired is True
    assert "extracted embedded JSON" in (record.error or "")


def test_unsalvageable_output_triggers_one_repair_call():
    model = fake_model("no json whatsoever", bug_report_json())
    report, record = build(model).invoke({"code": "x = 1"})
    assert len(report.issues) == 1
    assert model.call_count == 2
    assert record.parse_repaired is True


def test_repair_disabled_raises_instead_of_calling_again():
    model = fake_model("no json whatsoever")
    with pytest.raises(OutputParserException):
        build(model, allow_repair=False).invoke({"code": "x = 1"})
    assert model.call_count == 1


def test_persistent_garbage_eventually_raises():
    model = fake_model("nope", "still nope")
    with pytest.raises(OutputParserException):
        build(model).invoke({"code": "x = 1"})


def test_model_failure_is_recorded_then_reraised():
    model = fake_model(bug_report_json())
    model.fail_with = RuntimeError("connection reset")
    with pytest.raises(RuntimeError):
        build(model).invoke({"code": "x = 1"})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('prefix {"a": 1} suffix', '{"a": 1}'),
        ('{"a": {"b": 2}} trailing', '{"a": {"b": 2}}'),
        ('{"a": "}"}', '{"a": "}"}'),
        ('{"a": "\\""}', '{"a": "\\""}'),
        ("no object here", None),
    ],
)
def test_json_object_extraction(text, expected):
    assert _extract_json_object(text) == expected


def test_format_instructions_are_compact_and_list_enum_choices():
    instructions = compact_format_instructions(BugReport)
    assert len(instructions) < 1500, "instructions must stay inside the token budget"
    assert "one of: critical | high | medium | low | info" in instructions
    assert "issues" in instructions
    default = PydanticOutputParser(
        pydantic_object=BugReport
    ).get_format_instructions()
    assert len(instructions) < len(default)


def test_parser_records_the_raw_model_text():
    parser = RepairingPydanticParser(pydantic_object=BugReport, trace={})
    parser.parse(bug_report_json())
    assert parser.trace["raw"] == bug_report_json()
    assert parser.trace["repaired"] is False


class _RateLimited(Exception):
    status_code = 429


def test_retry_after_is_read_from_the_provider_message():
    from chains.lcel_chains import retry_after_seconds

    exc = _RateLimited("Rate limit reached ... Please try again in 5.745s. Need more")
    assert 6.0 < retry_after_seconds(exc) < 7.0
    assert retry_after_seconds(_RateLimited("try again in 250ms")) < 2
    assert retry_after_seconds(_RateLimited("try again in 1m30s")) == 30.0


def test_a_rate_limited_call_waits_and_succeeds(monkeypatch):
    import chains.lcel_chains as lc

    slept: list[float] = []
    monkeypatch.setattr(lc.time, "sleep", slept.append)

    model = fake_model(bug_report_json())
    calls = {"n": 0}
    original = model._generate

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _RateLimited("Please try again in 2s.")
        return original(*args, **kwargs)

    object.__setattr__(model, "_generate", flaky)
    report, record = build(model).invoke({"code": "x = 1"})
    assert len(report.issues) == 1
    assert len(slept) == 1 and 2.0 < slept[0] < 3.5
    assert "rate-limited" in (record.error or "")


def test_non_rate_limit_errors_are_not_retried(monkeypatch):
    import chains.lcel_chains as lc

    monkeypatch.setattr(lc.time, "sleep", lambda s: pytest.fail("must not sleep"))
    model = fake_model(bug_report_json())
    model.fail_with = RuntimeError("connection reset")
    with pytest.raises(RuntimeError):
        build(model).invoke({"code": "x = 1"})


class _JsonRejected(Exception):
    def __init__(self, generated: str):
        super().__init__("Error code: 400 - json_validate_failed")
        self.body = {"error": {"code": "json_validate_failed",
                               "failed_generation": generated}}


def _model_raising(*errors_then_ok):
    model = fake_model(*[r for r in errors_then_ok if isinstance(r, str)])
    queue = [e for e in errors_then_ok if not isinstance(e, str)]
    original = model._generate

    def generate(*args, **kwargs):
        if queue:
            raise queue.pop(0)
        return original(*args, **kwargs)

    object.__setattr__(model, "_generate", generate)
    return model


def test_provider_json_rejection_is_retried_once():
    model = _model_raising(_JsonRejected('{"broken"'), bug_report_json())
    report, record = build(model).invoke({"code": "x = 1"})
    assert len(report.issues) == 1
    assert "rejected malformed JSON 1" in (record.error or "")


def test_repeated_rejection_hands_the_text_to_the_repair_path():
    model = _model_raising(
        _JsonRejected('{"issues": [}'), _JsonRejected('{"issues": [}'),
        bug_report_json(),
    )
    report, record = build(model).invoke({"code": "x = 1"})
    assert len(report.issues) == 1
    assert record.parse_repaired is True
    assert record.raw_output == '{"issues": [}'
