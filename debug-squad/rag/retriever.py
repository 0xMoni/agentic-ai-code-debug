"""Agentic RAG retrieval."""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import Settings, get_settings
from models.schemas import (
    AgentName,
    Intent,
    RetrievalDecision,
    RetrievedPassage,
)
from rag.ingest import collection_count, get_collection

RETRIEVAL_POLICY: dict[tuple[Intent, AgentName], str | None] = {
    (Intent.CODE_EXPLANATION, AgentName.EXPLAINER): None,
    (Intent.STYLE_IMPROVEMENT, AgentName.STYLE_LINT): "python naming conventions readability PEP 8",
    (Intent.FULL_REVIEW, AgentName.STYLE_LINT): "python naming conventions readability PEP 8",
    (Intent.DEBUGGING, AgentName.BUG_FINDER): "common python exceptions debugging edge cases",
    (Intent.FULL_REVIEW, AgentName.BUG_FINDER): "common python exceptions edge cases boundary conditions",
    (Intent.TEST_GENERATION, AgentName.TEST_WRITER): "testing principles boundary edge cases test oracle",
    (Intent.FULL_REVIEW, AgentName.TEST_WRITER): "testing principles boundary edge cases test oracle",
    (Intent.FULL_REVIEW, AgentName.FIXER): None,
    (Intent.DEBUGGING, AgentName.FIXER): None,
    (Intent.STYLE_IMPROVEMENT, AgentName.FIXER): None,
}

SKIP_REASONS: dict[AgentName, str] = {
    AgentName.EXPLAINER: (
        "Skipped - explaining code needs only the code itself. Reference "
        "material would add tokens without improving the answer."
    ),
    AgentName.FIXER: (
        "Skipped - the Fixer already receives the Bug-Finder and Style/Lint "
        "findings. The correction is determined by those, not by documentation."
    ),
}


@dataclass
class RetrievalStatus:
    """Health of the vector store, for the UI and the compliance dashboard."""

    available: bool
    chunk_count: int
    collection: str
    persist_dir: str
    error: str | None = None


def retrieval_status(settings: Settings | None = None) -> RetrievalStatus:
    settings = settings or get_settings()
    try:
        count = collection_count(settings)
        return RetrievalStatus(
            available=count > 0,
            chunk_count=count,
            collection=settings.chroma_collection,
            persist_dir=str(settings.persist_path),
            error=None
            if count > 0
            else "Collection is empty. Build it with: python -m rag.ingest",
        )
    except Exception as exc:  # noqa: BLE001
        return RetrievalStatus(
            available=False,
            chunk_count=0,
            collection=settings.chroma_collection,
            persist_dir=str(settings.persist_path),
            error=f"{type(exc).__name__}: {exc}",
        )


def similarity_search(
    query: str,
    *,
    top_k: int | None = None,
    settings: Settings | None = None,
) -> list[RetrievedPassage]:
    """Query the Chroma collection."""
    settings = settings or get_settings()
    top_k = top_k or settings.rag_top_k

    collection = get_collection(settings)
    result = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    passages: list[RetrievedPassage] = []
    for index, text in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) else {}
        distance = distances[index] if index < len(distances) else None
        source = str(metadata.get("source", "unknown"))
        section = metadata.get("section")
        label = f"{source} - {section}" if section else source
        passages.append(
            RetrievedPassage(
                text=text,
                source=label,
                chunk_index=int(metadata.get("chunk_index", index)),
                distance=float(distance) if distance is not None else None,
                score=round(max(0.0, 1.0 - float(distance)), 4)
                if distance is not None
                else None,
            )
        )
    return passages


def policy_decision(
    intent: Intent,
    agent: AgentName,
    *,
    rag_enabled: bool = True,
    settings: Settings | None = None,
) -> RetrievalDecision:
    """Decide and, if appropriate, retrieve - using the deterministic policy."""
    settings = settings or get_settings()

    if not rag_enabled:
        return RetrievalDecision(
            agent=agent,
            should_retrieve=False,
            reason="Skipped - RAG was turned off for this review in the workspace.",
        )

    key = (intent, agent)
    query = RETRIEVAL_POLICY.get(key)

    if query is None:
        reason = SKIP_REASONS.get(
            agent,
            f"Skipped - the retrieval policy has no knowledge need for "
            f"{agent.value} on a '{intent.value}' request.",
        )
        return RetrievalDecision(agent=agent, should_retrieve=False, reason=reason)

    status = retrieval_status(settings)
    if not status.available:
        return RetrievalDecision(
            agent=agent,
            should_retrieve=True,
            query=query,
            reason=(
                f"Retrieval wanted for {agent.value} on a '{intent.value}' "
                "request, but the vector store is not ready."
            ),
            error=status.error,
        )

    try:
        passages = similarity_search(query, settings=settings)
    except Exception as exc:  # noqa: BLE001
        return RetrievalDecision(
            agent=agent,
            should_retrieve=True,
            query=query,
            reason=f"Retrieval attempted for {agent.value} but the search failed.",
            error=f"{type(exc).__name__}: {exc}",
        )

    if not passages:
        return RetrievalDecision(
            agent=agent,
            should_retrieve=True,
            query=query,
            reason=(
                f"Retrieval ran for {agent.value} but the knowledge base "
                "returned no passages for this query."
            ),
            error="Empty result set.",
        )

    return RetrievalDecision(
        agent=agent,
        should_retrieve=True,
        query=query,
        reason=(
            f"Retrieved for {agent.value}: a '{intent.value}' request depends on "
            f"documented conventions, so {len(passages)} passage(s) were pulled "
            "from the knowledge base and passed into the prompt."
        ),
        passages=passages,
    )


def list_indexed_documents(settings: Settings | None = None) -> list[dict]:
    """Per-document chunk counts, e.g. for checking what was ingested."""
    settings = settings or get_settings()
    try:
        collection = get_collection(settings)
        stored = collection.get(include=["metadatas"])
    except Exception:  # noqa: BLE001
        return []

    summary: dict[str, dict] = {}
    for metadata in stored.get("metadatas") or []:
        source = str(metadata.get("source", "unknown"))
        entry = summary.setdefault(
            source,
            {
                "source": source,
                "title": metadata.get("title", source),
                "chunks": 0,
                "characters": 0,
                "sections": set(),
            },
        )
        entry["chunks"] += 1
        entry["characters"] += int(metadata.get("characters", 0))
        if metadata.get("section"):
            entry["sections"].add(metadata["section"])

    documents = []
    for entry in summary.values():
        entry["sections"] = sorted(entry["sections"])
        documents.append(entry)
    return sorted(documents, key=lambda d: d["source"])
