"""Code workspace - paste code, say what you want, get a review."""

from __future__ import annotations

import html

import streamlit as st

from examples.catalogue import EXAMPLES, EXAMPLES_BY_KEY
from models.schemas import Intent
from routing.intent_router import INTENT_LABELS, agents_for
from services.history_manager import HistoryManager
from services.review_orchestrator import ReviewOrchestrator
from ui import components as ui
from ui.state import PAGE_AGENTS, PAGE_RESULTS, PAGE_SETTINGS, goto, health

NO_EXAMPLE = "Pick an example…"

TASKS: dict[str, str] = {
    "Review & fix everything": "Review and fix everything",
    "Find the bug": "Find the bug in this code",
    "Make it readable": "Improve readability",
    "Explain it": "Explain this code",
    "Write tests": "Generate edge-case tests",
}

STAGE_WORDS = {
    "routing": "Reading your request",
    "bug_finder": "Bug-Finder",
    "style_lint": "Style/Lint",
    "fixer": "Fixer",
    "test_writer": "Test-Writer",
    "test_lab": "Running tests",
    "explainer": "Explainer",
}


# Callbacks run before widgets are drawn - the only time a widget's value can be set.
def _use_task() -> None:
    picked = st.session_state.get("task_pick")
    if picked in TASKS:
        st.session_state["user_request"] = TASKS[picked]


def _load_example() -> None:
    key = st.session_state.get("example_choice")
    example = EXAMPLES_BY_KEY.get(key)
    if example is None:
        return
    st.session_state["loaded_example"] = key
    st.session_state["code"] = example.code
    st.session_state["filename"] = example.filename
    st.session_state["user_request"] = example.suggested_request
    st.session_state["task_pick"] = next(
        (label for label, text in TASKS.items() if text == example.suggested_request),
        None,
    )
    st.session_state["session"] = None


def _clear_editor() -> None:
    st.session_state["code"] = ""
    st.session_state["filename"] = "my_code.py"
    st.session_state["session"] = None
    st.session_state["loaded_example"] = None
    st.session_state["example_choice"] = NO_EXAMPLE
    st.session_state["progress_log"] = []


def render(settings) -> None:
    ui.page_header(
        "What are we reviewing today?",
        "Paste your Python or pick an example, tell the squad what you're after, "
        "and they'll take it from there.",
    )
    _code_panel()
    _task_panel(settings)
    _result_strip()


def _editor_height(code: str) -> int:
    """Grow with the code instead of leaving a big empty box."""
    lines = max(1, len(code.splitlines()))
    return int(min(560, max(220, lines * 27 + 64)))


def _code_panel() -> None:
    with st.container(key="code_panel"):
        code = st.session_state.get("code", "")
        lines = len(code.splitlines())
        st.markdown(
            f'<div class="ds-panel-title">Your code '
            f'<span class="ds-file">{html.escape(st.session_state.get("filename", "my_code.py"))}'
            f" · {lines} line{'s' if lines != 1 else ''}</span></div>",
            unsafe_allow_html=True,
        )

        loaded = EXAMPLES_BY_KEY.get(st.session_state.get("loaded_example") or "")
        pick, actions = st.columns([2.1, 1.9], vertical_alignment="center", gap="medium")
        pick.selectbox(
            "Example",
            options=[NO_EXAMPLE, *[e.key for e in EXAMPLES]],
            format_func=lambda key: key if key == NO_EXAMPLE else EXAMPLES_BY_KEY[key].title,
            key="example_choice",
            on_change=_load_example,
            label_visibility="collapsed",
        )
        with actions:
            slots = st.columns(3 if loaded else 2, gap="small", vertical_alignment="center")
            if loaded:
                with slots[0].popover("Hint", width="stretch"):
                    st.markdown("**What the squad should find**")
                    st.markdown(loaded.expected)
            with slots[-2]:
                uploaded = st.file_uploader(
                    "Upload a .py file", type=["py"], label_visibility="collapsed", key="upload",
                )
            slots[-1].button("Clear", on_click=_clear_editor, width="stretch")

        if uploaded is not None:
            try:
                content = uploaded.getvalue().decode("utf-8")
            except UnicodeDecodeError:
                st.error("That file isn't plain text, so it can't be reviewed.")
            else:
                if content != st.session_state.get("uploaded_cache"):
                    st.session_state["uploaded_cache"] = content
                    st.session_state["code"] = content
                    st.session_state["filename"] = uploaded.name
                    st.session_state["session"] = None
                    st.session_state["loaded_example"] = None

        edit_tab, read_tab = st.tabs(["Edit", "Read with line numbers"])
        with edit_tab:
            st.text_area(
                "Your code",
                key="code",
                height=_editor_height(st.session_state.get("code", "")),
                label_visibility="collapsed",
                placeholder="def my_function(...):\n    ...",
            )
        with read_tab:
            current = st.session_state.get("code", "")
            if current.strip():
                st.code(current, language="python", line_numbers=True)
            else:
                st.caption("Nothing here yet - paste some code in the Edit tab.")


