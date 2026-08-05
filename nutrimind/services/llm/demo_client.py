"""Deterministic demo backend — runs the full application with zero credentials.

Purpose (ARCHITECTURE.md §6):
  * evaluators can launch NutriMind without an IBM account,
  * unit/integration tests never need API keys,
  * live demos survive quota exhaustion or network failure.

Responses are curated and deterministic; streaming is simulated. Embeddings
are stable pseudo-vectors derived from a SHA-256 of the text, so the whole
RAG pipeline (index → search) stays functional — mechanically identical to
live mode. Every reply is transparently labeled.

**When the prompt carries retrieved passages, the answer is built from them.**
This backend cannot compose prose, but it must never answer a grounded question
from an unrelated script: doing so produced replies about bananas and diabetes,
labelled "Grounded" and carrying three real citations to a Thai cookbook, because
the keyword table matched the words "banana" and "sugar" *inside the retrieved
passages*. Citations that do not support the text they accompany are precisely
the invented evidence this project promises never to produce, so passage handling
is checked before the scripted table and quotes the sources instead.
"""

from __future__ import annotations

import hashlib
import random
import re
import time
from typing import Iterator, Sequence

from nutrimind.services.llm.base import ChatResult, LLMClient, Message

_DEMO_MODEL_ID = "demo/deterministic"
_EMBED_DIM = 384
_STREAM_DELAY_S = 0.015

_FOOTER = (
    "\n\n_(Demo mode: this is a built-in sample answer. Configure IBM watsonx.ai "
    "credentials — see docs/IBM_SETUP.md — for live Granite responses.)_"
)

_GROUNDED_FOOTER = (
    "\n\n_(Demo mode: the passages above were really retrieved from your "
    "documents and the citations are real, but they are quoted rather than "
    "summarised — composing an answer from them needs a language model. "
    "Configure IBM watsonx.ai for that; see docs/IBM_SETUP.md.)_"
)

#: Matches one entry of ``RetrievalResult.context_text()``:
#:
#:     <<<PASSAGE 1 source="guide.pdf" page="4"
#:     passage text
#:     PASSAGE>>>
#:
#: The fence replaced a bare ``[1] (from guide.pdf, page 4)`` heading when
#: retrieved passages were delimited as untrusted data. This parser has to track
#: that format: if it silently stops matching, ``parse_passages`` returns nothing,
#: the demo backend falls through to its scripted keyword table, and grounded
#: answers go back to being unrelated canned text carrying real citations —
#: the exact defect fixed in Milestone 4.
_PASSAGE_ENTRY = re.compile(
    r'<<<PASSAGE (\d+) source="(.*?)" page="(\d+)"\n(.*?)\nPASSAGE>>>',
    re.DOTALL)

#: The agents append the user's question after the passages under one of these.
_QUESTION_MARKERS = ("\n\nQUESTION:", "\n\nUSER REQUEST:")

#: Longest quote taken from any single passage, so one large chunk cannot
#: crowd out the others.
_QUOTE_CHARS = 320

