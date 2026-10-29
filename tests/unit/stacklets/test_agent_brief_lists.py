"""What the agent's brief says about the room's topic and its lists.

A pointer, not the items: the brief goes stale within the turn, so it
says which lists to fetch and how, and the agent reads the live list
when someone asks. Which topic a room is follows the archivist's rule
for topic rooms, `Thema: Einkauf` or `Topic: Camping`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "agent" / "runtime"))

from brief import brief_for, brief_lines, topic_for_room_label, when_sent  # noqa: E402
from stacklets.docs.bot.topic_rooms import parse_topic_name  # noqa: E402
from tests.memory.unit.instance import running_memory  # noqa: E402

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


def test_a_runtime_with_the_memory_elsewhere_gets_the_same_briefing(tmp_path):
    """The bot runner mounts the family memory at its own path, not in
    nanobot's workspace; a runtime there is briefed the same way."""
    briefing = _brief(tmp_path)
    msg = SimpleNamespace(metadata={"room": "Camping"}, sender_id="")

    assert brief_for(msg, tmp_path / "vault") == briefing != []


# ── Which topic a room is ────────────────────────────────────────────────

ROOM_NAMES = ["Thema: Einkauf", "Thema: Einkäufe & Besorgungen", "Topic: Itchy & Scratchy Land",
              "thema:Café Müller", "Topic: " + "Ein sehr langer Name für ein Thema " * 3]


def _vault_with_list(tmp_path, topic: str) -> Path:
    lists = tmp_path / "family" / "lists"
    lists.mkdir(parents=True)
    (lists / "einkaufsliste.md").write_text(
        f"---\ntype: list\ntitle: Einkaufsliste\ntopics:\n  - {topic}\n---\n\n- [ ] Milch\n")
    return tmp_path


def test_a_german_topic_room_is_the_topic_the_archivist_bound_it_to(tmp_path):
    assert topic_for_room_label("Thema: Einkauf", _vault_with_list(tmp_path, "einkauf")) == "einkauf"


def test_a_room_kept_only_for_lists_is_known_by_its_lists(tmp_path):
    """No page yet, because nothing was filed there; its list makes it a topic."""
    vault = _vault_with_list(tmp_path, "einkaufe-besorgungen")
    assert topic_for_room_label("Thema: Einkäufe & Besorgungen", vault) == "einkaufe-besorgungen"


def test_a_room_no_topic_knows_is_none(tmp_path):
    assert topic_for_room_label("Thema: Garten", _vault_with_list(tmp_path, "einkauf")) == ""


@pytest.mark.parametrize("name", ROOM_NAMES)
def test_the_agent_and_the_archivist_derive_the_same_topic(tmp_path, name):
    """The agent cannot import the archivist's rule, so it restates it; this holds them together."""
    slug = parse_topic_name(name).slug
    assert topic_for_room_label(name, _vault_with_list(tmp_path, slug)) == slug


def test_the_brief_offers_no_overview_a_topic_does_not_have(tmp_path):
    vault = tmp_path / "vault"
    _vault_with_list(vault, "einkauf")
    lines = brief_lines(SimpleNamespace(metadata={"room": "Thema: Einkauf"}, sender_id=""), tmp_path)
    assert lines[0] == "This room's topic is 'einkauf'."
    assert "einkaufsliste" in lines[1]


def test_a_message_says_when_it_was_sent_so_by_friday_becomes_a_date():
    from datetime import datetime, timedelta, timezone
    sent = datetime(2026, 10, 12, 18, 55, tzinfo=timezone(timedelta(hours=2), "CEST"))
    assert when_sent(sent) == "Sent Monday, 2026-10-12 18:55 (CEST)."


# ── A renamed topic ──────────────────────────────────────────────────────

def _renamed_vault(tmp_path) -> Path:
    """The family's sewing topic, renamed from `nahprojekte` to `naehprojekte`."""
    topic = tmp_path / "family" / "naehprojekte"
    topic.mkdir(parents=True)
    (topic / "topic.md").write_text(
        "---\ntype: topic\ncard_id: topic-nahprojekte\ntitle: Nähprojekte\n"
        "former_topic_ids:\n  - nahprojekte\n---\n\n# Nähprojekte\n")
    (topic / "about.md").write_text("# Nähprojekte\n")
    # The empty folders an older release's rename left behind.
    (tmp_path / "family" / "nahprojekte" / "notes" / "2026").mkdir(parents=True)
    return tmp_path


def test_a_room_whose_topic_was_renamed_is_the_topic_under_its_new_id(tmp_path):
    """The room is still "Thema: Nähprojekte", which derives the old id;
    the topic card names the id it has now, as it does for the archivist."""
    from stack.cards.lookup import renamed_topic

    vault = _renamed_vault(tmp_path)

    assert topic_for_room_label("Thema: Nähprojekte", vault) == "naehprojekte" == \
        renamed_topic("family", "nahprojekte", root=vault)[0]


def test_the_brief_names_a_renamed_topic(tmp_path):
    msg = SimpleNamespace(metadata={"room": "Thema: Nähprojekte"}, sender_id="")

    _renamed_vault(tmp_path / "vault")

    lines = brief_lines(msg, tmp_path)

    assert any("This room's topic is 'naehprojekte'" in line for line in lines)


def test_after_the_rename_command_the_room_is_the_topic_under_its_new_id(tmp_path):
    """Given the topic renamed with `stack memory topic rename` on memory's
    test instance, the room's name, which derives the old id, maps to the
    new one in the memory the command left (no empty old folder in it)."""
    note = ("---\ntype: note\ncard_id: note-1a1a1a1a1a\ntitle: Schnittmuster\n"
            "tags:\n  - nahprojekte\n---\n\n# Schnittmuster\n")
    files = {"family/nahprojekte/topic.md": ("---\ntype: topic\ncard_id: topic-nahprojekte\n"
                                             "title: Nähprojekte\n---\n"),
             "family/nahprojekte/notes/2026/10/schnittmuster.md": note}
    with running_memory(tmp_path / "data", files) as memory:
        answer = memory.person("admin").runs(
            "memory", "topic", ["rename", "nahprojekte", "naehprojekte"])
        assert answer.get("committed") is True, answer

        assert topic_for_room_label("Thema: Nähprojekte", memory.vault) == "naehprojekte"
