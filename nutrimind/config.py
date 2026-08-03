"""Typed, environment-driven configuration for NutriMind AI.

All secrets and IBM-specific values come from ``.env`` (loaded via
python-dotenv) or real environment variables — never from code. The settings
object is immutable, validated fail-fast at startup, and safe to construct
without any credentials (the app then resolves to demo mode).

Two safety-relevant defaults:

* ``FLASK_DEBUG`` defaults to **off**. The interactive debugger is a remote
  code execution primitive, and defaults are what a rushed deploy inherits.
* When ``FLASK_SECRET_KEY`` is unset, a random key is generated once and
  persisted to ``instance/secret_key`` (see :func:`_resolve_secret_key`), so
  the app never falls back to the publicly-known placeholder value.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"

#: Hosts of the official watsonx.ai Runtime regional endpoints.
KNOWN_WATSONX_HOSTS = (
    "us-south.ml.cloud.ibm.com",   # Dallas
    "eu-de.ml.cloud.ibm.com",      # Frankfurt
    "eu-gb.ml.cloud.ibm.com",      # London
    "jp-tok.ml.cloud.ibm.com",     # Tokyo
    "au-syd.ml.cloud.ibm.com",     # Sydney
    "ca-tor.ml.cloud.ibm.com",     # Toronto
)

#: Verified against the live IBM catalog on 2026-07-06 (docs/IBM_SETUP.md §7).
DEFAULT_MODEL_ID = "ibm/granite-4-h-small"
DEFAULT_EMBEDDING_MODEL_ID = "ibm/granite-embedding-278m-multilingual"

_VALID_APP_MODES = ("live", "demo", "auto")
_VALID_EMBEDDING_PROVIDERS = ("watsonx", "local", "auto")

#: Sentinel meaning "FLASK_SECRET_KEY was never configured". Ships in .env.example.
_DEFAULT_SECRET = "change-me-generate-a-random-value"
#: Auto-generated key lives here so it survives restarts and is shared by workers.
_SECRET_KEY_FILENAME = "secret_key"


def _env(name: str, default: str = "") -> str:
    """Read an env var, stripping whitespace and accidental wrapping quotes."""
    raw = os.environ.get(name, default) or ""
    return raw.strip().strip('"').strip("'").strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    try:
        return int(value) if value else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name).lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


def _read_key_file(path: Path) -> str:
    """Contents of an existing key file, or ``""`` when absent/blank."""
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _resolve_secret_key(raw: str, instance_dir: Path, *,
                        allow_generation: bool) -> tuple[str, str]:
    """Resolve the Flask secret key; generate a persistent one when unset.

    Returns ``(key, source)`` where source is one of:

    * ``"environment"`` — read from ``FLASK_SECRET_KEY`` (the production path)
    * ``"generated"``   — read from or written to ``instance/secret_key``
    * ``"ephemeral"``   — random, process-local, because the instance directory
      is not writable; sessions do not survive a restart
    * ``"placeholder"`` — unresolved (generation disabled); a fatal config error

    Why generate at all: ``FLASK_DEBUG`` now defaults to off, and the old rule
    made a placeholder key fatal whenever debug was off. Applying that rule
    unchanged would break ``git clone && python run.py``, which is a documented
    and genuinely useful property of this project. Persisting a real random key
    keeps zero-setup working *and* guarantees no deployment ever runs on the
    publicly-known placeholder — safe in both directions instead of trading one
    failure for another.
    """
    if raw and raw != _DEFAULT_SECRET:
        return raw, "environment"
    if not allow_generation:
        return _DEFAULT_SECRET, "placeholder"

    key_path = instance_dir / _SECRET_KEY_FILENAME
    try:
        existing = _read_key_file(key_path)
        if existing:
            return existing, "generated"

        instance_dir.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_hex(32)
        try:
            # Exclusive create, not write_text: when several workers boot at the
            # same moment exactly one wins, and the losers adopt its key. Plain
            # writes would leave each worker signing sessions with a different
            # key, so cookies issued by one would be rejected by the others.
            with key_path.open("x", encoding="utf-8") as handle:
                handle.write(generated)
        except FileExistsError:
            return _read_key_file(key_path) or generated, "generated"
        try:
            key_path.chmod(0o600)
        except OSError:  # pragma: no cover — filesystems without POSIX modes
            pass
        return generated, "generated"
    except OSError:
        # Read-only instance directory (some container images). Boot anyway with
        # a process-local key; validate_settings warns about the consequence.
        return secrets.token_hex(32), "ephemeral"


@dataclass(frozen=True)
class RAGSettings:
    """Tunable retrieval parameters (defaults documented in ARCHITECTURE.md §4).

    * ``chunk_size`` 800 chars ≈ 200 tokens: large enough to hold a complete
      guideline statement, comfortably under the embedding model's 512-token
      input window (granite-embedding-278m).
    * ``chunk_overlap`` 120 chars (15%): preserves sentences straddling a
      boundary without materially inflating index size.
    * ``top_k`` 5: enough passage diversity for grounding while keeping the
      prompt small — Lite-plan tokens are the scarce resource.
    * ``similarity_threshold`` 0.35 cosine: below this the Knowledge Agent
      answers from general knowledge with an explicit label instead of
      pretending weak matches are evidence (ARCHITECTURE.md §4.4).
    """

    chunk_size: int
    chunk_overlap: int
    top_k: int
    #: None = let the embedding provider decide. Thresholds are a property of
    #: a vector space, not a global constant: a dense semantic embedding and a
    #: sparse lexical one score on completely different scales, so a single
    #: value silently breaks whichever provider it was not chosen for.
    similarity_threshold: float | None


@dataclass(frozen=True)
class WatsonxSettings:
    """Everything needed to talk to IBM watsonx.ai."""

    api_key: str
    url: str
    project_id: str
    space_id: str
    model_id: str
    embedding_model_id: str

    @property
    def has_credentials(self) -> bool:
        """True when the minimum viable credential set is present."""
        return bool(self.api_key and self.project_id and self.url)

    @property
    def host_is_known_region(self) -> bool:
        return any(host in self.url for host in KNOWN_WATSONX_HOSTS)


@dataclass(frozen=True)
class Settings:
    """Immutable application settings resolved from the environment."""

    watsonx: WatsonxSettings
    rag: RAGSettings
    app_mode: str              # requested: live | demo | auto
    embeddings_provider: str   # watsonx | local | auto
    secret_key: str
    #: where secret_key came from: environment | generated | ephemeral | placeholder
    secret_key_source: str
    debug: bool
    #: how long a signed-in session stays valid without re-authenticating
    session_days: int
    #: reverse proxies in front of the app whose X-Forwarded-* may be trusted
    trusted_proxy_hops: int
    max_upload_mb: int
    log_level: str
    database_uri: str
    base_dir: Path
    instance_dir: Path
    upload_dir: Path
    chroma_dir: Path
    knowledge_base_dir: Path


def load_settings(dotenv_path: Path | None = None, *, ensure_dirs: bool = True) -> Settings:
    """Load settings from ``.env`` + environment.

    :param dotenv_path: explicit ``.env`` location (defaults to repo root)
    :param ensure_dirs: create runtime directories (instance/, uploads/, chroma/)
    """
    load_dotenv(dotenv_path or (BASE_DIR / ".env"), override=False)

    watsonx = WatsonxSettings(
        api_key=_env("WATSONX_APIKEY"),
        url=_env("WATSONX_URL", "https://us-south.ml.cloud.ibm.com").rstrip("/"),
        project_id=_env("WATSONX_PROJECT_ID"),
        space_id=_env("WATSONX_SPACE_ID"),
        model_id=_env("WATSONX_MODEL_ID", DEFAULT_MODEL_ID),
        embedding_model_id=_env("WATSONX_EMBEDDING_MODEL_ID", DEFAULT_EMBEDDING_MODEL_ID),
    )

    rag = RAGSettings(
        chunk_size=_env_int("RAG_CHUNK_SIZE", 800),
        chunk_overlap=_env_int("RAG_CHUNK_OVERLAP", 120),
        top_k=_env_int("RAG_TOP_K", 5),
        similarity_threshold=(float(_env("RAG_SIMILARITY_THRESHOLD"))
                              if _env("RAG_SIMILARITY_THRESHOLD") else None),
    )

    instance_dir = (Path(_env("NUTRIMIND_INSTANCE_DIR"))
                    if _env("NUTRIMIND_INSTANCE_DIR") else INSTANCE_DIR)
    secret_key, secret_key_source = _resolve_secret_key(
        _env("FLASK_SECRET_KEY", _DEFAULT_SECRET), instance_dir,
        allow_generation=ensure_dirs)
    settings = Settings(
        watsonx=watsonx,
        rag=rag,
        app_mode=_env("APP_MODE", "auto").lower(),
        embeddings_provider=_env("EMBEDDINGS_PROVIDER", "auto").lower(),
        secret_key=secret_key,
        secret_key_source=secret_key_source,
        # Off by default: production must never expose the interactive debugger,
        # and defaults are what get used when a deploy is rushed. Opt in locally
        # with FLASK_DEBUG=1.
        debug=_env_bool("FLASK_DEBUG", False),
        # Long enough that people are not re-authenticating constantly, short
        # enough that a session stolen from a shared machine expires.
        session_days=_env_int("SESSION_DAYS", 14),
        # 0 = not behind a proxy. Trusting the header when nothing strips it
        # lets any client claim any address, so this must be opted into.
        trusted_proxy_hops=_env_int("TRUSTED_PROXY_HOPS", 0),
        max_upload_mb=_env_int("MAX_UPLOAD_MB", 15),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        database_uri=_env(
            "DATABASE_URI", f"sqlite:///{(instance_dir / 'nutrimind.db').as_posix()}"
        ),
        base_dir=BASE_DIR,
        instance_dir=instance_dir,
        upload_dir=instance_dir / "uploads",
        chroma_dir=Path(_env("CHROMA_DIR")) if _env("CHROMA_DIR") else instance_dir / "chroma",
        knowledge_base_dir=BASE_DIR / "knowledge_base",
    )

    if ensure_dirs:
        for directory in (settings.instance_dir, settings.upload_dir, settings.chroma_dir):
            directory.mkdir(parents=True, exist_ok=True)

    return settings


def validate_settings(settings: Settings) -> tuple[list[str], list[str]]:
    """Validate settings and return ``(errors, warnings)``.

    Errors make the app unusable and should stop startup; warnings are
    surfaced but non-fatal. Both use plain language with a concrete fix.
    """
    errors: list[str] = []
    warnings: list[str] = []
    wx = settings.watsonx

    if settings.app_mode not in _VALID_APP_MODES:
        errors.append(
            f"APP_MODE must be one of {_VALID_APP_MODES}, got '{settings.app_mode}'."
        )
    if settings.embeddings_provider not in _VALID_EMBEDDING_PROVIDERS:
        errors.append(
            "EMBEDDINGS_PROVIDER must be one of "
            f"{_VALID_EMBEDDING_PROVIDERS}, got '{settings.embeddings_provider}'."
        )

    if settings.app_mode == "live" and not wx.has_credentials:
        missing = [
            name
            for name, value in (
                ("WATSONX_APIKEY", wx.api_key),
                ("WATSONX_PROJECT_ID", wx.project_id),
                ("WATSONX_URL", wx.url),
            )
            if not value
        ]
        errors.append(
            "APP_MODE=live requires IBM credentials, but these are missing: "
            f"{', '.join(missing)}. Fill them in .env (see docs/IBM_SETUP.md) "
            "or set APP_MODE=auto to fall back to demo mode."
        )

    if wx.has_credentials:
        if not wx.url.startswith("https://"):
            errors.append(f"WATSONX_URL must start with https:// — got '{wx.url}'.")
        elif not wx.host_is_known_region:
            warnings.append(
                f"WATSONX_URL host '{wx.url}' is not a recognized watsonx.ai "
                "regional endpoint. Double-check the region table in docs/IBM_SETUP.md."
            )
        if not wx.model_id:
            errors.append("WATSONX_MODEL_ID must not be empty.")
        if not wx.embedding_model_id:
            errors.append("WATSONX_EMBEDDING_MODEL_ID must not be empty.")
    elif settings.app_mode == "auto":
        warnings.append(
            "IBM credentials are not configured — the app will run in DEMO mode. "
            "Follow docs/IBM_SETUP.md to enable live watsonx.ai responses."
        )

    if not (0 <= settings.trusted_proxy_hops <= 5):
        errors.append("TRUSTED_PROXY_HOPS must be 0-5, got "
                      f"{settings.trusted_proxy_hops}.")

    if not (1 <= settings.session_days <= 365):
        errors.append(f"SESSION_DAYS must be 1-365, got {settings.session_days}.")

    if not (1 <= settings.max_upload_mb <= 100):
        errors.append(f"MAX_UPLOAD_MB must be 1-100, got {settings.max_upload_mb}.")

    rag = settings.rag
    if not (100 <= rag.chunk_size <= 4000):
        errors.append(f"RAG_CHUNK_SIZE must be 100-4000, got {rag.chunk_size}.")
    if not (0 <= rag.chunk_overlap < rag.chunk_size):
        errors.append("RAG_CHUNK_OVERLAP must be >= 0 and smaller than RAG_CHUNK_SIZE.")
    if not (1 <= rag.top_k <= 20):
        errors.append(f"RAG_TOP_K must be 1-20, got {rag.top_k}.")
    if rag.similarity_threshold is not None and not (0.0 <= rag.similarity_threshold <= 1.0):
        errors.append(f"RAG_SIMILARITY_THRESHOLD must be 0-1, got {rag.similarity_threshold}.")

    if settings.secret_key_source == "placeholder":
        errors.append(
            "FLASK_SECRET_KEY is unset and no key could be generated. Set it "
            'explicitly: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    elif settings.secret_key_source == "generated":
        warnings.append(
            "FLASK_SECRET_KEY was not set, so a random key was generated and stored "
            f"in {settings.instance_dir / _SECRET_KEY_FILENAME}. Sessions survive "
            "restarts, but set FLASK_SECRET_KEY explicitly in production so the key "
            "is managed alongside your other secrets and can be rotated."
        )
    elif settings.secret_key_source == "ephemeral":
        warnings.append(
            "FLASK_SECRET_KEY is unset and the instance directory is not writable, "
            "so a process-local key is in use. Every restart invalidates all "
            "sessions and multiple workers will not share sessions. Set "
            "FLASK_SECRET_KEY explicitly."
        )

    return errors, warnings


def resolve_app_mode(settings: Settings) -> str:
    """Resolve the *effective* mode: 'auto' becomes live only with credentials."""
    if settings.app_mode == "demo":
        return "demo"
    if settings.app_mode == "live":
        return "live"
    return "live" if settings.watsonx.has_credentials else "demo"
