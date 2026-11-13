"""Environment-based configuration for Debug Squad."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env", override=False)


DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_OLLAMA_MODEL = "gemma3:1b"


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass
class Settings:
    """Resolved application settings."""

    provider: str = "groq"
    groq_api_key: str = ""
    groq_model: str = DEFAULT_GROQ_MODEL
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    temperature: float = 0.1
    timeout_seconds: int = 90
    max_retries: int = 2

    max_fix_attempts: int = 3

    sandbox_backend: str = "auto"
    sandbox_timeout_seconds: int = 20
    sandbox_memory_mb: int = 512
    sandbox_cpu_seconds: int = 10

    rag_enabled: bool = True
    chroma_persist_dir: str = "chroma_db"
    chroma_collection: str = "debug_squad_kb"
    rag_top_k: int = 3

    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)


    @property
    def model_name(self) -> str:
        """The model that will actually be used, given the provider."""
        return self.groq_model if self.provider == "groq" else self.ollama_model

    @property
    def has_credentials(self) -> bool:
        """True when the active provider has what it needs to run."""
        if self.provider == "groq":
            return bool(self.groq_api_key)
        return True

    @property
    def persist_path(self) -> Path:
        p = Path(self.chroma_persist_dir)
        return p if p.is_absolute() else self.project_root / p

    @property
    def history_path(self) -> Path:
        return self.project_root / "history"

    @property
    def knowledge_base_path(self) -> Path:
        return self.project_root / "rag" / "knowledge_base"


    @property
    def python_executable(self) -> str:
        """Interpreter used for sandboxed runs and for `-m ruff`."""
        return sys.executable

    def masked_groq_key(self) -> str:
        """Never show a full secret - not in the UI, not in a report."""
        key = self.groq_api_key
        if not key:
            return "(not set)"
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}{'*' * 12}{key[-4:]}"


def load_settings() -> Settings:
    """Build a Settings object from the current environment."""
    return Settings(
        provider=_env("LLM_PROVIDER", "groq").lower(),
        groq_api_key=_env("GROQ_API_KEY"),
        groq_model=_env("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        ollama_base_url=_env("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=_env("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        temperature=_env_float("LLM_TEMPERATURE", 0.1),
        timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 90),
        max_retries=_env_int("LLM_MAX_RETRIES", 2),
        max_fix_attempts=_env_int("MAX_FIX_ATTEMPTS", 3),
        sandbox_backend=_env("SANDBOX_BACKEND", "auto").lower(),
        sandbox_timeout_seconds=_env_int("SANDBOX_TIMEOUT_SECONDS", 20),
        sandbox_memory_mb=_env_int("SANDBOX_MEMORY_MB", 512),
        sandbox_cpu_seconds=_env_int("SANDBOX_CPU_SECONDS", 10),
        rag_enabled=_env_bool("RAG_ENABLED", True),
        chroma_persist_dir=_env("CHROMA_PERSIST_DIR", "chroma_db"),
        chroma_collection=_env("CHROMA_COLLECTION", "debug_squad_kb"),
        rag_top_k=_env_int("RAG_TOP_K", 3),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings for normal application use."""
    return load_settings()


def reload_settings() -> Settings:
    """Re-read the environment."""
    get_settings.cache_clear()
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    return get_settings()
