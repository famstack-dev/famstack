"""The family wiki is built from the family's shared part, and nothing else.

The wiki container mounts one folder as Quartz's content (`/vault`). The
curator fills it from the brain and the media archive with
`wiki_input.publish`. Every page, the search index, the graph, the explorer,
backlinks, folder and tag pages, RSS and the sitemap come from that folder, so
a card that is not in it is on no page of the wiki. A card is in it when the
family as a whole may read it (`stack.cards.audience.readable` with no person
as the audience): the top level and the shared bucket. A file of the media
archive is in it when such a card embeds or links it.

The test builds the folder as the container gets it, over a brain with
Marge's own note, her own topic, her member page and Homer's own calendar,
and a shared note beside them. A case takes a few milliseconds.
"""

from __future__ import annotations

from pathlib import Path

from stacklets.memory.bot import wiki_input

SHARED_NOTE = "family/notes/2026/11/ausflug.md"
BRAIN = {
    # The top level and the family's bucket: what the family shares.
    "index.md": "---\ntitle: The Simpsons\n---\n\n# The Simpsons\n",
    "ontology.toml": "[topics]\n",
    SHARED_NOTE: ("---\ntype: note\ncard_id: note-ausflug\ntitle: Ausflug\n---\n\n"
                  "# Ausflug\n\n![[/media/2026/11/lake.jpg]]\n"),
    "family/notes/2026/11/ausflug.json": '{"sources": []}\n',
    "family/camping/about.md": "---\ntitle: Camping\n---\n\n# Camping\n",
    "family/lists/dates.md": "---\ntype: list\nkind: calendar\ntitle: Dates\n---\n\n- [ ] 2026-12-24 Christmas\n",
    # Marge's own note, with a photo and its sidecar.
    "marge/notes/2026/11/planung.md": ("---\ntype: note\ncard_id: note-planung\ntitle: Planung\n---\n\n"
                                       "# Planung\n\nSURPRISE-PARTY\n\n![[/media/2026/11/cake.jpg]]\n"),
    "marge/notes/2026/11/planung.json": '{"sources": []}\n',
    # Marge's own topic: its card, its compiled page and a note in it.
    "marge/geschenke/topic.md": "---\ntype: topic\ncard_id: topic-geschenke\ntitle: Geschenke\n---\n",
    "marge/geschenke/about.md": "---\ntitle: Geschenke\n---\n\n# Geschenke\n",
    "marge/geschenke/notes/2026/11/liste.md": ("---\ntype: note\ntitle: Liste\n---\n\n"
                                               "[Rechnung](/media/2026/11/skateboard.pdf)\n"),
    # Marge's member page, compiled into her bucket.
    "marge/about.md": "---\ntitle: Marge\n---\n\n# Marge\n",
    # Homer's own calendar and its archive, which the calendar's look back reads.
    "homer/lists/dates-homer.md": "---\ntype: list\nkind: calendar\ntitle: Homer's dates\n---\n\n- [ ] 2026-12-01 Moe\n",
    "homer/lists/archive/dates-homer/2026-11.md": "- [x] 2026-11-20 Bowling\n",
}
MEDIA = {"2026/11/lake.jpg": b"lake", "2026/11/cake.jpg": b"cake",
         "2026/11/skateboard.pdf": b"pdf", "2026/11/unused.jpg": b"unused"}
SHARED = {"index.md", "ontology.toml", SHARED_NOTE, "family/notes/2026/11/ausflug.json",
          "family/camping/about.md", "family/lists/dates.md", "media/2026/11/lake.jpg"}


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


class Memory:
    """A brain, a media archive, and the wiki's input the curator publishes from them."""

    def __init__(self, root: Path):
        self.brain, self.media, self.wiki = root / "brain", root / "media", root / "wiki"
        _write(self.brain, BRAIN)
        _write(self.brain / ".git", {"HEAD": "ref: refs/heads/main\n"})
        _write(self.media, MEDIA)

    def publish(self) -> set[str]:
        """Publish the wiki's input; the files it then holds."""
        wiki_input.publish(self.brain, self.media, self.wiki, shared_bucket="family")
        return _files(self.wiki)


def test_holds_only_what_the_family_shares(tmp_path):
    """Given Marge's own note, her own topic, her member page and Homer's own
    calendar beside a shared note, when the wiki's input is published, then it
    holds the top level and the family's bucket, and none of the person buckets."""
    assert Memory(tmp_path).publish() == SHARED


def test_serves_only_the_media_a_shared_card_shows(tmp_path):
    """Given a photo a shared note embeds, Marge's photo and her file, and a
    photo no card shows, when the wiki's input is published, then only the
    shared note's photo is in it, with its bytes."""
    memory = Memory(tmp_path)

    files = memory.publish()

    assert {f for f in files if f.startswith("media/")} == {"media/2026/11/lake.jpg"}
    assert (memory.wiki / "media/2026/11/lake.jpg").read_bytes() == b"lake"


def test_a_person_added_later_stays_out(tmp_path):
    """Given a published input, when a person added after the install files a
    note into their own bucket, then the next publish leaves it out too, with no
    list of people to keep."""
    memory = Memory(tmp_path)
    memory.publish()
    _write(memory.brain, {"maggie/notes/2026/11/first.md": "---\ntitle: First\n---\n"})

    assert memory.publish() == SHARED


def test_follows_the_brain(tmp_path):
    """Given a published input, when a shared note changes and another is
    removed from the brain, then the next publish has the new text and drops
    the removed note, its sidecar and its photo."""
    memory = Memory(tmp_path)
    memory.publish()
    _write(memory.brain, {"family/camping/about.md": "# Camping, new\n"})
    for rel in (SHARED_NOTE, "family/notes/2026/11/ausflug.json"):
        (memory.brain / rel).unlink()

    files = memory.publish()

    assert files == SHARED - {SHARED_NOTE, "family/notes/2026/11/ausflug.json", "media/2026/11/lake.jpg"}
    assert (memory.wiki / "family/camping/about.md").read_text(encoding="utf-8") == "# Camping, new\n"


def test_a_card_moved_into_a_persons_bucket_leaves(tmp_path):
    """Given a shared note in the published input, when it moves into Marge's
    bucket, then the next publish no longer has it."""
    memory = Memory(tmp_path)
    memory.publish()
    moved = memory.brain / "marge/notes/2026/11/ausflug.md"
    (memory.brain / SHARED_NOTE).rename(moved)

    assert SHARED_NOTE not in memory.publish()
    assert not list(memory.wiki.rglob("ausflug.md"))
