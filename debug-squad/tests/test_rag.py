"""Retrieval: chunking, the vector search, and the conditional policy (criteria 5 and 7)."""

from __future__ import annotations

import pytest

from config.settings import get_settings
from models.schemas import AgentName, Intent
from rag.ingest import CHUNK_MIN_CHARS, chunk_all
from rag.retriever import (
    RETRIEVAL_POLICY,
    list_indexed_documents,
    policy_decision,
    retrieval_status,
    similarity_search,
)


@pytest.fixture
def index_ready():
    status = retrieval_status(get_settings())
    if not status.available:
        pytest.skip("the vector index is not built here - run `python -m rag.ingest`")
    return status


def test_every_guide_is_chunked_on_headings():
    chunks = chunk_all(get_settings().knowledge_base_path)
    assert {c.source for c in chunks} >= {
        "python_style_conventions.md",
        "common_python_exceptions.md",
        "debugging_patterns.md",
        "testing_principles.md",
        "algorithmic_edge_cases.md",
        "code_readability.md",
    }
    assert all(len(c.text) >= CHUNK_MIN_CHARS for c in chunks)
    assert all(" - " in c.text.splitlines()[0] for c in chunks)
    assert len({c.chunk_id for c in chunks}) == len(chunks), "chunk ids must be unique"


def test_search_finds_passages_by_meaning(index_ready):
    """The question never says 'ZeroDivisionError', yet that section ranks first."""
    passages = similarity_search("what happens when you divide by zero", top_k=3)
    assert passages
    assert passages[0].source == "common_python_exceptions.md - ZeroDivisionError", [
        p.source for p in passages
    ]
    for passage in passages:
        assert passage.distance is not None and passage.score is not None
        assert ".md" in passage.source, "every passage must cite its source guide"


def test_results_come_back_closest_first(index_ready):
    passages = similarity_search("how should I name my functions", top_k=4)
    distances = [p.distance for p in passages]
    assert distances == sorted(distances)


def test_index_lists_every_guide(index_ready):
    documents = list_indexed_documents(get_settings())
    assert len(documents) == 6
    assert sum(d["chunks"] for d in documents) == index_ready.chunk_count


def test_explaining_code_never_retrieves():
    decision = policy_decision(Intent.CODE_EXPLANATION, AgentName.EXPLAINER)
    assert decision.should_retrieve is False
    assert decision.passages == []
    assert decision.reason


def test_the_fixer_never_retrieves():
    for intent in (Intent.FULL_REVIEW, Intent.DEBUGGING, Intent.STYLE_IMPROVEMENT):
        assert RETRIEVAL_POLICY[(intent, AgentName.FIXER)] is None


def test_style_review_retrieves_style_notes(index_ready):
    decision = policy_decision(Intent.STYLE_IMPROVEMENT, AgentName.STYLE_LINT)
    assert decision.should_retrieve is True
    assert decision.passages
    assert any("style" in p.source.lower() or "readab" in p.source.lower()
               for p in decision.passages)


def test_turning_rag_off_stops_all_retrieval():
    decision = policy_decision(Intent.STYLE_IMPROVEMENT, AgentName.STYLE_LINT, rag_enabled=False)
    assert decision.should_retrieve is False
    assert "turned off" in decision.reason


def test_an_unlisted_combination_does_not_retrieve():
    decision = policy_decision(Intent.TEST_GENERATION, AgentName.BUG_FINDER)
    assert decision.should_retrieve is False
