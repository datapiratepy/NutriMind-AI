"""The demo backend, and the promise it must not break.

A reply labelled *grounded* carries citations to the user's own documents. The
text beside those citations therefore has to come from those documents. This
backend cannot compose prose, so it quotes them — but quoting is not the point
of these tests; **not answering from somewhere else** is.

The bug they exist for: the keyword table matched against the whole prompt,
including the retrieved passages. A Thai recipe containing the words "banana"
and "sugar" triggered the diabetes script, and the answer was returned with three
real citations to a cookbook. Retrieval was correct, the citations were correct,
and the text had nothing to do with either — which is exactly the invented
evidence the project promises never to produce.
"""

from __future__ import annotations

import pytest

from nutrimind.services.llm.demo_client import DemoClient, parse_passages

#: Shaped exactly like RetrievalResult.context_text(), including the words that
#: used to hijack the keyword table.
PASSAGES = (
    "[1] (from ThaiRecipes.pdf, page 4)\n"
    "Pad Thai Goong Sod: soak rice noodles, then stir fry with tamarind, "
    "palm sugar and fish sauce until the sauce coats every strand.\n\n"
    "[2] (from ThaiRecipes.pdf, page 9)\n"
    "Green curry: simmer coconut milk with curry paste, bamboo shoots and "
    "thai basil. Serve with jasmine rice."
)

KNOWLEDGE_PROMPT = (
    "No user profile is available.\n\n"
    f"PASSAGES:\n{PASSAGES}\n\n"
    "QUESTION: What about Pad Thai?\n\n"
    "Answer using the passages, citing them as [1], [2] where used."
)

ADVISOR_PROMPT = (
    "User profile: 21-year-old male.\n\n"
    f"PASSAGES:\n{PASSAGES}\n\n"
    "USER REQUEST: what should I cook tonight?"
)


def _reply(prompt: str) -> str:
    return DemoClient().chat([{"role": "user", "content": prompt}]).text


# -- the regression -----------------------------------------------------------

@pytest.mark.parametrize("prompt", [KNOWLEDGE_PROMPT, ADVISOR_PROMPT],
                         ids=["knowledge_agent", "health_advisor"])
def test_a_prompt_with_passages_is_answered_from_them(prompt):
    """Both agents that retrieve build their prompt slightly differently, so both
    shapes are asserted rather than one being assumed to imply the other."""
    reply = _reply(prompt)

    assert "tamarind" in reply or "coconut milk" in reply, (
        "the answer does not contain anything from the retrieved passages")
    assert "ThaiRecipes.pdf" in reply, "the answer does not name its source"


@pytest.mark.parametrize("prompt", [KNOWLEDGE_PROMPT, ADVISOR_PROMPT],
                         ids=["knowledge_agent", "health_advisor"])
def test_passages_are_not_hijacked_by_the_keyword_table(prompt):
    """The exact failure: "sugar" inside a recipe selected the diabetes script.

    The scripted answers are fine for ungrounded questions; presenting one as
    grounded, with citations to a document it was not derived from, is not.
    """
    reply = _reply(prompt).lower()

    for stray in ("diabetes", "insulin", "glucose monitoring", "body mass index"):
        assert stray not in reply, (
            f"a grounded answer contained {stray!r} — the keyword table was "
            "matched against the retrieved passages again")


def test_the_answer_cites_the_numbers_the_agent_supplied():
    """The UI renders the citation list from retrieval metadata, so the markers
    in the text have to use the same numbering or they point at nothing."""
    reply = _reply(KNOWLEDGE_PROMPT)
    assert "[1]" in reply


def test_a_grounded_reply_is_not_labelled_a_sample_answer():
    """The standard footer says "this is a built-in sample answer", which would
    be untrue printed under text quoted from the user's own PDF."""
    grounded = _reply(KNOWLEDGE_PROMPT)
    ordinary = _reply("how much protein is in paneer?")

    assert "built-in sample answer" in ordinary
    assert "built-in sample answer" not in grounded
    assert "really retrieved from your documents" in grounded


# -- the scripted path must still work ----------------------------------------

def test_a_prompt_without_passages_still_uses_the_scripted_answers():
    """Demo mode has to stay useful for questions with no documents behind them."""
    assert "paneer" in _reply("how much protein is in paneer?").lower()


def test_agent_markers_still_route_correctly():
    """The planner and analyzer identify themselves with distinctive phrases; the
    passage check runs first, and must not shadow them."""
    plan = _reply("Return ONLY valid JSON per your output format.")
    assert plan.lstrip().startswith("{"), "the meal planner's JSON marker stopped working"

    assessment = _reply("Write one short paragraph assessing the meal quality.")
    assert "suggestion" in assessment.lower() or "meal" in assessment.lower()


# -- parsing ------------------------------------------------------------------

def test_passages_are_parsed_with_their_source_and_page():
    parsed = parse_passages(KNOWLEDGE_PROMPT)
    assert [(n, f, p) for n, f, p, _ in parsed] == [
        (1, "ThaiRecipes.pdf", 4), (2, "ThaiRecipes.pdf", 9)]
    assert "tamarind" in parsed[0][3]


def test_the_question_is_not_parsed_as_a_passage():
    """The question follows the passages in the same prompt; swallowing it would
    quote the user's own words back as if they were evidence."""
    parsed = parse_passages(KNOWLEDGE_PROMPT)
    assert not any("QUESTION" in text for _, _, _, text in parsed)
    assert not any("citing them as" in text for _, _, _, text in parsed)


@pytest.mark.parametrize("prompt", [
    "how much protein is in paneer?",
    "",
    "PASSAGES:\n",                       # header with nothing after it
    "Talk about PASSAGES: in prose.",    # the word, not the block
])
def test_prompts_without_real_passages_parse_to_nothing(prompt):
    assert parse_passages(prompt) == []
