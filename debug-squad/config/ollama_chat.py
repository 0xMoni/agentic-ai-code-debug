"""A minimal LangChain chat model for Ollama."""

from __future__ import annotations

from typing import Any

import requests
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult


def _role_for(message: BaseMessage) -> str:
    if isinstance(message, SystemMessage):
        return "system"
    if isinstance(message, AIMessage):
        return "assistant"
    if isinstance(message, HumanMessage):
        return "user"
    return "user"


class ChatOllama(BaseChatModel):
    """Chat model backed by a local Ollama server's /api/chat endpoint."""

    model: str = "gemma3:1b"
    base_url: str = "http://127.0.0.1:11434"
    temperature: float = 0.1
    timeout: int = 120
    json_mode: bool = False
    """When True, ask Ollama to constrain output to valid JSON."""

    @property
    def _llm_type(self) -> str:
        return "ollama-chat"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model, "base_url": self.base_url}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": _role_for(m), "content": str(m.content)} for m in messages
            ],
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if stop:
            payload["options"]["stop"] = stop
        if self.json_mode:
            payload["format"] = "json"

        try:
            response = requests.post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {self.base_url}. "
                "Start it with `ollama serve` in a terminal."
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                f"Ollama timed out after {self.timeout}s. Try a smaller model."
            ) from exc
        except requests.exceptions.HTTPError as exc:
            detail = ""
            try:
                detail = response.json().get("error", "")
            except Exception:  # noqa: BLE001
                detail = response.text[:300]
            raise RuntimeError(f"Ollama returned an error: {detail}") from exc

        content = (data.get("message") or {}).get("content", "")
        message = AIMessage(content=content)
        return ChatResult(generations=[ChatGeneration(message=message)])
