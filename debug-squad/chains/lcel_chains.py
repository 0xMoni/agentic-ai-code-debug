"""LangChain Expression Language pipelines."""

from __future__ import annotations

import json
import re
import time
from typing import Any, TypeVar

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.outputs import Generation
from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from models.schemas import AgentName, PromptRecord, PromptTechnique

TModel = TypeVar("TModel", bound=BaseModel)


RATE_LIMIT_MAX_WAITS = 4
RATE_LIMIT_MAX_SLEEP_SECONDS = 30.0
_RETRY_AFTER = re.compile(r"try again in\s+(?:(\d+)m)?\s*([\d.]+)\s*(ms|s)", re.IGNORECASE)


def is_rate_limit_error(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    name = type(exc).__name__
    return name == "RateLimitError" or "rate limit" in str(exc).lower()


def retry_after_seconds(exc: BaseException) -> float:
    """How long the provider asked us to wait, with a safety margin."""
    match = _RETRY_AFTER.search(str(exc))
    if not match:
        return 8.0
    minutes = float(match.group(1) or 0)
    value = float(match.group(2))
    seconds = value / 1000 if match.group(3).lower() == "ms" else value
    return min(minutes * 60 + seconds + 0.75, RATE_LIMIT_MAX_SLEEP_SECONDS)


def provider_rejected_json(exc: BaseException) -> str | None:
    """If the provider refused the model's own JSON, return what it generated."""
    if "json_validate_failed" not in str(exc):
        return None
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and error.get("failed_generation"):
            return str(error["failed_generation"])
    match = re.search(r"'failed_generation':\s*'(.*)'\s*}\s*}?\s*$", str(exc), re.DOTALL)
    return match.group(1) if match else ""


def invoke_with_rate_limit(runnable: Runnable, payload: dict[str, Any], on_wait=None) -> Any:
    """Invoke, waiting out 429s as instructed by the provider."""
    for attempt in range(RATE_LIMIT_MAX_WAITS + 1):
        try:
            return runnable.invoke(payload)
        except Exception as exc:
            if not is_rate_limit_error(exc) or attempt == RATE_LIMIT_MAX_WAITS:
                raise
            delay = retry_after_seconds(exc)
            if on_wait:
                on_wait(delay)
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


def _strip_code_fences(text: str) -> str:
    """Remove ```json ..."""
    fenced = re.search(r"```(?:json|JSON)?\s*(.*?)```", text, re.DOTALL)
    return fenced.group(1).strip() if fenced else text.strip()


def _extract_json_object(text: str) -> str | None:
    """Find the first balanced JSON object in a blob of text."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def compact_format_instructions(model_cls: type[BaseModel]) -> str:
    """Build short, explicit JSON instructions from a Pydantic model."""
    schema = model_cls.model_json_schema()
    definitions = schema.get("$defs", {})

    def describe(prop: dict[str, Any], depth: int = 0) -> Any:
        """Return a JSON-serialisable description of one property."""
        if depth > 4:
            return "..."
        if "$ref" in prop:
            name = prop["$ref"].split("/")[-1]
            target = definitions.get(name, {})
            if "enum" in target:
                return "one of: " + " | ".join(str(v) for v in target["enum"])
            return describe_object(target, depth + 1)
        if "anyOf" in prop:
            nullable = any(o.get("type") == "null" for o in prop["anyOf"])
            options = [o for o in prop["anyOf"] if o.get("type") != "null"]
            if not options:
                return "null"
            described = describe(options[0], depth)
            if nullable and isinstance(described, str):
                return f"{described} or null"
            return described
        if "enum" in prop:
            return "one of: " + " | ".join(str(v) for v in prop["enum"])
        kind = prop.get("type", "any")
        if kind == "array":
            return [describe(prop.get("items", {}), depth + 1)]
        if kind == "object":
            return describe_object(prop, depth + 1)
        return kind

    def describe_object(obj: dict[str, Any], depth: int = 0) -> Any:
        properties = obj.get("properties", {})
        if not properties:
            return "object"
        return {key: describe(value, depth) for key, value in properties.items()}

    shape = describe_object(schema)
    return (
        "Return ONLY a JSON object with exactly this shape. The values below "
        "show the required type, or the exact set of allowed choices:\n"
        f"{json.dumps(shape, indent=2)}\n"
        "No markdown fences, no commentary before or after the JSON."
    )


class RepairingPydanticParser(PydanticOutputParser):
    """A PydanticOutputParser that salvages near-miss JSON."""

    trace: dict[str, Any] = {}

    def get_format_instructions(self) -> str:
        return compact_format_instructions(self.pydantic_object)

    def _attempt(self, text: str, partial: bool) -> Any:
        """Run the real PydanticOutputParser over a candidate string."""
        return super().parse_result([Generation(text=text)], partial=partial)

    # LCEL calls parse_result(), not parse(), so the salvage logic lives here.
    def parse_result(self, result: list[Generation], *, partial: bool = False) -> Any:
        """The method LangChain's Runnable pipeline actually calls."""
        text = result[0].text if result else ""
        self.trace["raw"] = text
        self.trace["repaired"] = False

        try:
            return self._attempt(text, partial)
        except (OutputParserException, ValueError):
            pass

        candidate = _strip_code_fences(text)
        if candidate and candidate != text:
            try:
                parsed = self._attempt(candidate, partial)
                self.trace["repaired"] = True
                self.trace["repair_method"] = "stripped markdown fences"
                return parsed
            except (OutputParserException, ValueError):
                pass

        extracted = _extract_json_object(candidate or text)
        if extracted:
            try:
                parsed = self._attempt(extracted, partial)
                self.trace["repaired"] = True
                self.trace["repair_method"] = "extracted embedded JSON object"
                return parsed
            except (OutputParserException, ValueError):
                pass

        raise OutputParserException(
            f"Model output could not be parsed as {self.pydantic_object.__name__}. "
            f"First 300 characters received: {text[:300]!r}",
            llm_output=text,
        )

    def parse(self, text: str) -> Any:
        """Convenience entry point; delegates to the same salvage logic."""
        return self.parse_result([Generation(text=text)])


REPAIR_SYSTEM = (
    "You convert malformed output into valid JSON. "
    "Return only the corrected JSON object. Do not explain anything."
)

REPAIR_USER = """This text was supposed to be a JSON object matching the schema below, but it could not be parsed.

Schema:
{format_instructions}

Text received:
{bad_output}

Error:
{error}

Return only the corrected JSON object."""


class StructuredChain:
    """One LCEL pipeline producing a validated Pydantic object."""

    def __init__(
        self,
        *,
        system_prompt: str,
        user_template: str,
        output_model: type[TModel],
        model: BaseChatModel,
        agent: AgentName,
        technique: PromptTechnique,
        provider: str = "",
        model_name: str = "",
        allow_llm_repair: bool = True,
    ) -> None:
        self.system_prompt = system_prompt
        self.user_template = user_template
        self.output_model = output_model
        self.model = model
        self.agent = agent
        self.technique = technique
        self.provider = provider
        self.model_name = model_name
        self.allow_llm_repair = allow_llm_repair

        self.parser = RepairingPydanticParser(
            pydantic_object=output_model, trace={}
        )
        self.prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessagePromptTemplate.from_template(
                    system_prompt, template_format="f-string"
                ),
                HumanMessagePromptTemplate.from_template(
                    user_template, template_format="f-string"
                ),
            ]
        )

        self.chain: Runnable = self.prompt | self.model | self.parser


    @property
    def pipeline_description(self) -> str:
        """Readable 'Prompt -> Model -> Parser' rendering for the UI."""
        return (
            "ChatPromptTemplate(system + human)\n"
            "    |\n"
            f"{type(self.model).__name__}(model={self.model_name or 'n/a'})\n"
            "    |\n"
            f"RepairingPydanticParser(PydanticOutputParser[{self.output_model.__name__}])"
        )

    @property
    def source_expression(self) -> str:
        return "self.chain = self.prompt | self.model | self.parser"

    def format_instructions(self) -> str:
        return self.parser.get_format_instructions()

    def _invoke_handling_rejected_json(self, payload: dict[str, Any]) -> Any:
        """Run the pipeline; if the provider rejects the JSON, retry then repair."""
        try:
            return invoke_with_rate_limit(self.chain, payload, self._note_wait)
        except Exception as exc:
            if provider_rejected_json(exc) is None:
                raise
            self.json_rejections += 1
        try:
            return invoke_with_rate_limit(self.chain, payload, self._note_wait)
        except Exception as exc:
            rejected = provider_rejected_json(exc)
            if rejected is None:
                raise
            self.json_rejections += 1
            self.parser.trace["raw"] = rejected
            raise OutputParserException(
                "The provider rejected the model's JSON twice (json_validate_failed).",
                llm_output=rejected,
            ) from exc

    def _note_wait(self, seconds: float) -> None:
        self.rate_limit_waits.append(round(seconds, 2))

    def _repair_with_llm(self, bad_output: str, error: str) -> Any:
        """Last resort: ask the model to fix its own malformed JSON."""
        repair_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", REPAIR_SYSTEM),
                ("human", REPAIR_USER),
            ]
        )
        repair_chain = repair_prompt | self.model | StrOutputParser()
        fixed = invoke_with_rate_limit(
            repair_chain,
            {
                "format_instructions": self.format_instructions(),
                "bad_output": bad_output[:2000],
                "error": error[:500],
            },
            self._note_wait,
        )
        return self.parser.parse(fixed)

    def invoke(self, inputs: dict[str, Any]) -> tuple[TModel, PromptRecord]:
        """Run the pipeline and return (parsed output, prompt record)."""
        payload = dict(inputs)
        payload.setdefault("format_instructions", self.format_instructions())

        rendered = ""
        try:
            rendered = self.prompt.format_messages(**payload)[-1].content
        except Exception:  # noqa: BLE001
            rendered = "(could not render prompt for display)"

        record = PromptRecord(
            agent=self.agent,
            technique=self.technique,
            system_prompt=self.system_prompt,
            user_prompt_template=self.user_template,
            rendered_user_prompt=str(rendered),
            output_parser=(
                f"RepairingPydanticParser(PydanticOutputParser"
                f"[{self.output_model.__name__}])"
            ),
            lcel_pipeline=self.pipeline_description,
            model=self.model_name,
            provider=self.provider,
        )

        self.parser.trace.clear()
        self.rate_limit_waits: list[float] = []
        self.json_rejections = 0
        started = time.perf_counter()
        original_raw: str | None = None
        try:
            result = self._invoke_handling_rejected_json(payload)
        except OutputParserException as exc:
            raw = self.parser.trace.get("raw", "")
            original_raw = raw
            if not self.allow_llm_repair:
                record.raw_output = raw
                record.error = str(exc)
                record.duration_seconds = time.perf_counter() - started
                raise
            try:
                result = self._repair_with_llm(raw, str(exc))
                self.parser.trace["repaired"] = True
                self.parser.trace["repair_method"] = "LLM repair call"
            except Exception as repair_exc:  # noqa: BLE001
                record.raw_output = raw
                record.error = f"{exc}  |  repair also failed: {repair_exc}"
                record.duration_seconds = time.perf_counter() - started
                raise OutputParserException(str(record.error), llm_output=raw) from exc
        except Exception as exc:
            record.raw_output = self.parser.trace.get("raw", "")
            record.error = f"{type(exc).__name__}: {exc}"
            record.duration_seconds = time.perf_counter() - started
            raise

        record.duration_seconds = time.perf_counter() - started
        record.raw_output = (
            original_raw if original_raw is not None
            else self.parser.trace.get("raw", "")
        )

        notes: list[str] = []
        if self.json_rejections:
            notes.append(
                f"provider rejected malformed JSON {self.json_rejections} time(s)"
            )
        if self.parser.trace.get("repaired"):
            notes.append(f"recovered: {self.parser.trace.get('repair_method', 'repaired')}")
        if self.rate_limit_waits:
            notes.append(
                f"rate-limited: waited {sum(self.rate_limit_waits):.1f}s over "
                f"{len(self.rate_limit_waits)} retry/retries, as the provider asked"
            )
        record.parse_repaired = bool(self.json_rejections or self.parser.trace.get("repaired"))
        if notes:
            record.error = "(" + "; ".join(notes) + ")"
        try:
            record.parsed_output = result.model_dump(mode="json")
        except Exception:  # noqa: BLE001
            record.parsed_output = None
        return result, record