#: (keywords, response) pairs checked in order against the last user message.
#: Marker entries FIRST: they match distinctive agent-prompt phrases so the
#: agent workflows (plan JSON, meal assessment) stay fully demo-compatible.
_SCRIPTED: list[tuple[tuple[str, ...], str]] = [
    (
        ("return only valid json",),
        """{"title": "Balanced vegetarian day (demo)",
 "meals": [
  {"name": "Breakfast", "items": [{"food": "vegetable poha", "portion": "1 plate"}, {"food": "curd", "portion": "1 katori"}], "calories": 420, "protein_g": 14},
  {"name": "Lunch", "items": [{"food": "roti", "portion": "2"}, {"food": "dal tadka", "portion": "1 katori"}, {"food": "mixed vegetable sabzi", "portion": "1 katori"}], "calories": 610, "protein_g": 22},
  {"name": "Snack", "items": [{"food": "apple", "portion": "1"}, {"food": "roasted chana", "portion": "1 handful"}], "calories": 210, "protein_g": 8},
  {"name": "Dinner", "items": [{"food": "paneer bhurji", "portion": "1 katori"}, {"food": "roti", "portion": "1"}, {"food": "salad", "portion": "1 plate"}], "calories": 560, "protein_g": 26}
 ],
 "hydration": "Spread your water target across the day - a glass with each meal helps.",
 "notes": "Vegetarian, allergy-aware demo plan with protein at every meal. Configure IBM watsonx.ai for fully personalized plans."}""",
    ),
    (
        ("write one short paragraph assessing",),
        "This meal offers a reasonable balance for its size: protein is solid "
        "relative to the calories, and the fiber content supports satiety and "
        "digestion.\n\n- Add a vitamin-C source (citrus, amla) to boost iron "
        "absorption.\n- A glass of buttermilk would add protein and probiotics.\n"
        "- Keep an eye on refined-carb portions later in the day.",
    ),
    (
        ("hypertension", "blood pressure"),
        "For blood-pressure management, the evidence consistently supports a "
        "DASH-style pattern: plenty of vegetables, fruit and low-fat dairy, "
        "less salt (under ~5 g/day), fewer processed foods, and adequate "
        "potassium from foods like banana, coconut water and leafy greens. "
        "Please review changes with your clinician.",
    ),
    (
        ("muscle", "bulking"),
        "For muscle building, prioritize 1.6-2 g protein per kg body weight "
        "spread over 4-5 feedings, a modest calorie surplus (~300-400 kcal), "
        "and resistance training. Vegetarian options: paneer, dal, soya "
        "chunks, curd, peanuts and milk.",
    ),
    (
        ("meal plan", "diet plan"),
        "Here is a sample 1,800 kcal vegetarian day plan:\n\n"
        "**Breakfast** — Vegetable poha with peanuts (~350 kcal, 10 g protein)\n"
        "**Lunch** — 2 rotis, dal tadka, mixed-vegetable sabzi, curd (~550 kcal, 22 g protein)\n"
        "**Snack** — Apple with a handful of roasted chana (~200 kcal, 7 g protein)\n"
        "**Dinner** — Paneer bhurji with 1 roti and salad (~500 kcal, 24 g protein)\n"
        "**Hydration** — 8-10 glasses of water across the day.",
    ),
    (
        ("today i ate", "i ate", "analyze my"),
        "Quick estimate for the meal you described:\n\n"
        "Calories ≈ 640 kcal · Protein ≈ 24 g · Fat ≈ 18 g · Carbs ≈ 92 g · Fiber ≈ 9 g\n\n"
        "Overall a balanced plate. Consider adding a source of vitamin C "
        "(citrus or amla) to improve iron absorption from the dal.",
    ),
    (
        ("protein", "paneer"),
        "Paneer provides roughly **18 g of protein per 100 g**, along with calcium "
        "and vitamin B12, making it one of the strongest vegetarian protein sources "
        "in Indian cuisine. Pair it with whole grains for a complete amino-acid profile.",
    ),
    (
        ("diabet", "banana", "sugar"),
        "People with diabetes can usually include fruit in moderation. A small "
        "banana (~15 g carbs) is generally acceptable when paired with protein or "
        "fat to slow glucose absorption — individual responses vary, so glucose "
        "monitoring and a clinician's guidance matter most.",
    ),
    (
        ("bmi",),
        "BMI = weight (kg) ÷ height² (m²). For example, 70 kg at 1.75 m → "
        "70 / 3.06 ≈ **22.9**, which falls in the healthy range (18.5-24.9). "
        "Use the BMI calculator on your profile page for a tracked, personalized result.",
    ),
    (
        ("hello", "hi ", "hey", "namaste"),
        "Hello! I'm NutriMind. Ask me about nutrition, request a meal plan, or "
        "describe what you ate today and I'll analyze it.",
    ),
]

_DEFAULT_RESPONSE = (
    "Balanced nutrition rests on a few reliable principles: plenty of vegetables "
    "and whole grains, adequate protein at every meal, healthy fats in moderation, "
    "and consistent hydration. Ask me something specific — a meal plan, a food "
    "analysis, or a nutrition question — and I'll go deeper."
)


