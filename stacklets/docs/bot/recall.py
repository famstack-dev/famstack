"""Recall — turn a chat message into a search-ready query.

The archivist routes most chat messages straight into Paperless +
memory-vault search as a literal regex. That works for keyword recall
("Allianz", "Radlager") but fails on natural-language questions:
"When did Bart get vaccinated?" doesn't literally appear in any
document, so the regex walker returns nothing.

This module is the one place that decides whether a message needs
LLM help. The rule is intentionally narrow so the family learns
exactly one trigger:

    - Message ends with `?`  →  question mode (LLM keyword extraction)
    - Anything else          →  keyword mode (literal regex, fast path)

Question mode runs the user's message through the classifier model
with an ontology-primed prompt and gets back 2-4 keywords that would
literally appear in a matching document. Those keywords get
OR-alternated into a single regex; everything downstream (vault walk,
Paperless search) sees one regex string and stays oblivious to which
mode produced it.

Everything is best-effort: an unreachable LLM, a malformed response,
or an empty keyword list falls back to the literal query. Recall is
a quality-of-life feature, never a gate.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from loguru import logger

from pipeline import Classifier


async def resolve_search_query(
    query: str,
    *,
    classifier: Optional[Classifier],
    ontology_section: str,
    language: str,
) -> Tuple[str, list[str]]:
    """Resolve a chat message into the regex the search walkers should run.

    Returns `(search_regex, keywords_used)`:

      * `search_regex` is the string passed to `search_memory` /
        `_paperless.search`. Always non-empty; equals the input
        message in the literal-query path.
      * `keywords_used` is the list the LLM produced (empty when
        rewrite didn't run or failed). The bot uses this to surface a
        "Searched for: ..." header so the family can tell when a
        bad rewrite hid results.

    Question mode only runs when *all* of the following hold:

      1. The message ends with `?` (after rstrip).
      2. A `Classifier` was passed (None means LLM is unavailable;
         the archivist boots without a classifier when the AI
         endpoint isn't configured).

    Otherwise the function short-circuits to the literal path. This
    is deliberately strict — surprise LLM calls are exactly the
    behaviour we want to avoid.
    """
    if not _looks_like_question(query) or classifier is None:
        return query, []

    try:
        keywords = await classifier.rewrite_query(
            query, ontology_section, language,
        )
    except Exception as e:
        # Classifier already swallows the documented LLM errors and
        # returns []; this catch-all is a last-resort guard so a
        # genuine programming bug in the prompt builders never
        # blocks a search. We log + fall back to literal.
        logger.warning("[recall] rewrite_query raised: {}", e)
        return query, []

    if not keywords:
        return query, []

    # Each keyword goes through re.escape so a chatty LLM that returns
    # "C++" or "Lisa's" can't blow up the alternation regex.
    pattern = "|".join(re.escape(k) for k in keywords)
    return pattern, keywords


# ── Triggers ────────────────────────────────────────────────────────────

def _looks_like_question(message: str) -> bool:
    """The question-mode trigger: `?` at the end after trailing whitespace.

    Strict by design. We considered "starts with W-word" / "is longer
    than N tokens" / NLP heuristics; all of them surprised users in
    practice. A single character anchor is something the family can
    learn in one sentence: "End with a question mark for a smart
    answer; otherwise it's a fast keyword search."
    """
    return message.rstrip().endswith("?")
