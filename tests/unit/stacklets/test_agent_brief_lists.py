"""The agent's brief names the topic's lists that have something open.

A pointer, not the items: the brief goes stale within the turn, so it
says which lists to fetch and how, and the agent reads the live list
when someone asks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "agent" / "runtime"))

from brief import brief_lines  # noqa: E402

PACKING = "---\ntype: list\ntitle: Packliste\ntopics:\n  - camping\n---\n\n- [ ] Zelt\n"
DONE = "---\ntype: list\ntitle: Erledigt\ntopics:\n  - camping\n---\n\n- [x] Gas\n"
GROCERIES = "---\ntype: list\ntitle: Einkauf\n---\n\n- [ ] Milch\n"


def _brief(tmp_path) -> list[str]:
    vault = tmp_path / "vault"
    (vault / "family" / "camping").mkdir(parents=True)
    (vault / "family" / "camping" / "about.md").write_text("# Camping\n")
    lists = vault / "family" / "lists"
    lists.mkdir()
    for slug, text in (("packliste", PACKING), ("erledigt", DONE), ("einkauf", GROCERIES)):
        (lists / f"{slug}.md").write_text(text)
    msg = SimpleNamespace(metadata={"room": "Camping"}, sender_id="")
    return brief_lines(msg, tmp_path)


def test_names_only_the_topics_lists_with_open_items(tmp_path):
    assert ("This topic's lists with open items: packliste; run "
            "`stack memory list show <list>` to see one.") in _brief(tmp_path)
