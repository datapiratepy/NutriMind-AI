"""Page-aware, paragraph-preserving text chunker.

Strategy (defaults live in ``config.RAGSettings`` and are documented there):

1. Chunks never cross page boundaries — citations stay exact (filename +
   page). The cost is that a paragraph straddling a page break is split;
   acceptable for guideline-style PDFs.
2. Within a page, paragraphs are greedily packed up to ``chunk_size`` chars.
   Oversized paragraphs fall back to sentence packing, then to a hard cut.
3. Consecutive chunks share an ``overlap``-character tail, snapped to a word
   boundary, so sentences near a boundary appear in both chunks.

Pure functions, fully deterministic — tested without any I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"[ \t]+")
_PAGE_NUMBER_LINE = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit of text with citation-grade location info."""

    text: str
    page: int          # 1-based page number
    chunk_index: int   # 0-based index within the document


def clean_page_text(raw: str) -> str:
    """Normalize extracted PDF text: collapse spaces, drop page-number lines."""
    lines = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        line = _WHITESPACE.sub(" ", line).strip()
        if _PAGE_NUMBER_LINE.match(line):
            continue
        lines.append(line)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _split_oversized(paragraph: str, chunk_size: int) -> list[str]:
    """Sentence-pack an oversized paragraph; hard-cut pathological sentences."""
    pieces: list[str] = []
    buffer = ""
    for sentence in _SENTENCE_SPLIT.split(paragraph):
        while len(sentence) > chunk_size:  # pathological unbroken run
            pieces.append(sentence[:chunk_size])
            sentence = sentence[chunk_size:]
        if buffer and len(buffer) + len(sentence) + 1 > chunk_size:
            pieces.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}".strip()
    if buffer:
        pieces.append(buffer)
    return pieces


def _overlap_tail(text: str, overlap: int) -> str:
    """Last ``overlap`` chars of ``text``, snapped forward to a word boundary."""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    first_space = tail.find(" ")
    return tail[first_space + 1:] if first_space != -1 else tail


def chunk_pages(pages: list[str], *, chunk_size: int, overlap: int) -> list[Chunk]:
    """Chunk cleaned page texts into overlapping, page-bounded units.

    :param pages: raw text per page (index 0 = page 1); empty pages skipped.
    """
    chunks: list[Chunk] = []
    for page_number, raw in enumerate(pages, start=1):
        text = clean_page_text(raw or "")
        if not text:
            continue

        units: list[str] = []
        for paragraph in _PARAGRAPH_SPLIT.split(text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if len(paragraph) > chunk_size:
                units.extend(_split_oversized(paragraph, chunk_size))
            else:
                units.append(paragraph)

        buffer = ""
        page_chunks: list[str] = []
        for unit in units:
            if buffer and len(buffer) + len(unit) + 2 > chunk_size:
                page_chunks.append(buffer)
                buffer = f"{_overlap_tail(buffer, overlap)} {unit}".strip()
            else:
                buffer = f"{buffer}\n\n{unit}".strip() if buffer else unit
        if buffer:
            page_chunks.append(buffer)

        for text_piece in page_chunks:
            chunks.append(Chunk(text=text_piece, page=page_number,
                                chunk_index=len(chunks)))
    return chunks