def _task_panel(settings) -> None:
    with st.container(key="task_panel"):
        st.markdown('<div class="ds-panel-title">What should the squad do?</div>',
                    unsafe_allow_html=True)
        st.pills(
            "Quick picks",
            options=list(TASKS),
            selection_mode="single",
            key="task_pick",
            on_change=_use_task,
            label_visibility="collapsed",
            wrap=True,
        )

        code = st.session_state.get("code", "")
        provider = health(settings)

        ask, go = st.columns([3.2, 1], vertical_alignment="bottom")
        ask.text_input("Or say it in your own words", key="user_request")
        clicked = go.button(
            "Review my code",
            type="primary",
            width="stretch",
            disabled=not code.strip() or not provider.ok,
        )

        switches = st.container(horizontal=True, gap="large", key="task_switches")
        auto = switches.toggle(
            "Squad picks the agents",
            key="auto_intent",
            help="On: the AI reads your request and picks the right agents. "
            "Off: you choose the kind of review yourself.",
        )
        switches.toggle(
            "Use the reference notes",
            key="rag_enabled",
            help="When it helps, agents look up style rules, common errors or "
            "testing advice before answering. Turn off to skip that entirely.",
        )

        manual_intent: Intent | None = None
        if not auto:
            pick, crew = st.columns([1, 2.2], vertical_alignment="center")
            chosen = pick.selectbox(
                "Kind of review",
                options=[i.value for i in Intent],
                format_func=lambda value: INTENT_LABELS[Intent(value)],
                key="manual_intent",
            )
            manual_intent = Intent(chosen)
            crew.markdown(
                '<p class="ds-crew-label">Who will work on this</p>'
                f'<div class="ds-tags">{ui.agent_tags(agents_for(manual_intent))}</div>',
                unsafe_allow_html=True,
            )

        if not provider.ok:
            msg, action = st.columns([3.2, 1], vertical_alignment="center")
            msg.markdown(
                '<div class="ds-inline-warn"><b>The AI model isn\'t connected yet.</b> '
                + (
                    "Add your free Groq key to <code>debug-squad/.env</code>, "
                    "then reload it in Settings."
                    if not provider.configured
                    else html.escape(provider.detail)
                )
                + "</div>",
                unsafe_allow_html=True,
            )
            if action.button("Open Settings", width="stretch"):
                goto(PAGE_SETTINGS)
        elif not code.strip():
            st.caption("Paste some code or pick an example to get started.")

        if clicked:
            _run_review(settings, code, auto, manual_intent)


def _result_strip() -> None:
    session = st.session_state.get("session")
    if session is None:
        return

    st.markdown("---")
    ui.verdict(session)
    ui.trail(session)

    go_results, go_agents, _ = st.columns([1.2, 1.2, 2])
    if go_results.button("See the full results", type="primary", width="stretch"):
        goto(PAGE_RESULTS)
    if go_agents.button("See what each agent did", width="stretch"):
        goto(PAGE_AGENTS)

    log = st.session_state.get("progress_log") or []
    if log:
        with st.expander("What happened, step by step"):
            for stage, message in log:
                st.markdown(f"**{STAGE_WORDS.get(stage, stage)}** — {message}")


def _run_review(settings, code: str, auto: bool, manual_intent: Intent | None) -> None:
    collected: list[tuple[str, str]] = []

    with st.status("The squad is on it…", expanded=True) as status:

        def progress(stage: str, message: str) -> None:
            collected.append((stage, message))
            status.write(f"**{STAGE_WORDS.get(stage, stage)}** — {message}")

        session = ReviewOrchestrator(settings, progress=progress).run(
            code,
            st.session_state.get("user_request", ""),
            filename=st.session_state.get("filename", "my_code.py"),
            auto_intent=auto,
            manual_intent=manual_intent,
            rag_enabled=st.session_state.get("rag_enabled", True),
        )
        status.update(
            label="All done" if not session.errors else "Done, with some problems",
            state="complete" if not session.errors else "error",
            expanded=False,
        )

    st.session_state["session"] = session
    st.session_state["progress_log"] = collected

    try:
        HistoryManager(settings).save(session)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"The review worked, but it couldn't be saved to history: {exc}")

    st.rerun()
