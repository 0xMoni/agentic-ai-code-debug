"""Assignment Compliance - 7/7 Criteria."""

from __future__ import annotations

import html

import streamlit as st

from models.schemas import VerificationStatus
from services.compliance import ALL_CRITERIA, CRITERIA, SANDBOX_CRITERION
from ui import components as ui
from ui.state import goto

_TONE = {
    VerificationStatus.VERIFIED: "ok",
    VerificationStatus.FAILED: "bad",
    VerificationStatus.NOT_VERIFIED: "warn",
    VerificationStatus.NOT_CONFIGURED: "neutral",
}


def _results() -> dict[int, object]:
    return st.session_state.setdefault("compliance_results", {})


def render(settings) -> None:
    ui.page_header(
        "Assignment Compliance — 7/7 Criteria",
        "Every card below runs a real check against the running system. A "
        "criterion only reads Verified when that check succeeded just now - "
        "not because the feature has code behind it.",
    )

    results = _results()

    verified = sum(
        1
        for criterion in CRITERIA
        if results.get(criterion.number)
        and results[criterion.number].status is VerificationStatus.VERIFIED
    )
    failed = sum(
        1
        for criterion in CRITERIA
        if results.get(criterion.number)
        and results[criterion.number].status is VerificationStatus.FAILED
    )
    unconfigured = sum(
        1
        for criterion in CRITERIA
        if results.get(criterion.number)
        and results[criterion.number].status is VerificationStatus.NOT_CONFIGURED
    )
    unchecked = len(CRITERIA) - len(
        [c for c in CRITERIA if c.number in results]
    )

    ui.facts(
        [
            (f"{verified}/7", "verified by a live check"),
            (failed, "failed"),
            (unconfigured, "not configured"),
            (unchecked, "not checked yet"),
        ]
    )

    buttons = st.columns([1.3, 1.3, 1.3, 2])
    if buttons[0].button("Run all free checks", type="primary", width="stretch"):
        with st.spinner("Running checks..."):
            for criterion in ALL_CRITERIA:
                if not criterion.costs_tokens:
                    results[criterion.number] = criterion.check(settings)
        st.rerun()

    if buttons[1].button("Run every check", width="stretch"):
        with st.spinner("Running every check, including the LLM call..."):
            for criterion in ALL_CRITERIA:
                results[criterion.number] = criterion.check(settings)
        st.rerun()

    if buttons[2].button("Clear results", width="stretch"):
        st.session_state["compliance_results"] = {}
        st.rerun()

    buttons[3].markdown(
        '<p style="font-size:14px;color:#94A3B8;padding-top:7px;">'
        "“Free” checks make no API calls. Criterion 1 sends one real completion "
        "request, which is the only way to honestly claim inference works.</p>",
        unsafe_allow_html=True,
    )

    if unchecked == len(CRITERIA):
        ui.note(
            "<b>Nothing has been verified yet.</b> Press <b>Run all free checks</b> "
            "to verify six of the seven criteria without spending any tokens, then "
            "<b>Run every check</b> to include the live LLM call.",
            "warn",
        )

    st.markdown("---")

    for criterion in CRITERIA:
        _render_criterion(settings, criterion, results)

    st.markdown("---")
    st.markdown("### Beyond the seven criteria")
    _render_criterion(settings, SANDBOX_CRITERION, results)

    st.markdown("---")
    st.markdown("### How to verify this project by hand")
    st.markdown(
        """
Run these from the `debug-squad/` directory:

```bash
# the automated test suite - needs no API key, uses a stub model
pytest -v

# rebuild the vector index and print the chunk counts
python -m rag.ingest --rebuild

# lint the project with the same tool the Style agent uses
python -m ruff check .
```

Then, in the app: load **example 1** in the code workspace and press
**Review my code**. Check the AI agents screen for which agents ran and which
weren't needed, the test lab for the real pytest output, and the prompt
inspector for every prompt that was sent. Load **example 4** and tap
**Explain it**: the Explainer's log on the AI agents screen shows retrieval
being deliberately skipped, with the reason.
        """
    )


def _render_criterion(settings, criterion, results: dict) -> None:
    result = results.get(criterion.number)
    status = result.status if result else VerificationStatus.NOT_VERIFIED
    tone = _TONE[status]

    header = f"{criterion.number}. {criterion.title} — {status.value}"
    with st.expander(header, expanded=status is VerificationStatus.FAILED):
        ui.chip_row(
            [
                ui.chip(status.value, tone),
                *(
                    [ui.chip(f"{result.duration_seconds:.2f}s", "neutral")]
                    if result and result.duration_seconds
                    else []
                ),
                *(
                    [ui.chip("costs tokens", "warn")]
                    if criterion.costs_tokens
                    else [ui.chip("no API call", "accent")]
                ),
            ]
        )

        st.markdown("**What was implemented**")
        st.markdown(criterion.what)

        st.markdown("**How it works**")
        st.markdown(criterion.how)

        st.markdown("**Source files**")
        st.markdown(
            " ".join(f"`{path}`" for path in criterion.files),
        )

        if criterion.demo_notes:
            st.markdown("**Demonstration notes**")
            for note_text in criterion.demo_notes:
                st.markdown(f"- {note_text}")

        st.markdown("**Verification**")
        if result is None:
            ui.note(
                "This criterion has not been checked in this session. Its status "
                "is <b>Not Yet Verified</b> - which is the honest default, not a "
                "failure.",
                "warn",
            )
        else:
            ui.note(
                f"<b>{status.value}</b><br>{html.escape(result.detail)}", tone
            )
            if result.evidence:
                st.markdown("**Evidence**")
                st.code(result.evidence, language="text")

        actions = st.columns([1.2, 1.2, 2.6])
        if actions[0].button(
            "Run this check",
            key=f"check_{criterion.number}",
            width="stretch",
        ):
            with st.spinner(f"Checking: {criterion.title}..."):
                results[criterion.number] = criterion.check(settings)
            st.rerun()

        if criterion.navigate_to and actions[1].button(
            f"Open {criterion.navigate_to}",
            key=f"nav_{criterion.number}",
            width="stretch",
        ):
            goto(criterion.navigate_to)