class TextChain:
    """LCEL pipeline for prose output: prompt | model | StrOutputParser."""

    def __init__(
        self,
        *,
        system_prompt: str,
        user_template: str,
        model: BaseChatModel,
        agent: AgentName,
        technique: PromptTechnique,
        provider: str = "",
        model_name: str = "",
    ) -> None:
        self.system_prompt = system_prompt
        self.user_template = user_template
        self.model = model
        self.agent = agent
        self.technique = technique
        self.provider = provider
        self.model_name = model_name

        self.prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessagePromptTemplate.from_template(system_prompt),
                HumanMessagePromptTemplate.from_template(user_template),
            ]
        )
        self.parser = StrOutputParser()
        self.chain: Runnable = self.prompt | self.model | self.parser

    @property
    def pipeline_description(self) -> str:
        return (
            "ChatPromptTemplate(system + human)\n    |\n"
            f"{type(self.model).__name__}(model={self.model_name or 'n/a'})\n"
            "    |\n"
            "StrOutputParser()"
        )

    @property
    def source_expression(self) -> str:
        return "self.chain = self.prompt | self.model | self.parser"

    def invoke(self, inputs: dict[str, Any]) -> tuple[str, PromptRecord]:
        rendered = ""
        try:
            rendered = self.prompt.format_messages(**inputs)[-1].content
        except Exception:  # noqa: BLE001
            rendered = "(could not render prompt for display)"

        record = PromptRecord(
            agent=self.agent,
            technique=self.technique,
            system_prompt=self.system_prompt,
            user_prompt_template=self.user_template,
            rendered_user_prompt=str(rendered),
            output_parser="StrOutputParser()",
            lcel_pipeline=self.pipeline_description,
            model=self.model_name,
            provider=self.provider,
        )
        started = time.perf_counter()
        try:
            text = invoke_with_rate_limit(self.chain, inputs)
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"
            record.duration_seconds = time.perf_counter() - started
            raise
        record.duration_seconds = time.perf_counter() - started
        record.raw_output = text
        return text, record
