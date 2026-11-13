"""Debug Squad - four agents, one review."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

from config.settings import get_settings  # noqa: E402
from ui import components as ui  # noqa: E402
from ui.state import (  # noqa: E402
    PAGE_AGENTS,
    PAGE_COMPLIANCE,
    PAGE_PROMPTS,
    PAGE_RESULTS,
    PAGE_SETTINGS,
    PAGE_TESTS,
    PAGE_WORKSPACE,
    PAGES,
    apply_pending_navigation,
    backend,
    ensure_reference_notes,
    health,
    init_state,
    kb_status,
    refresh_probes,
)

st.set_page_config(
    page_title="Debug Squad",
    page_icon=":material/bug_report:",
    layout="wide",
    initial_sidebar_state="expanded",
)


def system_status(settings) -> list[tuple[str, str, str]]:
    """What the sidebar says about the system, in plain words."""
    provider = health(settings)
    sandbox = backend(settings)
    knowledge = kb_status(settings)

    if provider.ok:
        llm = ("AI model connected", f"{settings.model_name} on {settings.provider}", "ok")
    elif not provider.configured:
        llm = ("AI model not set up", "Add your Groq key in Settings", "bad")
    else:
        llm = ("AI model unreachable", provider.detail[:80], "bad")

    if sandbox.is_adequate:
        box = ("Safe test runner ready", f"Isolated with {sandbox.name}", "ok")
    elif sandbox.available:
        box = ("Test runner not isolated", f"Using {sandbox.name}", "warn")
    else:
        box = ("Tests can't run here", "No isolated sandbox found", "bad")

    if knowledge.available:
        notes = ("Reference notes loaded", f"{knowledge.chunk_count} passages in ChromaDB", "ok")
    else:
        notes = ("Reference notes missing", "Run: python -m rag.ingest", "bad")

    rag = (
        ("Looking things up: on", "Agents read the notes when it helps", "ok")
        if st.session_state.get("rag_enabled", True)
        else ("Looking things up: off", "Turned off in the workspace", "off")
    )
    return [llm, box, notes, rag]


def sidebar(settings) -> str:
    with st.sidebar:
        ui.brand()
        page = st.radio("Go to", PAGES, key="page", label_visibility="collapsed")
        st.markdown("---")
        ui.status_list(system_status(settings))
    return page


def main() -> None:
    settings = get_settings()
    ui.inject_styles()
    init_state()
    apply_pending_navigation()

    with st.spinner("Setting up the reference notes - first run only…"):
        problem = ensure_reference_notes(str(settings.persist_path), settings.chroma_collection)
    if problem:
        refresh_probes()

    page = sidebar(settings)

    if page == PAGE_WORKSPACE:
        from ui.pages import workspace

        workspace.render(settings)
    elif page == PAGE_AGENTS:
        from ui.pages import agents_view

        agents_view.render(settings)
    elif page == PAGE_RESULTS:
        from ui.pages import results

        results.render(settings)
    elif page == PAGE_TESTS:
        from ui.pages import test_lab

        test_lab.render(settings)
    elif page == PAGE_PROMPTS:
        from ui.pages import prompt_inspector

        prompt_inspector.render(settings)
    elif page == PAGE_COMPLIANCE:
        from ui.pages import compliance

        compliance.render(settings)
    elif page == PAGE_SETTINGS:
        from ui.pages import settings_page

        settings_page.render(settings)


if __name__ == "__main__":
    main()
