"""Every card tags a topic by its id: migration memory/0005 (ADR-017 §4).

A card's `tags` hold the ontology's topic ids (`medical`) and keywords,
lowercase with hyphens. The label a reader sees comes from the ontology,
in the instance's language, so an instance tags one subject alike
whichever language a classifier or Paperless named it in. The archivist
writes new records that way (`test_git_mirror.py`); this migration brings
the files that were there before to the same shape, with the same rule.

The memory repo is a real git repository holding a small fabricated
vault with an ontology of its own, and Forgejo a real HTTP server that
applies each commit to it (the fixture from `test_memory_card_links.py`).
"""

from __future__ import annotations

import json
import sys
from hashlib import md5
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(Path(__file__).parent))

from cards_api import CardsAPI  # noqa: E402
from format_check import format_report  # noqa: E402
from graph import load_graph  # noqa: E402
from lib import BOT_USERNAME, tag_topics_by_id  # noqa: E402
from stack.frontmatter import parse  # noqa: E402
from stack.tags import card_tags, tag_for, tag_label, tag_words  # noqa: E402
import test_memory_card_links  # noqa: E402
from test_memory_card_links import _clone, _commits, _git, _read  # noqa: E402

# Forgejo's commit endpoint, applied to the `memory` repository this module defines.
forgejo = test_memory_card_links.forgejo

TRAILER = "Migration: memory/0005"

# The family's ontology: two topics of the seed, and one they added.
ONTOLOGY = """\
[topic.medical]
names = { de = "Gesundheit", en = "Medical" }

[topic.tax_office]
names = { de = "Finanzamt", en = "Tax Office" }

[topic.donuts]
names = { de = "Krapfen", en = "Donuts" }
"""

VACCINATION = "family/documents/2026/10/2026-10-02-impfpass-bart-p41.md"
TAX = "family/documents/2026/09/2026-09-14-tax-notice-p17.md"
TAB = "family/documents/2026/10/2026-10-13-tab-p31.md"
NOTE = "family/itchy-scratchy-land/notes/2026/10/lisa-paints-0c37f8.md"
BOOKMARK = "homer/bookmarks/2026/10/lard-lad-donuts-3a338e.md"
DIARY = "family/diary/entries/2024/03/2024-03-09-b48902f594.md"


def _card(kind: str, title: str, tags: list[str], body: str = "") -> str:
    listed = "".join(f"  - {t}\n" for t in tags)
    return (f"---\ntype: {kind}\ncard_id: {kind}-{md5(title.encode()).hexdigest()[:10]}\ntitle: {title}\n"
            f"tags:\n{listed}timestamp: \"2026-10-13T21:12:08Z\"\n---\n\n# {title}\n{body}")


VAULT = {
    "ontology.toml": ONTOLOGY,
    # Filed on a German instance: Paperless tags are German labels.
    VACCINATION: _card("document", "Impfpass Bart", ["Gesundheit", "Impfung"]),
    # Filed in English, a label with a space in it.
    TAX: _card("document", "Tax notice", ["Tax Office", "Medical"]),
    # A tag no rule reads without guessing.
    TAB: _card("document", "Tab", ["Moe's", "Krapfen"]),
    # Keywords in any case; `Reise` is no topic of this ontology, `Donuts` is.
    NOTE: _card("note", "Lisa paints the park", ["Cropper Paper", "watercolour", "Donuts", "krapfen"],
                "\nBefore we leave.  \nTrailing spaces stay.\n"),
    # Already in shape: left alone.
    BOOKMARK: _card("bookmark", "Lard Lad", ["donuts", "itchy-scratchy-land"]),
    # Signed with a digest: an edit from outside the compile would make it the family's.
    DIARY: ("---\ntype: diary\ncard_id: diary-3f9a0c1b2d\ntitle: Bart's first skateboard\n"
            "tags:\n  - Gesundheit\n  - \"Person: Bart\"\nentry_id: b48902f594\n"
            "digest: 0123456789abcdef\n---\n\n# Bart's first skateboard\n"),
}


