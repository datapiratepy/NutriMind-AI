"""Prompt loader — system prompts are versioned .txt files, not inline strings.

Keeping prompts as files makes prompt engineering reviewable in git diffs and
lets non-developers read them. Loaded once per process (lru_cache).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from nutrimind.exceptions import ConfigurationError

_PROMPT_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Return the system prompt text for ``name`` (without extension)."""
    path = _PROMPT_DIR / f"{name}.txt"
    if not path.exists():
        raise ConfigurationError(f"System prompt '{name}' is missing.",
                                 hint=f"Expected file: {path}")
    return path.read_text(encoding="utf-8").strip()
