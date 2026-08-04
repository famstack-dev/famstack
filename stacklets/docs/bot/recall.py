"""Recall — turn a chat message into a search-ready query.

The archivist routes most chat messages straight into Paperless +
memory-vault search as a literal regex. That works for keyword recall
("Globex", "Radlager") but fails on natural-language questions:
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

Chat infers the trigger; the CLI does not. `stack memory search`
reaches the same rewrite behind an explicit `--nl`, and that
difference is deliberate on both sides. Here, every message is
already being read for a family, and one character is a rule a
family can learn. There, the default query language is a regex, in
which `?` is a quantifier, so inferring from it would turn `Zelt?`
into a surprise model call. Do not "fix" either one into the other.
"""

from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger

from pipeline import Classifier
# Memory owns the vault, so it owns the rewrite and the regex the
# walker reads. This module keeps the chat-side half: when to spend an
# LLM call, and how Paperless wants the same keywords rendered. The
# import above puts `stacklets/` on sys.path, which is what makes the
# sibling stacklet reachable here.
from memory.lib import keywords_to_regex


async def resolve_search_query(
    query: str,
    *,
    classifier: Optional[Classifier],
    ontology_section: str,
    language: str,
) -> Tuple[str, str, list[str]]:
    """Resolve a chat message into the strings the two search backends need.

    Returns `(memory_regex, paperless_query, keywords_used)`:

      * `memory_regex` is the Python regex the memory walker feeds
        to `re.search`. Always non-empty.
      * `paperless_query` is the Whoosh-compatible string the bot
        passes to `PaperlessAPI.search`. The API client wildcards
        bare tokens itself (`_to_search_query`), so we only have to
        get the *operators* right here — bare tokens joined with ` OR `
        for question mode, the raw input for literal mode.
      * `keywords_used` is the list the LLM produced (empty when
        rewrite didn't run or failed). The bot uses this to surface a
        "Searched for: ..." header so the family can tell when a bad
        rewrite hid results.

    The two backends need different query languages: the memory walker
    runs Python regex (so `|` is the alternation operator and
    `re.escape` is mandatory), while Paperless runs Whoosh (so `|` is
    a literal character and `OR` is the alternation operator). Before
    splitting the return shape, a single regex was being shipped to
    both -- which is why `fish|price|cost` matched nothing in Paperless
    even when the memory side matched it correctly.

    Question mode only runs when *all* of the following hold:

      1. The message ends with `?` (after rstrip).
      2. A `Classifier` was passed (None means LLM is unavailable;
         the archivist boots without a classifier when the AI
         endpoint isn't configured).

    Otherwise the function short-circuits to the literal path. This
    is deliberately strict -- surprise LLM calls are exactly the
    behaviour we want to avoid.
    """
    if not _looks_like_question(query):
        logger.debug("[recall] literal mode: no trailing '?'")
        return query, query, []
    if classifier is None:
        logger.info("[recall] literal mode: no classifier configured")
        return query, query, []

    logger.info("[recall] question mode: rewriting {!r}", query[:80])
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
        return query, query, []

    if not keywords:
        logger.info("[recall] rewrite returned no keywords; falling back to literal")
        return query, query, []
    logger.info("[recall] keywords: {}", keywords)

    # Memory side: regex alternation, escaped per keyword. Memory
    # renders this because memory is what has to read it back.
    memory_regex = keywords_to_regex(keywords)
    # Paperless side: Whoosh OR alternation, bare tokens. The
    # PaperlessAPI wraps this in `_to_search_query` which adds the
    # prefix wildcard on each bare term -- so "fish OR price" becomes
    # "fish* OR price*" downstream.
    paperless_query = " OR ".join(keywords)
    return memory_regex, paperless_query, keywords


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
