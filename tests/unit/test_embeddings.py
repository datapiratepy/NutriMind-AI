"""Embedding providers, and the property the lexical one exists to guarantee.

The zero-credential provider used to hash each whole chunk into one random
direction. That satisfied every structural test — vectors had the right length,
were deterministic, were unit length — while being useless in practice: it could
only ever match a query byte-identical to the chunk. A real cookbook was indexed
and "pad thai" returned nothing.

So these tests assert *retrieval behaviour*, not vector shape. A provider that
passes them can actually find things; the old one could not, and no test noticed.
"""

from __future__ import annotations

import pytest

from nutrimind.config import load_settings
from nutrimind.retrieval.embeddings import (
    EmbeddingProvider,
    LexicalEmbeddingProvider,
    resolve_embedding_provider,
)

#: Stand-in for indexed passages: overlapping vocabulary, distinct topics.
CORPUS = {
    "thai": "Pad thai is made with rice noodles, tamarind, fish sauce, peanuts "
            "and bean sprouts, stir fried over very high heat.",
    "curry": "Green curry simmers coconut milk with green curry paste, "
             "lemongrass, galangal, thai basil and bamboo shoots.",
    "bmi": "Body mass index divides weight in kilograms by the square of height "
           "in metres, and the healthy adult band runs from 18.5 to 24.9.",
    "iron": "Spinach, lentils and fortified cereals are useful sources of iron; "
            "vitamin C alongside them improves absorption.",
}


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.fixture()
def provider() -> LexicalEmbeddingProvider:
    return LexicalEmbeddingProvider()


def _best_match(provider, query: str) -> tuple[str, float]:
    scores = {
        key: _cosine(provider.embed_query(query), provider.embed_documents([text])[0])
        for key, text in CORPUS.items()
    }
    key = max(scores, key=scores.get)
    return key, scores[key]


# -- the regression this provider was written for -----------------------------

@pytest.mark.parametrize("query,expected", [
    ("pad thai", "thai"),
    ("fish sauce", "thai"),
    ("rice noodles", "thai"),
    ("coconut milk", "curry"),
    ("lemongrass", "curry"),
    ("body mass index", "bmi"),
    ("sources of iron", "iron"),
])
def test_a_query_finds_the_passage_containing_its_words(provider, query, expected):
    """The whole point: shared vocabulary must produce the top score.

    The previous provider failed every one of these — it hashed whole texts, so a
    two-word query landed nowhere near the passage containing those two words.
    """
    match, score = _best_match(provider, query)
    assert match == expected, f"{query!r} matched {match!r} instead of {expected!r}"
    assert score >= provider.default_similarity_threshold, (
        f"{query!r} scored {score:.4f}, below the provider's own threshold "
        f"{provider.default_similarity_threshold} — it would be discarded")


@pytest.mark.parametrize("query", [
    "quantum chromodynamics",
    "kubernetes ingress controller",
    "mortgage interest deduction",
])
def test_unrelated_queries_are_rejected(provider, query):
    """A false positive here is worse than a miss.

    An accepted hit is shown to the user as *grounded*, with a citation. Hash
    collisions once pushed "quantum chromodynamics" above the threshold against a
    Thai cookbook, which is why matches() verifies real word overlap instead of
    trusting the arithmetic alone.
    """
    for text in CORPUS.values():
        assert not provider.matches(query, text), (
            f"{query!r} was accepted as a match for unrelated text")


def test_match_verification_requires_a_shared_word(provider):
    assert provider.matches("coconut milk", CORPUS["curry"])
    assert not provider.matches("coconut milk", CORPUS["bmi"])


# -- properties the vector space must keep ------------------------------------

def test_embeddings_are_deterministic_across_instances(provider):
    """Stored vectors are compared against queries embedded later, possibly in
    another process, so the token hash must not be salted per run."""
    first = provider.embed_query("coconut milk and lemongrass")
    second = LexicalEmbeddingProvider().embed_query("coconut milk and lemongrass")
    assert first == second


def test_vectors_are_unit_length(provider):
    vector = provider.embed_documents([CORPUS["thai"]])[0]
    assert _cosine(vector, vector) == pytest.approx(1.0, abs=1e-9)


def test_query_and_document_share_one_vector_space(provider):
    """embed_query and embed_documents must agree, or nothing ever matches."""
    text = CORPUS["curry"]
    assert provider.embed_query(text) == provider.embed_documents([text])[0]


def test_text_with_no_usable_tokens_is_handled(provider):
    """Punctuation-only chunks occur in real PDFs; they must not divide by zero."""
    vector = provider.embed_query("!!! ... ???")
    assert len(vector) == LexicalEmbeddingProvider._DIM
    assert all(value == 0.0 for value in vector)


def test_vocabulary_matters_and_word_order_does_not(provider):
    """Bag of words, honestly described: this is keyword search."""
    assert provider.embed_query("fish sauce") == provider.embed_query("sauce fish")
    assert provider.embed_query("fish sauce") != provider.embed_query("fish stock")


# -- honesty about what the provider can do -----------------------------------

def test_lexical_provider_declares_itself_non_semantic(provider):
    """The UI branches on this to explain an empty result instead of blaming the
    threshold, and to say that semantic search needs credentials."""
    assert provider.semantic is False
    assert provider.capability


def test_lexical_threshold_is_far_below_the_semantic_one(provider):
    """Thresholds belong to a vector space, not to the application.

    Cosine between a short query and a long chunk is bounded by how much of the
    two vectors can overlap, so a sparse lexical space cannot reach the values a
    dense semantic one does. Judging it by 0.35 rejects every correct hit — which
    is precisely how this bug presented itself.
    """
    assert provider.default_similarity_threshold < \
        EmbeddingProvider.default_similarity_threshold


def test_semantic_providers_do_not_require_shared_words():
    """matches() must stay a no-op for real embeddings.

    Demanding word overlap there would discard every paraphrase — exactly what a
    semantic model is chosen for.
    """
    class _Semantic(EmbeddingProvider):
        name = "fake-semantic"

        def embed_documents(self, texts):
            return [[1.0] for _ in texts]

        def embed_query(self, text):  # noqa: ARG002 — interface parity
            return [1.0]

    assert _Semantic().matches("aubergine recipes", "how to roast an eggplant")


def test_auto_resolution_yields_a_provider_that_can_retrieve(monkeypatch):
    """No credentials + no sentence-transformers -> lexical, not noise."""
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "auto")
    monkeypatch.delenv("WATSONX_APIKEY", raising=False)
    monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
    settings = load_settings(ensure_dirs=False)
    resolved = resolve_embedding_provider(settings)
    assert resolved.name in ("lexical", "local")  # local only if the extra is installed
    assert resolved.default_similarity_threshold > 0
