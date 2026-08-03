"""Embedding providers: watsonx (primary), sentence-transformers, hash fallback.

Vector spaces are incompatible across providers, so each gets its own Chroma
collection (``kb_<provider>``) — resolution here decides which one is active.

Resolution for ``EMBEDDINGS_PROVIDER``:

* ``watsonx`` — IBM Granite embeddings; requires credentials (fail fast).
* ``local``   — sentence-transformers MiniLM; requires the optional dependency.
* ``auto``    — watsonx if credentials exist, else sentence-transformers if
  installed, else the dependency-free **hash** provider so the whole pipeline
  (upload → index → search → citations) still works mechanically in demo
  environments. The active provider is always visible in /api/system/info.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from typing import Sequence

from nutrimind.config import Settings
from nutrimind.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

_EMBED_BATCH_SIZE = 16  # keeps individual watsonx requests small


class EmbeddingProvider(ABC):
    """Contract shared by all embedding backends."""

    #: short identifier — also the Chroma collection suffix.
    name: str = "unset"

    #: True when similarity reflects *meaning* rather than shared words.
    #: The UI uses this to say what the knowledge base can actually do.
    semantic: bool = True

    #: Cosine similarity above which a hit counts as evidence, **for this vector
    #: space**. Not a global constant: a dense 768-dim semantic embedding and a
    #: sparse bag-of-words vector produce completely different scales, so one
    #: number cannot serve both. See LexicalEmbeddingProvider for the arithmetic.
    default_similarity_threshold: float = 0.35

    #: One-line description of the trade-off, shown to the user.
    capability: str = ""

    def matches(self, query: str, text: str) -> bool:  # noqa: ARG002
        """Final say on whether a retrieved passage really answers ``query``.

        Semantic providers accept whatever the vector space ranked highly —
        that is the entire point of an embedding, and demanding shared words
        would throw away every paraphrase it was chosen to find.
        Approximate spaces override this; see LexicalEmbeddingProvider.
        """
        return True

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text (used at ingestion time)."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Vector for a search query (must share the document vector space)."""