def parse_passages(prompt: str) -> list[tuple[int, str, int, str]]:
    """Pull ``(number, filename, page, text)`` out of a prompt's PASSAGES block.

    Returns ``[]`` when the prompt carries no passages, which is how the caller
    distinguishes a grounded turn from an ordinary one.
    """
    marker = "PASSAGES:\n"
    start = prompt.find(marker)
    if start == -1:
        return []

    region = prompt[start + len(marker):]
    for question_marker in _QUESTION_MARKERS:
        cut = region.find(question_marker)
        if cut != -1:
            region = region[:cut]

    return [
        (int(number), filename, int(page), " ".join(text.split()))
        for number, filename, page, text in _PASSAGE_ENTRY.findall(region)
        if text.strip()
    ]


def _answer_from_passages(passages: list[tuple[int, str, int, str]]) -> str:
    """Compose a grounded answer by quoting the retrieved passages.

    Extractive on purpose. A real model would summarise these; this backend can
    only quote them, and quoting is the honest option — the alternative that was
    here before answered from an unrelated script while the UI attached these
    same citations to it.

    The ``[n]`` markers match the numbering the agent put in the prompt, so the
    citation list the UI renders lines up with the text.
    """
    lines = ["Here is what your own documents say:", ""]
    for number, filename, page, text in passages[:3]:
        quote = text if len(text) <= _QUOTE_CHARS else text[:_QUOTE_CHARS].rstrip() + "…"
        lines.append(f"> {quote}")
        lines.append(f"— {filename}, page {page} [{number}]")
        lines.append("")
    return "\n".join(lines).rstrip()


def _pick_response(messages: Sequence[Message]) -> str:
    raw = next(
        (m.get("content", "") for m in reversed(list(messages)) if m.get("role") == "user"),
        "",
    )

    # Checked before the keyword table, and deliberately so. The table matches
    # against the whole prompt, which includes the retrieved passages — a Thai
    # recipe containing "sugar" and "banana" therefore triggered the diabetes
    # script, and the answer was presented as grounded with real citations to a
    # cookbook. Whenever passages are present they are the only legitimate source
    # for the answer.
    passages = parse_passages(raw)
    if passages:
        return _answer_from_passages(passages)

    lowered = raw.lower()
    for keywords, response in _SCRIPTED:
        if any(keyword in lowered for keyword in keywords):
            return response
    return _DEFAULT_RESPONSE


def _reply(messages: Sequence[Message]) -> str:
    """Full demo reply, with the footer that matches how it was produced.

    A grounded reply gets a different note: saying "this is a built-in sample
    answer" under text quoted from the user's own PDF would be untrue, and the
    distinction is exactly what was missing when canned text shipped with real
    citations attached.
    """
    body = _pick_response(messages)
    grounded = any(parse_passages(m.get("content", ""))
                   for m in messages if m.get("role") == "user")
    return body + (_GROUNDED_FOOTER if grounded else _FOOTER)


class DemoClient(LLMClient):
    """Zero-credential, deterministic drop-in for :class:`WatsonxClient`."""

    mode = "demo"

    def __init__(self, *, reason: str = "explicitly enabled") -> None:
        self.detail = reason

    def chat(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int = 512,      # noqa: ARG002 — accepted for interface parity
        temperature: float = 0.2,   # noqa: ARG002
    ) -> ChatResult:
        return ChatResult(text=_reply(messages), model_id=_DEMO_MODEL_ID)

    def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int = 512,      # noqa: ARG002
        temperature: float = 0.2,   # noqa: ARG002
    ) -> Iterator[str]:
        for word in self.chat(messages).text.split(" "):
            yield word + " "
            time.sleep(_STREAM_DELAY_S)  # simulate token latency for realistic UX

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._pseudo_embedding(text) for text in texts]

    def ping(self) -> None:  # demo backend is always reachable
        return None

    @staticmethod
    def _pseudo_embedding(text: str) -> list[float]:
        """Stable unit vector derived from the text's SHA-256 digest."""
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        rng = random.Random(seed)
        vector = [rng.uniform(-1.0, 1.0) for _ in range(_EMBED_DIM)]
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]
