"""Smoke tests for every screen, using Streamlit's own AppTest harness."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP = str(PROJECT_ROOT / "app.py")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.state import (  # noqa: E402
    PAGE_AGENTS,
    PAGE_COMPLIANCE,
    PAGE_PROMPTS,
    PAGE_RESULTS,
    PAGE_SETTINGS,
    PAGE_TESTS,
    PAGE_WORKSPACE,
    PAGES,
)

TIMEOUT = 90


def run_page(page: str, **state) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=TIMEOUT)
    app.session_state["page"] = page
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    return app


def no_errors(app: AppTest) -> None:
    assert not app.exception, "; ".join(str(e.value) for e in app.exception)


def rendered_text(app: AppTest) -> str:
    parts: list[str] = []
    for collection in (app.markdown, app.code, app.caption, app.text, app.error,
                       app.warning, app.success, app.info):
        for element in collection:
            parts.append(str(getattr(element, "value", "") or ""))
    for element in app.sidebar.markdown:
        parts.append(element.value or "")
    for element in app.expander:
        parts.append(element.label or "")
    return "\n".join(parts)


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    """Point history at a temp folder so tests never touch real reviews."""
    from services import history_manager

    original = history_manager.HistoryManager.__init__

    def patched(self, settings=None):
        original(self, settings)
        self.directory = tmp_path / "history"

    monkeypatch.setattr(history_manager.HistoryManager, "__init__", patched)
    return tmp_path / "history"


def test_the_seven_pages_in_order():
    assert PAGES == [
        PAGE_WORKSPACE,
        PAGE_AGENTS,
        PAGE_RESULTS,
        PAGE_TESTS,
        PAGE_PROMPTS,
        PAGE_COMPLIANCE,
        PAGE_SETTINGS,
    ]


def test_the_knowledge_base_page_is_gone():
    assert "Knowledge base" not in PAGES
    assert not (PROJECT_ROOT / "ui" / "pages" / "knowledge_base.py").exists()


def test_the_app_opens_on_the_workspace():
    app = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    no_errors(app)
    assert app.session_state["page"] == PAGE_WORKSPACE


def test_sidebar_lists_the_pages_and_shows_system_status():
    app = run_page(PAGE_WORKSPACE)
    assert list(app.sidebar.radio[0].options) == PAGES
    sidebar = "\n".join(m.value for m in app.sidebar.markdown)
    assert "How things look" in sidebar
    for phrase in ("AI model", "test runner", "Reference notes"):
        assert phrase in sidebar, f"status is missing '{phrase}'"


@pytest.mark.parametrize("page", PAGES)
def test_no_page_shows_the_old_top_bar(page):
    app = run_page(page)
    assert "ds-topbar" not in rendered_text(app)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_no_review(page, isolated_history):
    no_errors(run_page(page))


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_a_verified_review(page, verified_session, isolated_history):
    no_errors(run_page(page, session=verified_session))


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_an_unrun_review(page, unverified_session, isolated_history):
    no_errors(run_page(page, session=unverified_session))


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_after_an_explanation(page, explanation_session, isolated_history):
    no_errors(run_page(page, session=explanation_session))


def test_workspace_has_its_controls():
    app = run_page(PAGE_WORKSPACE)
    assert app.text_area(key="code")
    assert app.text_input(key="user_request")
    labels = {t.label for t in app.toggle}
    assert {"Squad picks the agents", "Use the reference notes"} <= labels
    assert any(b.label == "Review my code" for b in app.button)


def test_review_button_is_disabled_without_a_key():
    app = run_page(PAGE_WORKSPACE)
    review = next(b for b in app.button if b.label == "Review my code")
    assert review.disabled is True
    assert "isn't connected yet" in rendered_text(app)


def test_quick_pick_fills_in_the_request():
    app = run_page(PAGE_WORKSPACE)
    app.button_group(key="task_pick").set_value("Explain it").run()
    no_errors(app)
    assert app.session_state["user_request"] == "Explain this code"


def test_choosing_an_example_loads_it():
    app = run_page(PAGE_WORKSPACE)
    app.selectbox(key="example_choice").set_value("average").run()
    no_errors(app)
    assert "def average" in app.session_state["code"]
    assert app.session_state["filename"] == "average_runtime_risk.py"
    assert app.session_state["user_request"] == "Find the bug in this code"


def test_clear_empties_the_editor():
    app = run_page(PAGE_WORKSPACE)
    next(b for b in app.button if b.label == "Clear").click().run()
    no_errors(app)
    assert app.session_state["code"] == ""


def test_turning_off_auto_shows_the_manual_choice_and_crew():
    app = run_page(PAGE_WORKSPACE)
    app.toggle(key="auto_intent").set_value(False).run()
    no_errors(app)
    assert app.selectbox(key="manual_intent")
    assert "Who will work on this" in rendered_text(app)


def test_your_code_survives_visiting_other_pages():
    """Streamlit drops widget state for widgets not on screen - guard it."""
    app = run_page(PAGE_WORKSPACE)
    app.text_area(key="code").input("print('mine')").run()
    app.toggle(key="rag_enabled").set_value(False).run()
    for page in [PAGE_SETTINGS, PAGE_RESULTS, PAGE_TESTS, PAGE_PROMPTS,
                 PAGE_COMPLIANCE, PAGE_AGENTS, PAGE_WORKSPACE]:
        app.sidebar.radio[0].set_value(page).run()
        no_errors(app)
    assert app.session_state["code"] == "print('mine')"
    assert app.session_state["rag_enabled"] is False


def test_result_strip_buttons_navigate(verified_session):
    for label, expected in [("See the full results", PAGE_RESULTS),
                            ("See what each agent did", PAGE_AGENTS)]:
        app = run_page(PAGE_WORKSPACE, session=verified_session)
        next(b for b in app.button if b.label == label).click().run()
        no_errors(app)
        app.run()
        assert app.session_state["page"] == expected


def test_results_show_the_real_verdict(unverified_session, isolated_history):
    app = run_page(PAGE_RESULTS, session=unverified_session)
    text = rendered_text(app)
    assert "Reviewed, not tested" in text
    assert "written but not run" in text


def test_verified_results_have_working_downloads(verified_session, isolated_history):
    app = run_page(PAGE_RESULTS, session=verified_session)
    labels = {d.label for d in app.download_button}
    assert {"Download fixed code", "Download the report", "Download raw data"} <= labels


def test_history_lists_saved_reviews_with_their_code(
    verified_session, isolated_history, settings
):
    from services.history_manager import HistoryManager

    HistoryManager(settings).save(verified_session)
    app = run_page(PAGE_RESULTS)
    text = rendered_text(app)
    assert "History (1)" in [t.label for t in app.tabs]
    assert verified_session.filename in text
    assert "def find_max" in text, "the code that was run must be shown"


def test_opening_a_saved_review_makes_it_the_latest(
    verified_session, isolated_history, settings
):
    from services.history_manager import HistoryManager

    HistoryManager(settings).save(verified_session)
    app = run_page(PAGE_RESULTS)
    next(b for b in app.button if b.label == "Open this review").click().run()
    no_errors(app)
    assert app.session_state["session"].session_id == verified_session.session_id


def test_editing_a_saved_review_loads_its_code(
    verified_session, isolated_history, settings
):
    from services.history_manager import HistoryManager

    HistoryManager(settings).save(verified_session)
    app = run_page(PAGE_RESULTS)
    next(b for b in app.button if b.label == "Edit this code").click().run()
    no_errors(app)
    app.run()
    assert app.session_state["page"] == PAGE_WORKSPACE
    assert app.session_state["code"] == verified_session.original_code


def test_deleting_from_history(verified_session, isolated_history, settings):
    from services.history_manager import HistoryManager

    HistoryManager(settings).save(verified_session)
    app = run_page(PAGE_RESULTS)
    next(b for b in app.button if b.label == "Delete").click().run()
    no_errors(app)
    assert HistoryManager(settings).count() == 0


def test_empty_results_offer_a_way_back(isolated_history):
    app = run_page(PAGE_RESULTS)
    next(b for b in app.button if b.label == "Go to the code workspace").click().run()
    no_errors(app)
    app.run()
    assert app.session_state["page"] == PAGE_WORKSPACE


def test_agents_page_explains_skipped_agents(explanation_session):
    text = rendered_text(run_page(PAGE_AGENTS, session=explanation_session))
    assert "Not needed" in text
    assert "Treated as: Code Explanation" in text


def test_test_lab_says_plainly_when_tests_did_not_run(unverified_session):
    text = rendered_text(run_page(PAGE_TESTS, session=unverified_session))
    assert "NOT executed" in text or "not executed" in text.lower()
    assert "No claim is made" in text


def test_test_lab_shows_real_results(verified_session):
    app = run_page(PAGE_TESTS, session=verified_session)
    no_errors(app)
    text = rendered_text(app)
    assert "test_all_negative" in text
    assert "the definition of a maximum" in text, "the oracle must be visible"


def test_prompt_inspector_lists_all_four_techniques():
    labels = [e.label for e in run_page(PAGE_PROMPTS).expander]
    for title in ("Zero-shot", "Few-shot", "Reflection", "ReAct-style tool use"):
        assert any(title in label for label in labels), f"missing {title}"


def test_prompt_inspector_shows_recorded_calls(verified_session):
    app = run_page(PAGE_PROMPTS, session=verified_session)
    no_errors(app)
    text = rendered_text(app)
    assert "You are the Bug-Finder agent" in text
    assert "RepairingPydanticParser" in text


def test_compliance_defaults_to_not_verified():
    """Nothing may read Verified before a check has actually run."""
    app = run_page(PAGE_COMPLIANCE)
    labels = [e.label for e in app.expander]
    assert len(labels) >= 8
    assert all(" Verified" not in label or "Not Yet Verified" in label for label in labels)
    assert "Nothing has been verified yet" in rendered_text(app)


def test_compliance_free_checks_all_verify():
    app = run_page(PAGE_COMPLIANCE)
    next(b for b in app.button if b.label == "Run all free checks").click().run(timeout=TIMEOUT)
    no_errors(app)
    results = app.session_state["compliance_results"]
    for number in (2, 3, 4, 5, 6, 7, 8):
        assert results[number].status.value == "Verified", (
            f"criterion {number}: {results[number].status.value} - {results[number].detail}"
        )


def test_compliance_navigation_buttons_lead_to_real_pages():
    from services.compliance import ALL_CRITERIA

    for criterion in ALL_CRITERIA:
        assert criterion.navigate_to in ("", *PAGES), (
            f"criterion {criterion.number} points at missing page {criterion.navigate_to!r}"
        )
    app = run_page(PAGE_COMPLIANCE)
    next(b for b in app.button if b.label == "Open Prompt inspector").click().run()
    no_errors(app)
    app.run()
    assert app.session_state["page"] == PAGE_PROMPTS


def test_settings_masks_the_key_and_explains_setup():
    text = rendered_text(run_page(PAGE_SETTINGS))
    assert "No API key yet" in text
    assert "(not set)" in text


def test_no_page_leaks_a_configured_key(monkeypatch, verified_session, isolated_history):
    secret = "gsk_pretend_secret_value_123456"
    monkeypatch.setenv("GROQ_API_KEY", secret)
    from config.settings import get_settings
    from ui.state import refresh_probes

    get_settings.cache_clear()
    refresh_probes()
    for page in PAGES:
        app = run_page(page, session=verified_session)
        assert secret not in rendered_text(app), f"{page} leaked the API key"
