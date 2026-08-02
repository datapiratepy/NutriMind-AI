"""Unit tests for the page-aware chunker."""

from nutrimind.retrieval.chunker import chunk_pages, clean_page_text

CHUNK = {"chunk_size": 200, "overlap": 40}


def _page(sentences: int, prefix: str = "Sentence") -> str:
    return " ".join(f"{prefix} number {i} about nutrition facts." for i in range(sentences))


def test_chunks_respect_size_budget():
    chunks = chunk_pages([_page(40)], **CHUNK)
    assert len(chunks) > 1
    assert all(len(c.text) <= CHUNK["chunk_size"] + 60 for c in chunks)  # word-snap slack


def test_chunks_never_cross_pages():
    chunks = chunk_pages([_page(20, "Alpha"), _page(20, "Beta")], **CHUNK)
    for chunk in chunks:
        assert not ("Alpha" in chunk.text and "Beta" in chunk.text)
    assert {c.page for c in chunks} == {1, 2}


def test_overlap_carries_boundary_text():
    chunks = chunk_pages([_page(40)], **CHUNK)
    # strict=False is intentional: this is pairwise iteration, so the second
    # sequence is deliberately one shorter than the first.
    for previous, current in zip(chunks, chunks[1:], strict=False):
        if previous.page != current.page:
            continue
        tail_words = previous.text.split()[-3:]
        assert any(word in current.text for word in tail_words)


def test_chunk_indices_are_sequential():
    chunks = chunk_pages([_page(30), _page(30)], **CHUNK)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_empty_and_blank_pages_skipped():
    chunks = chunk_pages(["", "   \n  ", _page(5)], **CHUNK)
    assert chunks and all(c.page == 3 for c in chunks)


def test_oversized_paragraph_is_split():
    one_paragraph = _page(60).replace("\n", " ")
    chunks = chunk_pages([one_paragraph], **CHUNK)
    assert len(chunks) > 1


def test_pathological_unbroken_text_hard_cut():
    chunks = chunk_pages(["x" * 1000], chunk_size=200, overlap=40)
    # Overlap prepends up to `overlap` chars (+1 separator) to each chunk.
    assert chunks and all(len(c.text) <= 200 + 40 + 1 for c in chunks)


def test_clean_page_text_drops_page_number_lines():
    cleaned = clean_page_text("Real content here.\n  14  \nMore content.")
    assert "14" not in cleaned


def test_determinism():
    pages = [_page(25), _page(25, "Other")]
    assert chunk_pages(pages, **CHUNK) == chunk_pages(pages, **CHUNK)
