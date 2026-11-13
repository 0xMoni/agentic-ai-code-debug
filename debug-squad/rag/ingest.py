"""Knowledge-base ingestion into ChromaDB."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import Settings, get_settings

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2 (ONNX, local)"

CHUNK_TARGET_CHARS = 900
CHUNK_OVERLAP_CHARS = 150
CHUNK_MIN_CHARS = 120


@dataclass
class Chunk:
    text: str
    source: str
    title: str
    section: str
    index: int

    @property
    def chunk_id(self) -> str:
        digest = hashlib.sha1(
            f"{self.source}:{self.index}:{self.text}".encode()
        ).hexdigest()[:16]
        return f"{Path(self.source).stem}-{self.index:03d}-{digest}"


def _document_title(text: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown on `##` headings into (section title, body) pairs."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title = "Introduction"
    current: list[str] = []

    for line in lines:
        heading = re.match(r"^##\s+(.+)$", line)
        if heading:
            if current and any(ln.strip() for ln in current):
                sections.append((current_title, current))
            current_title = heading.group(1).strip()
            current = []
        elif not re.match(r"^#\s+", line):
            current.append(line)

    if current and any(ln.strip() for ln in current):
        sections.append((current_title, current))
    return [(title, "\n".join(body).strip()) for title, body in sections]


def _split_long_body(body: str) -> list[str]:
    """Split a long section into overlapping windows on paragraph breaks."""
    if len(body) <= CHUNK_TARGET_CHARS:
        return [body]

    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    windows: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if not current:
            current = paragraph
        elif len(current) + len(paragraph) + 2 <= CHUNK_TARGET_CHARS:
            current = f"{current}\n\n{paragraph}"
        else:
            windows.append(current)
            tail = current[-CHUNK_OVERLAP_CHARS:]
            boundary = tail.find("\n")
            overlap = tail[boundary + 1 :] if boundary != -1 else tail
            current = f"{overlap}\n\n{paragraph}" if overlap.strip() else paragraph

    if current:
        windows.append(current)

    final: list[str] = []
    for window in windows:
        if len(window) <= CHUNK_TARGET_CHARS * 1.6:
            final.append(window)
            continue
        step = CHUNK_TARGET_CHARS - CHUNK_OVERLAP_CHARS
        for start in range(0, len(window), step):
            piece = window[start : start + CHUNK_TARGET_CHARS]
            if len(piece) >= CHUNK_MIN_CHARS:
                final.append(piece)
    return final


def chunk_document(path: Path) -> list[Chunk]:
    """Turn one markdown file into retrievable chunks."""
    text = path.read_text(encoding="utf-8")
    title = _document_title(text, path.stem.replace("_", " ").title())
    chunks: list[Chunk] = []

    for section_title, body in _split_sections(text):
        for piece in _split_long_body(body):
            piece = piece.strip()
            if len(piece) < CHUNK_MIN_CHARS:
                continue
            chunks.append(
                Chunk(
                    text=f"{title} - {section_title}\n\n{piece}",
                    source=path.name,
                    title=title,
                    section=section_title,
                    index=len(chunks),
                )
            )
    return chunks


def chunk_all(kb_path: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(kb_path.glob("*.md")):
        chunks.extend(chunk_document(path))
    return chunks


@dataclass
class IngestResult:
    ok: bool
    documents: int = 0
    chunks: int = 0
    collection: str = ""
    persist_dir: str = ""
    per_document: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def get_embedding_function():
    """Chroma's bundled local ONNX MiniLM embedding function."""
    from chromadb.utils import embedding_functions

    return embedding_functions.ONNXMiniLM_L6_V2()


def get_client(settings: Settings | None = None):
    """A persistent Chroma client rooted at the configured directory."""
    import chromadb

    settings = settings or get_settings()
    settings.persist_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.persist_path))