class WatsonxEmbeddingProvider(EmbeddingProvider):
    """IBM Granite embeddings via the existing WatsonxClient (768-dim)."""

    name = "watsonx"
    capability = "semantic matching on IBM Granite embeddings"

    def __init__(self, settings: Settings) -> None:
        if not settings.watsonx.has_credentials:
            raise ConfigurationError(
                "EMBEDDINGS_PROVIDER=watsonx requires IBM credentials.",
                hint="Fill .env (docs/IBM_SETUP.md) or use EMBEDDINGS_PROVIDER=auto.",
            )
        from nutrimind.services.llm.watsonx_client import WatsonxClient

        self._client = WatsonxClient(settings.watsonx)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            vectors.extend(self._client.embed(texts[start:start + _EMBED_BATCH_SIZE]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed([text])[0]


class LocalEmbeddingProvider(EmbeddingProvider):
    """sentence-transformers MiniLM (384-dim) — offline, optional dependency."""

    name = "local"
    capability = "semantic matching on a local sentence-transformers model"
    _MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ConfigurationError(
                "EMBEDDINGS_PROVIDER=local requires the optional dependency.",
                hint="pip install sentence-transformers (large: pulls PyTorch).",
            ) from None
        self._model = SentenceTransformer(self._MODEL_ID)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.encode(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([text])[0].tolist()


class LexicalEmbeddingProvider(EmbeddingProvider):
    """Dependency-free keyword search, expressed as vectors.

    Replaces an earlier "hash" provider that hashed each whole chunk into one
    random direction. That kept the pipeline *mechanically* exercisable but made
    retrieval useless in practice: two texts sharing every word still landed in
    unrelated directions, so the only query that could ever match a chunk was its
    own text, byte for byte. Uploading a cookbook and asking for "pad thai"
    returned nothing, and the UI blamed the similarity threshold.

    This is the hashing trick instead: tokenise, hash each *token* to a
    dimension, weight it, normalise. Cosine similarity then measures shared
    vocabulary, so "pad thai" finds the chunk containing those words. Still not
    semantic — it cannot connect "aubergine" to "eggplant" — but it is honest
    keyword search rather than noise, and it needs no model and no credentials.

    **Why its threshold is so much lower than a semantic provider's.** Cosine
    between a short query and a long chunk is bounded by how much of each vector
    can overlap. A 2-token query against a 150-token chunk cannot exceed roughly
    ``sqrt(2/150) ≈ 0.12`` however perfect the match. Judging this space by the
    0.35 that suits dense embeddings would reject every correct hit — which is
    exactly the failure this class was written to fix.
    """

    name = "lexical"
    semantic = False
    capability = ("keyword matching only — finds passages that share words with "
                  "your question, not ones that merely mean the same thing")
    #: Chosen by measurement, not by feel. Against a real 28-chunk cookbook the
    #: worst genuine match scored 0.159 and the best false one 0.106, so this
    #: sits between them with margin on both sides. It is still a heuristic
    #: calibrated on one corpus; erring slightly high is deliberate, because a
    #: false positive here produces a confidently *cited* wrong answer, whereas a
    #: false negative only falls back to the visible "general knowledge" path.
    default_similarity_threshold = 0.12

    #: 2048, not 384. With 384 buckets a query token collides with some unrelated
    #: document token about a quarter of the time, and those collisions scored
    #: absent topics (0.130) above genuine matches (0.150) — no threshold can
    #: separate that. 2048 pushes collisions down far enough to give clean
    #: separation; 4096 measured no better, so it would only cost storage.
    _DIM = 2048
    _TOKEN = re.compile(r"[a-z0-9]+")
    _MIN_TOKEN_LENGTH = 2

    #: Function words appear in every passage, so they add similarity between
    #: texts that share nothing meaningful. Dropping them roughly doubled the
    #: measured gap between matching and non-matching passages.
    _STOPWORDS = frozenset("""
        a an and are as at be but by can could do does for from had has have how
        i if in into is it its me my not of on or our so that the their then
        there these they this to was we were what when where which who will with
        you your
    """.split())

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        return [t for t in cls._TOKEN.findall((text or "").lower())
                if len(t) >= cls._MIN_TOKEN_LENGTH and t not in cls._STOPWORDS]

    @staticmethod
    def _bucket(token: str) -> tuple[int, float]:
        """Map a token to (dimension, sign).

        blake2b rather than Python's ``hash()``: that is salted per process, so
        the same text would embed differently after every restart and stored
        vectors would stop matching new queries.

        The sign is the standard signed-hashing trick. Two different tokens
        landing in the same bucket is unavoidable with a fixed width; with a
        random sign their interference cancels on average instead of always
        adding, so a collision no longer looks like evidence. Without it,
        "quantum chromodynamics" scored above the threshold against a Thai
        cookbook purely through collisions.
        """
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=5).digest()
        bucket = int.from_bytes(digest[:4], "big") % LexicalEmbeddingProvider._DIM
        return bucket, 1.0 if digest[4] & 1 else -1.0

    def matches(self, query: str, text: str) -> bool:
        """Require at least one shared word.

        Signed hashing makes collisions rare, not impossible, and a rare false
        positive here is expensive: the answer is presented as *grounded*, with
        a citation, which is worse than admitting nothing was found. This is
        also simply what keyword search means — a passage sharing no word with
        the question is not a keyword match, whatever the arithmetic says.
        """
        return bool(set(self._tokenize(query)) & set(self._tokenize(text)))

    @classmethod
    def _vector(cls, text: str) -> list[float]:
        """L2-normalised term-frequency vector over hashed token buckets."""
        counts: dict[str, float] = {}
        for token in cls._tokenize(text):
            counts[token] = counts.get(token, 0.0) + 1.0

        vector = [0.0] * cls._DIM
        for token, count in counts.items():
            bucket, sign = cls._bucket(token)
            # Sublinear term frequency: a word repeated ten times is more
            # relevant than one used once, but not ten times more.
            vector[bucket] += sign * (1.0 + math.log(count))

        norm = sum(value * value for value in vector) ** 0.5
        if not norm:  # text with no usable tokens
            return vector
        return [value / norm for value in vector]


def _local_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


def resolve_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Pick the embedding backend per configuration (see module docstring)."""
    requested = settings.embeddings_provider
    if requested == "watsonx":
        return WatsonxEmbeddingProvider(settings)
    if requested == "local":
        return LocalEmbeddingProvider()

    # auto
    if settings.watsonx.has_credentials:
        return WatsonxEmbeddingProvider(settings)
    if _local_available():
        logger.info("embeddings: auto -> local (no IBM credentials)")
        return LocalEmbeddingProvider()
    logger.warning(
        "embeddings: auto -> lexical (no IBM credentials and "
        "sentence-transformers not installed). Keyword matching works; "
        "semantic matching does not — see docs/IBM_SETUP.md.")
    return LexicalEmbeddingProvider()
