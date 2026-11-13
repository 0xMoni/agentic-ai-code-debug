"""Prompt Inspector."""

from __future__ import annotations

import html
import json

import streamlit as st

from chains.lcel_chains import compact_format_instructions
from models.schemas import BugReport, PromptTechnique
from prompts.examples import BUG_FINDER_FEW_SHOT, example_summaries
from ui import components as ui
from ui.state import require_session

TECHNIQUE_NOTES: dict[PromptTechnique, tuple[str, str]] = {
    PromptTechnique.ZERO_SHOT: (
        "Zero-shot",
        "A direct instruction with no examples. Used for intent classification, "
        "the style review, the first-pass fix and test generation - tasks where "
        "the instruction plus a schema is enough.",
    ),
    PromptTechnique.FEW_SHOT: (
        "Few-shot",
        "Three worked examples are included before the real input. The third "
        "example is *correct* code whose answer is an empty issue list - without "
        "it, models invent a defect because the prompt implies one exists.",
    ),
    PromptTechnique.REFLECTION: (
        "Reflection",
        "The Fixer is shown its own previous answer, the tests, and the real "
        "pytest output, and asked to revise. Only used when tests genuinely ran "
        "and genuinely failed.",
    ),
    PromptTechnique.REACT_TOOL_USE: (
        "ReAct-style tool use",
        "The agent invokes a real tool and reads its output back into the "
        "prompt - ast.parse, ruff, the knowledge base, the sandbox.",
    ),
}