def get_collection(settings: Settings | None = None, *, create: bool = True):
    """Fetch (or create) the knowledge-base collection."""
    settings = settings or get_settings()
    client = get_client(settings)
    embedding_function = get_embedding_function()
    if create:
        return client.get_or_create_collection(
            name=settings.chroma_collection,
            embedding_function=embedding_function,
            metadata={"hnsw:space": "cosine"},
        )
    return client.get_collection(
        name=settings.chroma_collection, embedding_function=embedding_function
    )


def collection_count(settings: Settings | None = None) -> int:
    """Number of stored chunks, or 0 if the collection does not exist yet."""
    try:
        return get_collection(settings).count()
    except Exception:  # noqa: BLE001
        return 0


def ingest(
    settings: Settings | None = None, *, rebuild: bool = False
) -> IngestResult:
    """Build the vector index."""
    settings = settings or get_settings()
    kb_path = settings.knowledge_base_path

    if not kb_path.exists():
        return IngestResult(
            ok=False, error=f"Knowledge base directory not found: {kb_path}"
        )

    try:
        client = get_client(settings)
        embedding_function = get_embedding_function()
    except Exception as exc:  # noqa: BLE001
        return IngestResult(
            ok=False,
            error=(
                f"ChromaDB could not be initialised ({type(exc).__name__}: {exc}). "
                "The first run downloads the local embedding model (~80 MB) and "
                "needs internet access once."
            ),
        )

    if rebuild:
        with contextlib.suppress(Exception):
            client.delete_collection(settings.chroma_collection)

    try:
        collection = client.get_or_create_collection(
            name=settings.chroma_collection,
            embedding_function=embedding_function,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as exc:  # noqa: BLE001
        return IngestResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    chunks = chunk_all(kb_path)
    if not chunks:
        return IngestResult(
            ok=False,
            error=f"No markdown documents found in {kb_path}",
            collection=settings.chroma_collection,
        )

    existing = collection.count()
    if existing and not rebuild:
        per_document: dict[str, int] = {}
        for chunk in chunks:
            per_document[chunk.source] = per_document.get(chunk.source, 0) + 1
        return IngestResult(
            ok=True,
            documents=len(per_document),
            chunks=existing,
            collection=settings.chroma_collection,
            persist_dir=str(settings.persist_path),
            per_document=per_document,
        )

    per_document = {}
    try:
        batch_size = 32
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            collection.add(
                ids=[c.chunk_id for c in batch],
                documents=[c.text for c in batch],
                metadatas=[
                    {
                        "source": c.source,
                        "title": c.title,
                        "section": c.section,
                        "chunk_index": c.index,
                        "characters": len(c.text),
                    }
                    for c in batch
                ],
            )
        for chunk in chunks:
            per_document[chunk.source] = per_document.get(chunk.source, 0) + 1
    except Exception as exc:  # noqa: BLE001
        return IngestResult(
            ok=False,
            error=f"Embedding/insert failed - {type(exc).__name__}: {exc}",
            collection=settings.chroma_collection,
        )

    return IngestResult(
        ok=True,
        documents=len(per_document),
        chunks=collection.count(),
        collection=settings.chroma_collection,
        persist_dir=str(settings.persist_path),
        per_document=per_document,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Debug Squad knowledge base.")
    parser.add_argument(
        "--rebuild", action="store_true", help="Delete and rebuild the collection"
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"Knowledge base : {settings.knowledge_base_path}")
    print(f"Persist dir    : {settings.persist_path}")
    print(f"Collection     : {settings.chroma_collection}")
    print(f"Embedding model: {EMBEDDING_MODEL_NAME}")
    print("Ingesting (first run downloads the embedding model)...")

    result = ingest(settings, rebuild=args.rebuild)
    if not result.ok:
        print(f"\nFAILED: {result.error}")
        return 1

    print(f"\nOK - {result.documents} documents, {result.chunks} chunks stored.")
    for source, count in sorted(result.per_document.items()):
        print(f"  {count:>3} chunks  {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
