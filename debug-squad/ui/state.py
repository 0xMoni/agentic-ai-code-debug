"""Session state and cached health probes."""

from __future__ import annotations

import streamlit as st

from config.llm_provider import ProviderHealth, check_provider
from config.settings import Settings, get_settings, reload_settings
from examples.catalogue import EXAMPLES_BY_KEY
from models.schemas import Intent
from rag.retriever import RetrievalStatus, retrieval_status
from sandbox.sandbox_config import BackendInfo, detect_backend

DEFAULT_EXAMPLE = "find_max"

PAGE_WORKSPACE = "Code workspace"
PAGE_AGENTS = "AI agents"
PAGE_RESULTS = "Results & history"
PAGE_TESTS = "Test lab"
PAGE_PROMPTS = "Prompt inspector"
PAGE_COMPLIANCE = "Compliance"
PAGE_SETTINGS = "Settings"

PAGES = [
    PAGE_WORKSPACE,
    PAGE_AGENTS,
    PAGE_RESULTS,
    PAGE_TESTS,
    PAGE_PROMPTS,
    PAGE_COMPLIANCE,
    PAGE_SETTINGS,
]


PERSISTENT_WIDGET_KEYS = (
    "code",
    "user_request",
    "auto_intent",
    "manual_intent",
    "rag_enabled",
    "example_choice",
    "task_pick",
)


def init_state() -> None:
    """Seed session state on first load, and keep widget values alive."""
    example = EXAMPLES_BY_KEY[DEFAULT_EXAMPLE]
    defaults = {
        "page": PAGE_WORKSPACE,
        "code": example.code,
        "filename": example.filename,
        "user_request": example.suggested_request,
        "example_choice": DEFAULT_EXAMPLE,
        "loaded_example": DEFAULT_EXAMPLE,
        "task_pick": "Review & fix everything",
        "auto_intent": True,
        "manual_intent": Intent.FULL_REVIEW.value,
        "rag_enabled": True,
        "session": None,
        "progress_log": [],
        "compliance_results": {},
        "last_error": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    # Streamlit drops a widget's state when it isn't drawn; re-assigning keeps it.
    for key in PERSISTENT_WIDGET_KEYS:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def goto(page: str) -> None:
    """Navigate programmatically, from a button anywhere on a page."""
    # The nav radio owns 'page' and can't be written once drawn; apply it next run.
    st.session_state["pending_page"] = page
    st.rerun()


def apply_pending_navigation() -> None:
    """Promote a parked navigation target."""
    pending = st.session_state.pop("pending_page", None)
    if pending in PAGES:
        st.session_state["page"] = pending


@st.cache_data(ttl=60, show_spinner=False)
def cached_provider_health(
    provider: str, model: str, has_key: bool, _nonce: int = 0
) -> ProviderHealth:
    """Reachability + model availability."""
    return check_provider(get_settings(), run_inference=False)


@st.cache_data(ttl=120, show_spinner=False)
def cached_retrieval_status(persist_dir: str, collection: str) -> RetrievalStatus:
    return retrieval_status(get_settings())


@st.cache_resource(show_spinner=False)
def cached_backend(requested: str) -> BackendInfo:
    return detect_backend(get_settings())


@st.cache_resource(show_spinner=False)
def ensure_reference_notes(persist_dir: str, collection: str) -> str | None:
    """Build the reference notes on first start if they're missing."""
    from rag.ingest import collection_count, ingest

    settings = get_settings()
    if collection_count(settings) > 0:
        return None
    result = ingest(settings, rebuild=False)
    return None if result.ok else result.error


def refresh_probes() -> None:
    """Drop every cached probe, e.g. after a settings change."""
    cached_provider_health.clear()
    cached_retrieval_status.clear()
    cached_backend.clear()


def reload_configuration() -> Settings:
    settings = reload_settings()
    refresh_probes()
    return settings


def health(settings: Settings) -> ProviderHealth:
    return cached_provider_health(
        settings.provider, settings.model_name, bool(settings.groq_api_key)
    )


def kb_status(settings: Settings) -> RetrievalStatus:
    return cached_retrieval_status(
        str(settings.persist_path), settings.chroma_collection
    )


def backend(settings: Settings) -> BackendInfo:
    return cached_backend(settings.sandbox_backend)


def current_session():
    return st.session_state.get("session")


def require_session(message: str = "No review has been run yet.") -> bool:
    """Returns True when a session exists; otherwise renders an empty state."""
    from ui.components import empty_state

    if current_session() is None:
        empty_state(
            message,
            "Paste some code (or pick an example) in the code workspace, "
            "then press Review my code.",
        )
        if st.button("Go to the code workspace", type="primary"):
            goto(PAGE_WORKSPACE)
        return False
    return True