@pytest.fixture
def memory(tmp_path) -> Path:
    """The family/memory repository on Forgejo, holding the fabricated vault."""
    repo = tmp_path / "forgejo" / "memory"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    for path, text in VAULT.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed the vault")
    _git(repo, "update-server-info")
    return repo


def _tags(memory: Path, path: str) -> list[str]:
    return parse(_read(memory, path))["tags"]


class TestTopicTagsByIdOnce:
    """The first run: one commit by the memory bot, with the trailer."""

    @pytest.fixture
    def found(self, memory, forgejo, tmp_path):
        found, sha = tag_topics_by_id(forgejo, _clone(memory, tmp_path, "run1"), trailer=TRAILER)
        assert sha == _git(memory, "rev-parse", "HEAD")
        return found

    def test_one_commit_by_the_memory_bot(self, found, memory, httpserver):
        assert _commits(httpserver) == 1
        assert _git(memory, "log", "-1", "--format=%an|%B").strip() == \
            f"{BOT_USERNAME}|cards: tag topics by id in 4 card(s)\n\n{TRAILER}"

    def test_a_topic_label_in_any_language_becomes_its_id(self, found, memory):
        assert _tags(memory, VACCINATION) == ["medical", "impfung"]
        assert _tags(memory, TAX) == ["tax_office", "medical"]

    def test_a_topic_the_family_added_to_the_ontology_counts_too(self, found, memory):
        """The vault's own ontology decides, not the seed shipped with the stack."""
        assert _tags(memory, TAB)[1] == "donuts"

    def test_keywords_are_lowercase_with_hyphens_and_tags_that_meet_collapse(self, found, memory):
        # `Donuts` and `krapfen` are one topic: kept once, where the first was.
        assert _tags(memory, NOTE) == ["cropper-paper", "watercolour", "donuts"]

    def test_a_tag_no_rule_reads_stays_and_is_reported(self, found, memory):
        assert _tags(memory, TAB) == ["Moe's", "donuts"]
        assert found.left == [f"{TAB}: tag \"Moe's\" is no topic and no keyword"]

    def test_only_the_tags_change(self, found, memory):
        assert _read(memory, NOTE) == VAULT[NOTE].replace(
            "  - Cropper Paper\n  - watercolour\n  - Donuts\n  - krapfen\n",
            "  - cropper-paper\n  - watercolour\n  - donuts\n")
        for path in (BOOKMARK, DIARY, "ontology.toml"):
            assert _read(memory, path) == VAULT[path], path

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory, forgejo,
                                                          httpserver, tmp_path):
        head = _git(memory, "rev-parse", "HEAD")
        again, sha = tag_topics_by_id(forgejo, _clone(memory, tmp_path, "run2"), trailer=TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert _commits(httpserver) == 1 and _git(memory, "rev-parse", "HEAD") == head


class TestReadersFindATopicByItsLabel:
    """A topic's label is no longer in the file; readers take it from the
    ontology, so the card reads the same to them as before."""

    def _found(self, memory: Path) -> dict[str, list[str]]:
        graph = load_graph(memory)
        return {q: sorted(c.path for c in graph.find(q)) for q in ("medical", "Gesundheit", "Krapfen")}

    def test_find_returns_the_same_cards_before_and_after(self, memory, forgejo, tmp_path):
        before = self._found(memory)
        tag_topics_by_id(forgejo, _clone(memory, tmp_path, "run"))
        after = self._found(memory)

        assert after == before
        assert after["Gesundheit"] == sorted([VACCINATION, TAX, DIARY])

    def test_the_card_browser_shows_a_topic_by_its_label_in_the_instance_language(
            self, memory, forgejo, tmp_path):
        tag_topics_by_id(forgejo, _clone(memory, tmp_path, "run"))
        api = CardsAPI(vault=memory, language="de")
        card_id = parse(VAULT[VACCINATION])["card_id"]

        status, _, body = api.answer(f"/card/{card_id}", {})
        assert status == 200
        assert json.loads(body)["tag_labels"] == {"medical": "Gesundheit", "impfung": "impfung"}


class TestTheFormatCheck:
    """The report's "invalid tags" finding is what 0005 exists to empty."""

    def _invalid(self, memory: Path) -> dict[str, list[str]]:
        return {f["path"]: f["tags"] for f in format_report(load_graph(memory))["invalid_tags"]}

    def test_lists_every_label_and_every_tag_out_of_shape(self, memory):
        assert self._invalid(memory) == {
            VACCINATION: ["Gesundheit", "Impfung"], TAX: ["Tax Office", "Medical"],
            TAB: ["Moe's", "Krapfen"], NOTE: ["Cropper Paper", "Donuts", "krapfen"],
            DIARY: ["Gesundheit", "Person: Bart"]}

    def test_after_the_migration_holds_only_what_it_reported_and_the_diary(
            self, memory, forgejo, tmp_path):
        found, _ = tag_topics_by_id(forgejo, _clone(memory, tmp_path, "run"))

        assert self._invalid(memory) == {TAB: ["Moe's"], DIARY: ["Gesundheit", "Person: Bart"]}
        assert len(found.left) == 1


class TestATopicCardDeclaresATopic:
    """A topic card is the household's own topic on top of the ontology:
    `family/itchy-scratchy-land/topic.md` declares the topic
    `itchy-scratchy-land`, its folder. Its title is to that id what a label
    is to an ontology id. Every writer and reader takes the topics from
    `get_ontology`, the archivist's `card_tags` and the readers' `tag_label`
    among them, so the memory's topic cards are topics wherever the
    ontology's are. The family's `medical` and `tax-office` folders have
    topic cards too; their ids are the ontology's, whose meaning wins."""

    TOPICS = {
        "family/itchy-scratchy-land/topic.md": "Itchy & Scratchy Land",
        "family/duff-gardens/topic.md": "Summer Trip to Duff Gardens",
        "family/medical/topic.md": "Arztbesuche",
        "family/tax-office/topic.md": "Steuerberater",
    }

    @pytest.fixture
    def ontology(self, tmp_path):
        from lib import get_ontology

        vault = tmp_path / "vault"
        for path, title in {**self.TOPICS, "ontology.toml": None}.items():
            (vault / path).parent.mkdir(parents=True, exist_ok=True)
            (vault / path).write_text(ONTOLOGY if title is None else
                                      f"---\ntype: topic\ncard_id: topic-{Path(path).parent.name}\n"
                                      f"title: {title}\n---\n\n# {title}\n", encoding="utf-8")
        return get_ontology(vault)

    def test_its_title_in_any_spelling_the_rule_reads_is_its_id(self, ontology):
        """As "Tax Office" becomes `tax_office`."""
        assert [tag_for(t, ontology) for t in (
            "Itchy & Scratchy Land", "itchy & scratchy land", "Itchy Scratchy Land",
            "itchy_scratchy_land", "Tax Office")] == [
            "itchy-scratchy-land", "itchy-scratchy-land", "itchy-scratchy-land",
            "itchy-scratchy-land", "tax_office"]

    def test_a_writer_tags_a_record_by_its_id(self, ontology):
        """What the archivist writes for the tags a classifier returned."""
        assert card_tags(["Summer Trip to Duff Gardens", "Itchy & Scratchy Land", "Rollercoaster"],
                         ontology) == ["duff-gardens", "itchy-scratchy-land", "rollercoaster"]

    def test_a_reader_shows_its_title_as_its_label(self, ontology):
        assert tag_label("duff-gardens", ontology, "de") == "Summer Trip to Duff Gardens"
        assert tag_words("duff-gardens", ontology) == ["duff-gardens", "Summer Trip to Duff Gardens"]

    def test_an_id_the_ontology_has_keeps_the_ontologys_meaning(self, ontology):
        """The `medical` and `tax-office` folders do not take the ontology's
        topics over, and their titles stay keywords."""
        assert card_tags(["medical", "tax-office", "Arztbesuche", "Steuerberater"], ontology) == [
            "medical", "tax_office", "arztbesuche", "steuerberater"]
        assert tag_label("medical", ontology, "de") == "Gesundheit"

    def test_the_seed_alone_declares_nothing(self):
        """Without a memory there are no topic cards: the shipped ontology only."""
        from lib import get_ontology

        assert tag_for("Itchy & Scratchy Land", get_ontology(None)) is None
