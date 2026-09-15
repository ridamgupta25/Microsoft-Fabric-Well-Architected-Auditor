"""Turn an uploaded document into clean, citable, retrievable chunks.

Parse (PDF/DOCX/MD/TXT) keeps page numbers so a citation can point at a page; the
splitter carries the source filename and page onto every chunk; and the retriever
finds the pieces relevant to one check. Retrieval prefers embeddings (FastEmbed)
and falls back to keyword overlap, so it works on a base install with no AI extra.

Uploaded documents are **untrusted input**: :func:`scrub` drops any chunk whose
text carries prompt-injection phrasing before it can ever reach the model.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from ..agents.guardrails_agent import _INJECTION
from ..orchestrator.ai_config import AiConfig
from ..rag.embeddings import embed
from ..rag.vector_store import VectorStore

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "the a an of to and or is are for in on with per that this its into not no "
    "be by as at from which each all any only".split()
)


class UnsupportedTypeError(ValueError):
    """Raised when an uploaded file's extension has no parser."""


@dataclass(frozen=True, slots=True)
class Chunk:
    """A slice of one document, tagged with where it came from for citations."""

    text: str
    source: str  # filename
    page: int


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def parse(data: bytes, filename: str) -> list[tuple[int, str]]:
    """Extract ``(page_number, text)`` pairs from a document by extension.

    MD/TXT and DOCX are single-"page" (page 1); PDF preserves real page numbers.
    Raises :class:`UnsupportedTypeError` for anything without a parser.
    """
    ext = extension_of(filename)
    if ext == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return [(i + 1, (page.extract_text() or "")) for i, page in enumerate(reader.pages)]
    if ext == "docx":
        from docx import Document

        document = Document(io.BytesIO(data))
        return [(1, "\n".join(p.text for p in document.paragraphs))]
    if ext in ("md", "txt"):
        return [(1, data.decode("utf-8", "ignore"))]
    raise UnsupportedTypeError(ext)


def chunk(pages: list[tuple[int, str]], filename: str, *, size: int = 1200, overlap: int = 150) -> list[Chunk]:
    """Split each page into overlapping windows, carrying source + page."""
    out: list[Chunk] = []
    step = max(1, size - overlap)
    for page, text in pages:
        clean = text.strip()
        if not clean:
            continue
        i = 0
        while i < len(clean):
            piece = clean[i : i + size].strip()
            if piece:
                out.append(Chunk(text=piece, source=filename, page=page))
            i += step
    return out


def scrub(chunks: list[Chunk]) -> list[Chunk]:
    """Drop chunks carrying prompt-injection phrasing (untrusted-input defence)."""
    return [c for c in chunks if not _INJECTION.search(c.text)]


def parse_and_chunk(data: bytes, filename: str) -> list[Chunk]:
    """Parse, chunk and scrub one uploaded file in one call."""
    return scrub(chunk(parse(data, filename), filename))


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


class Retriever:
    """Find the chunks most relevant to a query, over one upload set.

    Uses embeddings when available (FastEmbed on, per-request key), else falls back
    to keyword-overlap scoring. Either way ``nearest`` returns real ``Chunk``
    objects so the evaluator can cite ``source`` + ``page``.
    """

    _COLLECTION = "evidence"

    def __init__(self, chunks: list[Chunk], *, ai: AiConfig | None = None) -> None:
        self._chunks = chunks
        self._ai = ai
        self._store: VectorStore | None = None
        self._embedded = False
        vectors: list[tuple[str, list[float], dict]] = []
        for idx, ch in enumerate(chunks):
            vec = embed(ch.text, ai=ai)
            if vec is None:
                vectors = []
                break
            vectors.append((str(idx), vec, {"idx": idx}))
        if vectors:
            store = VectorStore()
            store.index(self._COLLECTION, vectors)
            self._store = store
            self._embedded = True

    @property
    def uses_embeddings(self) -> bool:
        return self._embedded

    def nearest(self, query: str, k: int = 6) -> list[Chunk]:
        if not self._chunks:
            return []
        if self._store is not None:
            vec = embed(query, ai=self._ai)
            if vec is not None:
                hits = self._store.nearest(self._COLLECTION, vec, k=k)
                return [self._chunks[int(h.metadata["idx"])] for h in hits]
        return self._keyword_nearest(query, k)

    def _keyword_nearest(self, query: str, k: int) -> list[Chunk]:
        q = _tokens(query)
        if not q:
            return self._chunks[:k]
        scored = [
            (len(q & _tokens(ch.text)) / len(q), i, ch)
            for i, ch in enumerate(self._chunks)
        ]
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [ch for score, _, ch in scored[:k] if score > 0] or self._chunks[:k]


__all__ = ["Chunk", "Retriever", "UnsupportedTypeError", "parse", "chunk", "scrub", "parse_and_chunk"]
