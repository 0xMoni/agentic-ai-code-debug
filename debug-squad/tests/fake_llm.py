"""A scripted chat model for tests."""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeChatModel(BaseChatModel):
    """Returns queued responses in order; repeats the last one when exhausted."""

    responses: list[str] = []
    calls: list[list[str]] = []
    fail_with: Exception | None = None

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append([str(m.content) for m in messages])
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        text = self.responses[index] if self.responses else "{}"
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=text))]
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def last_prompt(self) -> str:
        return "\n".join(self.calls[-1]) if self.calls else ""


def fake_model(*responses: str) -> FakeChatModel:
    return FakeChatModel(responses=list(responses), calls=[])
