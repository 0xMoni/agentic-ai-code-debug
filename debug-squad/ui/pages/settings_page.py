"""Settings - see how things are set up."""

from __future__ import annotations

import html

import streamlit as st

from config.llm_provider import check_provider, list_models
from config.settings import DEFAULT_GROQ_MODEL
from rag.ingest import EMBEDDING_MODEL_NAME
from sandbox.sandbox_config import describe_backends
from ui import components as ui
from ui.state import backend, health, kb_status, reload_configuration


def render(settings) -> None:
    ui.page_header(
        "Settings",
        "Everything is set in the debug-squad/.env file. Change it there, "
        "then reload - keys are never typed into the app.",
    )

    if st.button("Reload settings from .env", type="primary"):
        reload_configuration()
        st.success("Settings reloaded.")
        st.rerun()

    ai_tab, tests_tab, notes_tab = st.tabs(["AI model", "Test runner", "Reference notes"])

    with ai_tab:
        provider = health(settings)
        if provider.ok:
            ui.note(f"<b>Connected.</b> {html.escape(provider.detail)}", "ok")
        elif not provider.configured:
            ui.note(
                "<b>No API key yet.</b> Get a free one at "
                '<a href="https://console.groq.com/keys">console.groq.com/keys</a>, '
                "paste it into <code>debug-squad/.env</code> as "
                "<code>GROQ_API_KEY=gsk_…</code>, then press Reload above.",
                "warn",
            )
        else:
            ui.note(f"<b>Can't reach the model.</b> {html.escape(provider.detail)}", "bad")

        ui.kv_block(
            [
                ("Provider", settings.provider),
                ("Model", settings.model_name),
                ("API key", settings.masked_groq_key()),
                ("Max fix attempts", str(settings.max_fix_attempts)),
                ("Creativity (temperature)", str(settings.temperature)),
            ],
            mono=True,
        )

        check, models, _ = st.columns([1.2, 1.2, 1.6])
        if check.button("Test the connection", width="stretch",
                        disabled=not settings.has_credentials):
            with st.spinner("Asking the model to say 'ready'…"):
                result = check_provider(settings, run_inference=True)
            (st.success if result.ok and result.inference_verified else st.error)(result.detail)

        if models.button("Show available models", width="stretch",
                         disabled=not settings.has_credentials):
            try:
                with st.spinner("Asking Groq…"):
                    available = list_models(settings)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Couldn't fetch the list: {exc}")
            else:
                st.code("\n".join(available) or "(none)", language="text")
                if settings.model_name not in available:
                    ui.note(
                        f"Your model <code>{html.escape(settings.model_name)}</code> isn't "
                        "in that list. Set <code>GROQ_MODEL</code> in .env to one that is.",
                        "bad",
                    )

        with st.expander("Using a model on your own computer instead (Ollama)"):
            st.markdown(
                f"""
1. Install Ollama from ollama.com, then run `ollama pull qwen2.5-coder:7b`.
2. Start it with `ollama serve`.
3. In `.env`, set `LLM_PROVIDER=ollama` and `OLLAMA_MODEL=qwen2.5-coder:7b`.

Small models get things wrong more often. Groq's `{DEFAULT_GROQ_MODEL}` gives the best results.
"""
            )

    with tests_tab:
        active = backend(settings)
        if active.is_adequate:
            ui.note(
                f"<b>Tests run safely.</b> Using {active.name}: no internet access, "
                "and nothing outside its own scratch folder can be changed.",
                "ok",
            )
        else:
            ui.note(f"<b>{html.escape(active.badge)}.</b> {html.escape(active.description)}", "warn")

        ui.kv_block(
            [
                ("Time limit", f"{settings.sandbox_timeout_seconds}s per run"),
                ("CPU limit", f"{settings.sandbox_cpu_seconds}s"),
                ("Memory limit", f"{settings.sandbox_memory_mb} MB"),
            ]
        )

        st.markdown("#### Options on this computer")
        for info in describe_backends():
            mark = "in use" if info.name == active.name else (
                "available" if info.available else "not available"
            )
            st.markdown(f"**{info.name}** ({mark}) — {info.description}")

        with st.expander("Setting this up on Windows or Linux"):
            st.markdown(
                """
- **macOS:** nothing to do. The built-in `sandbox-exec` is used.
- **Linux:** install Docker (`sudo apt install docker.io`).
- **Windows:** install Docker Desktop with WSL 2.

Without an isolated option, tests are written but not run, and the results
say so - they're never reported as passing.
"""
            )

    with notes_tab:
        notes = kb_status(settings)
        ui.kv_block(
            [
                ("Looking things up", "on" if st.session_state.get("rag_enabled", True) else "off"),
                ("Passages stored", str(notes.chunk_count)),
                ("Results per lookup", str(settings.rag_top_k)),
                ("Embedding model", EMBEDDING_MODEL_NAME),
            ]
        )
        st.caption(
            "The embedding model runs on this computer - no key needed. "
            "Turn looking things up on or off in the code workspace."
        )
