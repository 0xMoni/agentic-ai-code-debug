"""LLM provider factory and health validation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests
from langchain_core.language_models.chat_models import BaseChatModel

from config.ollama_chat import ChatOllama
from config.settings import Settings, get_settings

GROQ_API_BASE = "https://api.groq.com/openai/v1"


@dataclass
class ProviderHealth:
    """Result of a live check against the configured provider."""

    ok: bool
    provider: str
    model: str
    detail: str
    models_available: list[str] = field(default_factory=list)
    latency_seconds: float | None = None
    configured: bool = True
    inference_verified: bool = False
    """True only when a real completion came back from the model."""

    @property
    def badge(self) -> str:
        if not self.configured:
            return "Not Configured"
        return "Connected" if self.ok else "Unavailable"


class LLMNotConfiguredError(RuntimeError):
    """Raised when an LLM call is attempted without working configuration."""


def get_chat_model(
    settings: Settings | None = None, *, json_mode: bool = False
) -> BaseChatModel:
    """Return a LangChain chat model for the configured provider."""
    settings = settings or get_settings()

    if settings.provider == "groq":
        if not settings.groq_api_key:
            raise LLMNotConfiguredError(
                "GROQ_API_KEY is not set. Get a free key at "
                "https://console.groq.com/keys, then add it to debug-squad/.env "
                "as GROQ_API_KEY=... and restart the app."
            )
        from langchain_groq import ChatGroq

        model_kwargs: dict = {}
        if json_mode:
            model_kwargs["response_format"] = {"type": "json_object"}

        return ChatGroq(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            temperature=settings.temperature,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
            model_kwargs=model_kwargs,
        )

    if settings.provider == "ollama":
        return ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=settings.temperature,
            timeout=settings.timeout_seconds,
            json_mode=json_mode,
        )

    raise LLMNotConfiguredError(
        f"Unknown LLM_PROVIDER '{settings.provider}'. Use 'groq' or 'ollama'."
    )


def list_models(settings: Settings | None = None) -> list[str]:
    """Fetch the live model list from the provider."""
    settings = settings or get_settings()

    if settings.provider == "groq":
        if not settings.groq_api_key:
            return []
        response = requests.get(
            f"{GROQ_API_BASE}/models",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        return sorted(
            m["id"]
            for m in data
            if m.get("id") and "whisper" not in m["id"] and "tts" not in m["id"]
        )

    response = requests.get(f"{settings.ollama_base_url}/api/tags", timeout=10)
    response.raise_for_status()
    return sorted(m["name"] for m in response.json().get("models", []))


def check_provider(
    settings: Settings | None = None, *, run_inference: bool = False
) -> ProviderHealth:
    """Probe the provider."""
    settings = settings or get_settings()
    provider = settings.provider
    model = settings.model_name

    if not settings.has_credentials:
        return ProviderHealth(
            ok=False,
            provider=provider,
            model=model,
            configured=False,
            detail=(
                "GROQ_API_KEY is not set. Add a free key from "
                "https://console.groq.com/keys to debug-squad/.env"
            ),
        )

    try:
        models = list_models(settings)
    except requests.exceptions.ConnectionError:
        hint = (
            "No internet connection to api.groq.com."
            if provider == "groq"
            else f"Ollama is not running at {settings.ollama_base_url}. "
            "Start it with `ollama serve`."
        )
        return ProviderHealth(
            ok=False, provider=provider, model=model, detail=hint
        )
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 401:
            detail = "Groq rejected the API key (401). Check GROQ_API_KEY in .env."
        elif status == 429:
            detail = "Groq rate limit reached (429). Wait a moment and retry."
        else:
            detail = f"Provider returned HTTP {status}."
        return ProviderHealth(
            ok=False, provider=provider, model=model, detail=detail
        )
    except Exception as exc:  # noqa: BLE001
        return ProviderHealth(
            ok=False,
            provider=provider,
            model=model,
            detail=f"{type(exc).__name__}: {exc}",
        )

    if models and model not in models:
        return ProviderHealth(
            ok=False,
            provider=provider,
            model=model,
            models_available=models,
            detail=(
                f"Model '{model}' is not available to this account. "
                f"Pick one of: {', '.join(models[:6])}"
                + ("..." if len(models) > 6 else "")
            ),
        )

    if not run_inference:
        return ProviderHealth(
            ok=True,
            provider=provider,
            model=model,
            models_available=models,
            detail=f"{provider} reachable; model '{model}' is available.",
        )

    started = time.perf_counter()
    try:
        chat = get_chat_model(settings)
        reply = chat.invoke("Reply with the single word: ready")
        elapsed = time.perf_counter() - started
        text = str(reply.content).strip()
        return ProviderHealth(
            ok=True,
            provider=provider,
            model=model,
            models_available=models,
            latency_seconds=elapsed,
            inference_verified=bool(text),
            detail=f"Live inference succeeded in {elapsed:.2f}s. Model replied: {text[:60]!r}",
        )
    except Exception as exc:  # noqa: BLE001
        return ProviderHealth(
            ok=False,
            provider=provider,
            model=model,
            models_available=models,
            latency_seconds=time.perf_counter() - started,
            detail=f"Inference failed - {type(exc).__name__}: {exc}",
        )