def render(settings) -> None:
    ui.page_header(
        "Prompt inspector",
        "Every prompt this system sends, the technique behind it, and the LCEL "
        "pipeline it runs through.",
    )

    technique_tab, live_tab, pipeline_tab = st.tabs(
        ["Techniques", "Live prompt records", "LCEL and parsers"]
    )

    with technique_tab:
        st.markdown("### The four prompt-engineering techniques")
        for technique, (title, description) in TECHNIQUE_NOTES.items():
            with st.expander(f"{title}", expanded=False):
                st.markdown(description)
                if technique is PromptTechnique.FEW_SHOT:
                    st.markdown("**The three few-shot examples**")
                    for summary in example_summaries():
                        st.markdown(f"- {summary}")
                    with st.expander("See the full few-shot block as sent", expanded=False):
                        for index, (code, output) in enumerate(
                            BUG_FINDER_FEW_SHOT, start=1
                        ):
                            st.markdown(f"**Example {index} — input**")
                            st.code(code, language="python")
                            st.markdown(f"**Example {index} — expected output**")
                            st.code(output, language="json")
                if technique is PromptTechnique.REACT_TOOL_USE:
                    st.markdown(
                        "- `ast.parse` — Bug-Finder, before any model call\n"
                        "- `ruff` — Style/Lint, output injected into the prompt\n"
                        "- `knowledge_base` — conditional Chroma retrieval\n"
                        "- `sandbox` — Test-Writer executes and reads back results"
                    )

        st.markdown("---")
        st.markdown("### System prompts, as sent")
        from prompts import system_prompts

        for name in sorted(n for n in dir(system_prompts) if n.endswith("_SYSTEM")):
            with st.expander(name, expanded=False):
                st.code(getattr(system_prompts, name), language="text")

        st.markdown("### User prompt templates")
        from prompts import user_prompts

        for name in sorted(n for n in dir(user_prompts) if n.endswith("_USER")):
            with st.expander(name, expanded=False):
                st.code(getattr(user_prompts, name), language="text")
                st.caption(
                    "The {placeholders} are filled by the LCEL chain at invoke time."
                )

    with live_tab:
        if not require_session("No prompts have been recorded yet."):
            return

        session = st.session_state["session"]
        if not session.prompts:
            ui.empty_state(
                "This review made no LLM calls.",
                "That happens when, for example, the file failed its syntax check.",
            )
            return

        labels = [
            f"{index + 1}. {ui.AGENT_TITLES.get(record.agent, record.agent.value)}"
            f" — {record.technique.value.replace('_', ' ')}"
            for index, record in enumerate(session.prompts)
        ]
        choice = st.selectbox("LLM call", options=range(len(labels)),
                              format_func=lambda i: labels[i])
        record = session.prompts[choice]

        ui.chip_row(
            [
                ui.chip(ui.AGENT_TITLES.get(record.agent, record.agent.value), "accent"),
                ui.chip(record.technique.value.replace("_", " "), "accent"),
                ui.chip(f"{record.duration_seconds:.2f}s", "neutral"),
                ui.chip(f"{record.provider}: {record.model}", "neutral"),
                ui.chip(
                    "parse repaired" if record.parse_repaired else "parsed cleanly",
                    "warn" if record.parse_repaired else "ok",
                ),
            ]
        )

        if record.error:
            tone = "warn" if record.error.startswith("(") else "bad"
            ui.note(html.escape(record.error), tone)

        st.markdown("### LCEL pipeline")
        st.code(record.lcel_pipeline, language="text")

        st.markdown("### System prompt")
        st.code(record.system_prompt, language="text")

        st.markdown("### User prompt template")
        st.code(record.user_prompt_template, language="text")

        st.markdown("### Rendered user prompt (what was actually sent)")
        st.code(record.rendered_user_prompt, language="text")

        if record.few_shot_examples:
            st.markdown("### Few-shot examples included")
            for item in record.few_shot_examples:
                st.markdown(f"- `{item}`")

        st.markdown("### Retrieved context")
        if record.retrieved_context:
            st.caption(
                f"{len(record.retrieved_context)} passage(s) were injected into "
                "this prompt."
            )
            for index, passage in enumerate(record.retrieved_context, start=1):
                with st.expander(f"Passage {index}", expanded=False):
                    st.code(passage, language="text")
        else:
            st.caption(
                "None — retrieval was either skipped for this agent or returned "
                "nothing. The agent's log on the AI agents page shows the decision "
                "and its reason."
            )

        st.markdown("### Output parser")
        st.code(record.output_parser, language="text")

        raw_column, parsed_column = st.columns(2)
        with raw_column:
            st.markdown("### Raw model output")
            st.code(record.raw_output or "(nothing recorded)", language="json")
        with parsed_column:
            st.markdown("### Parsed and validated")
            if record.parsed_output is not None:
                st.code(
                    json.dumps(record.parsed_output, indent=2)[:6000], language="json"
                )
            else:
                st.caption("Not a structured output (or parsing failed).")

    with pipeline_tab:
        st.markdown("### How a chain is built")
        st.markdown(
            "Every LLM call in this project runs through a LangChain Expression "
            "Language pipeline composed with the `|` operator. This is the actual "
            "code from `chains/lcel_chains.py`:"
        )
        st.code(
            '''# Prompt templating - system + human message templates
self.prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(system_prompt),
    HumanMessagePromptTemplate.from_template(user_template),
])

# Structured output parsing, with salvage for near-miss JSON
self.parser = RepairingPydanticParser(pydantic_object=output_model)

# The LCEL pipeline itself
self.chain = self.prompt | self.model | self.parser

# ...and invoking it
result = self.chain.invoke({"code": code, "format_instructions": ...})''',
            language="python",
        )

        st.markdown("### LangChain components in use")
        ui.kv_block(
            [
                ("Prompt templating", "ChatPromptTemplate.from_messages"),
                (
                    "Message templates",
                    "SystemMessagePromptTemplate, HumanMessagePromptTemplate",
                ),
                ("Model integration", "ChatGroq (langchain-groq), ChatOllama (BaseChatModel subclass)"),
                ("Output parsing", "PydanticOutputParser via RepairingPydanticParser"),
                ("Text output parsing", "StrOutputParser (explanation handler)"),
                ("Composition", "Runnable pipelines built with the | operator"),
            ],
            mono=True,
        )

        st.markdown("### Why a custom parser subclass")
        st.markdown(
            "`RepairingPydanticParser` extends LangChain's `PydanticOutputParser` "
            "and overrides **`parse_result`** — not `parse`. When a parser sits in "
            "an LCEL chain, `parse_result` is the method the Runnable actually "
            "calls, so overriding it is what puts the repair logic genuinely "
            "inside the pipeline rather than in a wrapper around it. It salvages "
            "the two things small models really do — markdown fences and JSON "
            "wrapped in prose — before spending a second call on an LLM repair."
        )

        st.markdown("### Generated format instructions")
        st.markdown(
            "Rather than embedding a full JSON schema (thousands of characters, "
            "which matters on a token-limited free tier), the parser emits a "
            "trimmed shape listing field types and the exact enum choices:"
        )
        st.code(compact_format_instructions(BugReport), language="text")
