"""The memory on an instance without an AI: no compile with the model.

An instance whose `[ai] provider` is "none" tells the curator so
(`AI_OFF=true` in its environment). The curator then says once,
at its start, that the wiki and the diary wait for the AI, and compiles the
views without the model only: every push still reaches the views at once
(which cards belong, dates, list items, links), and no compile with the
model is started that could only fail.

Driven as the memory's test instance runs the curator: its own loop, in a
process of its own, with the environment its container has
(`RunningMemory.curators_loop`).
"""

from __future__ import annotations

import subprocess

from .instance import clone_of, git, running_memory
from .simpsons_cards import ONTOLOGY, VAULT

NOTE = ("family/notes/2026/10/bowling-night.md",
        "---\ntype: note\ncard_id: note-bowling-night\ntitle: Bowling night\n"
        "date: \"2026-10-12\"\ntimestamp: \"2026-10-12T19:00:00Z\"\npersons:\n  - homer\n---\n\n"
        "# Bowling night\n\n> [!summary]\n> Homer bowled a 300.\n")


def _pushed_by_homer(memory, path: str, text: str) -> None:
    """One card committed by Homer in his own clone and pushed to Forgejo."""
    homer = memory.forgejo.repo.parent / "homer"
    clone_of(memory.forgejo, homer)
    (homer / path).parent.mkdir(parents=True, exist_ok=True)
    (homer / path).write_text(text, encoding="utf-8")
    git(homer, "add", path)
    subprocess.run(["git", "-C", str(homer), "-c", "user.name=homer",
                    "-c", "user.email=homer@simpson", "commit", "-qm", f"File {path}"],
                   check=True, capture_output=True)
    git(homer, "push", "-q", "origin", "HEAD:main")


def test_without_an_ai_the_curator_compiles_without_the_model_and_says_so_once(tmp_path):
    """Given the AI is off, when the curator starts and a card is pushed,
    then it logs one line that the wiki and the diary wait for the AI,
    compiles the views at once, and starts no compile with the model."""
    with running_memory(tmp_path, {**VAULT, "ontology.toml": ONTOLOGY}) as memory:
        memory.environ["AI_OFF"] = "true"
        booted = memory.curators_loop("boot", timeout=60)
        _pushed_by_homer(memory, *NOTE)
        turned = memory.curators_loop("turns", timeout=60)

    assert booted.count("wait for the AI") == 1, booted
    assert "compiling at once" in turned, turned
    assert "with the model" not in turned, turned
    assert "No AI endpoint" not in booted + turned
