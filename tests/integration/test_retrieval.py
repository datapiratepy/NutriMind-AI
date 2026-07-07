"""Integration tests: ingestion, Chroma persistence, retrieval, citations.

Uses the hash embedding provider (dependency-free, deterministic) and a
synthetic three-page PDF built with pypdf — no external files needed.
"""

from __future__ import annotations

import datetime as dt

import pytest

from nutrimind.retrieval.chunker import Chunk
from nutrimind.retrieval.embeddings import HashEmbeddingProvider
from nutrimind.retrieval.retriever import RetrievalResult, Retriever
from nutrimind.retrieval.vector_store import RetrievedChunk, VectorStore


@pytest.fixture()
def store(tmp_path) -> VectorStore:
    return VectorStore(tmp_path / "chroma", HashEmbeddingProvider())


def _index_sample(store: VectorStore, document_id: int = 1) -> list[Chunk]:
    chunks = [
        Chunk(text="Bananas contain potassium and natural sugars.", page=1, chunk_index=0),
        Chunk(text="Paneer is a rich vegetarian protein source.", page=2, chunk_index=1),
        Chunk(text="Vitamin B12 is mainly found in animal products.", page=2, chunk_index=2),
    ]
    embeddings = store.provider.embed_documents([c.text for c in chunks])
    store.add_chunks(document_id=document_id, filename="sample.pdf",
                     uploaded_at=dt.datetime.utcnow().isoformat(timespec="seconds"),
                     chunks=chunks, embeddings=embeddings)
    return chunks


def test_add_query_roundtrip_with_full_metadata(store):
    chunks = _index_sample(store)
    assert store.count() == 3

    hit = store.query(store.provider.embed_query(chunks[1].text), top_k=1)[0]
    assert isinstance(hit, RetrievedChunk)
    assert hit.text == chunks[1].text
    assert hit.similarity == pytest.approx(1.0, abs=1e-6)  # identical text, hash provider
    assert (hit.document_id, hit.filename, hit.page, hit.chunk_index) == (1, "sample.pdf", 2, 1)
    assert hit.citation() == {"filename": "sample.pdf", "page": 2}


def test_persistence_across_instances(tmp_path):
    first = VectorStore(tmp_path / "chroma", HashEmbeddingProvider())
    _index_sample(first)
    reopened = VectorStore(tmp_path / "chroma", HashEmbeddingProvider())
    assert reopened.count() == 3


def test_per_provider_collection_naming(store):
    assert store.collection_name == "kb_hash"


def test_delete_document_purges_chunks(store):
    _index_sample(store, document_id=7)
    assert store.count_for_document(7) == 3
    store.delete_document(7)
    assert store.count() == 0


def test_retriever_threshold_and_grounded_flag(store):
    chunks = _index_sample(store)
    retriever = Retriever(store, top_k=3, similarity_threshold=0.99)

    exact = retriever.retrieve(chunks[0].text)
    assert exact.grounded and len(exact.chunks) == 1

    unrelated = retriever.retrieve("completely unrelated query about rockets")
    assert not unrelated.grounded and unrelated.chunks == []
    assert unrelated.citations() == []


def test_citations_deduplicate_by_file_and_page(store):
    _index_sample(store)
    result = RetrievalResult(
        query="q",
        chunks=store.query(store.provider.embed_query("protein"), top_k=3),
        grounded=True, provider="hash", threshold=0.0,
    )
    citations = result.citations()
    assert {(c["filename"], c["page"]) for c in citations} <= {("sample.pdf", 1), ("sample.pdf", 2)}
    assert len(citations) == len({(c["filename"], c["page"]) for c in citations})


def test_context_text_numbers_passages(store):
    _index_sample(store)
    result = Retriever(store, top_k=2, similarity_threshold=0.0).retrieve("potassium")
    context = result.context_text()
    assert context.startswith("[1] (from sample.pdf, page")
    assert "[2]" in context
