"""memory rewrite <question> — the keyword hop, run where the model lives.

`stack memory search --nl` needs a model, and the host `./stack` is
stdlib-only by design. So the host asks this, inside the bot-runner,
for the one thing it cannot work out itself: which words would
literally appear in a document that answers the question. The host
still does the searching.

Output is one keyword per line on stdout, which is the smallest thing
that survives a `docker exec` round trip without a parser. Exit 0 with
keywords, exit 1 with none. Exit 1 is not an error: the host reads it
as "no rewrite available" and searches the question literally, the
same fallback the archivist has always had.

The ontology comes from the live vault (`MEMORY_VAULT_DIR`), because
the keywords have to be in the vocabulary this family files under. A
`--vault` override on the host does not change that: the taxonomy is
the family's, not the directory's.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from memory.lib import get_ontology, rewrite_query  # noqa: E402

from stack.ai.client import LLM  # noqa: E402

HELP = "Turn a natural-language question into vault search keywords"


async def run(llm: LLM, argv: list[str]) -> int:
    """Entry point the dispatcher calls with the shared LLM client."""
    if not argv or not argv[0].strip():
        print("usage: rewrite <question>", file=sys.stderr)
        return 2

    question = argv[0]
    vault = Path(os.environ.get("MEMORY_VAULT_DIR", "/data/memory/vault"))
    language = os.environ.get("LANGUAGE", "en")

    # `get_ontology` falls back to the shipped seed when the vault has
    # no ontology.toml of its own, so a fresh install still gets sane
    # topic names rather than an empty prompt section.
    ontology = get_ontology(vault if vault.exists() else None)

    keywords = await rewrite_query(
        question,
        llm=llm,
        ontology_section=ontology.classifier_prompt_section(language),
        language=language,
    )
    if not keywords:
        return 1

    print("\n".join(keywords))
    return 0
