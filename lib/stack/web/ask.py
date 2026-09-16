"""Answering a plain question from search snippets.

Deliberately the smallest thing that works: one search, the top handful
of snippets, one model call. No second query, no fetching the pages, no
tool loop deciding what to read next.

That is a judgement about what local models can actually do, not a
shortcut. The best measured browser agents complete about a third of
ordinary web tasks, and the gap between a frontier model and a small
one widens sharply on multi-step tool use. A single call over snippets
a search engine already ranked has none of those failure modes: it
either finds the answer in the text it was handed or it does not, and
"it does not" is a sentence the model can say.

It is also what keeps the command cheap enough to sit in a chat round
trip, and it keeps page content out of the agent's context entirely --
the agent gets an answer and some links, not a wall of HTML.

This module is the pure half: snippets in, a prompt out, and the source
list that goes under the answer. No network, no model, no config, so it
is testable against a recorded search response.

Stdlib only: the host CLI runs this without a virtualenv.
"""

from __future__ import annotations

from dataclasses import dataclass

# Enough context to answer from, small enough that a local model reads
# it in a couple of seconds. Eight snippets of ~400 characters is
# roughly 800 tokens of evidence.
DEFAULT_SOURCES = 8
DEFAULT_SNIPPET_CHARS = 400


@dataclass(frozen=True)
class Source:
    """One search hit, numbered so the answer can cite it."""

    n: int
    title: str
    url: str
    snippet: str


def sources_from(results: list[dict], *, limit: int = DEFAULT_SOURCES,
                 snippet_chars: int = DEFAULT_SNIPPET_CHARS) -> list[Source]:
    """Turn raw search results into numbered, trimmed sources.

    Results with no URL are dropped: the whole contract is that every
    claim is traceable, and a source the family cannot open is worse
    than one fewer source. Results with no snippet are kept -- the title
    alone is sometimes the answer ("Immich 2.1 released") -- but they
    carry less weight simply by being shorter.
    """
    sources: list[Source] = []
    for item in results:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        snippet = " ".join((item.get("content") or "").split())
        if len(snippet) > snippet_chars:
            snippet = snippet[: snippet_chars - 1].rstrip() + "…"
        sources.append(Source(
            n=len(sources) + 1,
            title=" ".join((item.get("title") or "").split()),
            url=url,
            snippet=snippet,
        ))
        if len(sources) >= limit:
            break
    return sources


# ── The prompt ────────────────────────────────────────────────────────
#
# Three things it has to get right, in order of how badly each fails:
#
#   1. Refusing. A model that answers from its own memory when the
#      snippets do not cover the question is the failure mode that
#      makes the whole command untrustworthy, because the answer looks
#      exactly like a good one. So "I don't know" is named as a correct
#      answer rather than left as an implicit option.
#   2. Citing. Numbers, not URLs -- a model that writes URLs from
#      memory invents plausible ones, and the numbers map back to a
#      list we printed ourselves.
#   3. Stopping. The answer goes in a chat message, so a few sentences.

_INSTRUCTIONS = """\
Answer the question using only the numbered search results below.

Rules:
- Use only what the results say. Do not add facts from your own knowledge.
- If the results do not contain the answer, say so plainly in one sentence. \
That is a correct and useful response, not a failure.
- Cite the results you used by number, like [1] or [2][3].
- Be brief: a few sentences. No preamble, no restating the question.
- Today's date is not in the results, so avoid claims about what is "current" \
unless a result says when it was written."""


def build_prompt(question: str, sources: list[Source]) -> str:
    """The full prompt: instructions, the numbered results, the question.

    The question is repeated at the end because a local model reading a
    long block of snippets attends better to what came last, and the
    instructions at the top are what it needs first.
    """
    blocks = []
    for source in sources:
        lines = [f"[{source.n}] {source.title}".rstrip(), f"    {source.url}"]
        if source.snippet:
            lines.append(f"    {source.snippet}")
        blocks.append("\n".join(lines))

    return (
        f"{_INSTRUCTIONS}\n\n"
        f"Search results:\n\n"
        f"{chr(10).join(blocks)}\n\n"
        f"Question: {question.strip()}"
    )


def render_sources(sources: list[Source]) -> str:
    """The source list printed under every answer.

    Printed whether or not the model found anything, and that is the
    point: "I could not answer this, here is what I looked at" lets the
    family judge for themselves, which a bare refusal does not.
    """
    return "\n".join(
        f"  [{s.n}] {s.title or s.url}\n      {s.url}" for s in sources
    )
