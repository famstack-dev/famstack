"""memory answer — answer a question from search hits, run where the model lives.

`stack memory ask` does the keyword rewrite and the search on the
host, then sends the question and the top hits here as JSON on stdin:

    {"question": "...", "evidence": [{"path", "title", "date",
                                      "excerpt", "summary"}, ...]}

The answer goes to stdout with `[N]` citations that number the
evidence in the order given. Exit 0 with an answer, exit 1 without one
(bad payload, model unavailable, empty reply); the host then shows the
search results instead.

The prompt follows the archivist's recall synthesis
(`stacklets/docs/bot/pipeline.py`, `_build_synthesize_prompt`). It is
kept here rather than imported, because that module belongs to the docs
stacklet and pulls in its whole pipeline.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stack.ai.client import LLM

HELP = "Answer a question from search hits (JSON on stdin)"


def build_prompt(question: str, evidence: list[dict], language: str, today: str) -> str:
    """The question, the numbered hits, and the rules for citing them."""
    items = []
    for n, ev in enumerate(evidence, 1):
        head = f"[{n}] {ev.get('path', '')}"
        if ev.get("title"):
            head += f" | {ev['title']}"
        if ev.get("date"):
            head += f" | {ev['date']}"
        lines = [head]
        if ev.get("summary"):
            lines.append(f"    Summary: {ev['summary']}")
        if ev.get("excerpt"):
            lines.append(f"    Matching line: {ev['excerpt']}")
        items.append("\n".join(lines))
    evidence_block = "\n\n".join(items)
    return f"""You are answering a family member's question from their own notes. Answer using ONLY the evidence below. Never invent facts.

Today's date is {today}. Use it to resolve relative-time phrases.

Question: {question}

Evidence (numbered; cite the ones you used as [N]):

{evidence_block}

Rules:
- Answer in one or two sentences.
- Cite every fact: "[1]", "[2, 3]".
- If the evidence does not answer the question, say so in one sentence and name the closest hit.
- Respond in the family's language: {language}.
- No preamble. Answer directly.

Answer:"""


async def run(llm: "LLM", argv: list[str]) -> int:
    """Entry point the dispatcher calls with the shared LLM client."""
    try:
        payload = json.loads(sys.stdin.read())
        question = str(payload["question"])
        evidence = list(payload["evidence"])
    except (ValueError, KeyError, TypeError) as e:
        print(f"answer: bad payload on stdin: {e}", file=sys.stderr)
        return 1

    prompt = build_prompt(question, evidence,
                          language=os.environ.get("LANGUAGE", "en"),
                          today=date.today().isoformat())
    try:
        # Capped like `stack web ask`: a valid answer is a few sentences,
        # and a model caught in a repetition loop would otherwise run
        # until the client timeout.
        raw = await llm.complete("recall", prompt, json_mode=False,
                                 temperature=0.0, max_tokens=600)
    except Exception as e:  # transport errors: the host falls back to results
        print(f"answer: model unavailable: {e}", file=sys.stderr)
        return 1

    answer = (raw or "").strip()
    if not answer:
        print("answer: the model returned nothing", file=sys.stderr)
        return 1
    print(answer)
    return 0
