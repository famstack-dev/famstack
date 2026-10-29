"""`stack memory topic rename`, described by what the admin sees it do.

The admin runs the command as `./stack memory topic rename` does, through
`Stack.run_cli_command`, against memory's test instance: core's card door,
the curator's API over its card store, Forgejo as a bare repository
(`running_memory`). What is asserted is what Forgejo holds afterwards and
what the command answered (the pattern of `test_memory_write.py`).

The Simpsons keep a sewing topic. The archivist made its folder from the
room "Thema: Nähprojekte" and spelled it `nahprojekte`, which reads as
another word; the admin renames it to `naehprojekte`.
"""

from __future__ import annotations

import pytest

from stack.frontmatter import parse
from stack.lists import read_list
from stacklets.memory.memory_api import Refused, put
from tests.memory.unit.instance import running_memory

OLD, NEW = "family/nahprojekte", "family/naehprojekte"
TOPIC = f"{OLD}/topic.md"
PATTERN = f"{OLD}/notes/2026/10/schnittmuster.md"
LIST = "family/lists/nahprojekte.md"
ARCHIVE = "family/lists/archive/nahprojekte/2026-09.md"
FABRIC = "family/notes/2026/10/stoff.md"
HOMERS = "homer/nahprojekte/notes/2026/10/knopf.md"
SHOP = "family/correspondents/stoffladen.md"
# Homer's own workshop topic. Its list lives in the shared bucket, where
# the archivist starts every topic's list.
SAW = "homer/werkstatt/notes/2026/10/saege.md"
# Homer's and Marge's own button topics, one id in two buckets, and the
# one list the archivist keeps for both in the shared bucket.
HOMERS_BUTTON = "homer/knopf/notes/2026/10/hemd.md"
MARGES_BUTTON = "marge/knopf/notes/2026/10/mantel.md"
BUTTON_LIST = "family/lists/knopf.md"
SHOP_LIST = "family/lists/werkstatt.md"
SHOP_ARCHIVE = "family/lists/archive/werkstatt/2026-09.md"
EDUCATION = "family/education/notes/2026/10/zeugnis.md"
PHOTOS = "family/fotos/sommer.md"


def _note(card_id: str, title: str, tags: list[str], body: str = "") -> str:
    tagged = "".join(f"  - {t}\n" for t in tags)
    return (f"---\ntype: note\ncard_id: {card_id}\ntitle: {title}\ndate: \"2026-10-12\"\n"
            f"tags:\n{tagged}---\n\n# {title}\n\n{body}")


FILES = {
    TOPIC: "---\ntype: topic\ncard_id: topic-nahprojekte\ntitle: Nahprojekte\n---\n\n# Nahprojekte\n",
    PATTERN: _note("note-1a1a1a1a1a", "Schnittmuster für Lisas Kleid", ["nahprojekte", "kleid"]),
    f"{OLD}/notes/2026/10/schnittmuster.json": '{"card_id": "note-1a1a1a1a1a"}\n',
    LIST: ("---\ntype: list\ncard_id: list-nahprojekte\ntitle: Nähprojekte\ntopics:\n"
           "  - nahprojekte\n---\n\n# Nähprojekte\n\n"
           "- [ ] Stoff kaufen [Schnittmuster für Lisas Kleid]"
           "(../nahprojekte/notes/2026/10/schnittmuster.md)\n- [ ] Garn\n"),
    ARCHIVE: ("---\ntype: list\nkind: archive\ncard_id: list-nahprojekte-2026-09\n"
              "title: Nähprojekte\n---\n\n- [x] Nadeln ✅ 2026-09-02\n"),
    FABRIC: _note("note-2b2b2b2b2b", "Stoff vom Markt", ["nahprojekte"],
                  "Passt zum [Schnittmuster](../../../nahprojekte/notes/2026/10/schnittmuster.md).\n"),
    HOMERS: _note("note-3c3c3c3c3c", "Knopf annähen", ["nahprojekte"]),
    # A correspondent's `topics` hold topic ids too; the household's own
    # topics are among them.
    SHOP: ("---\ntype: correspondent\ncard_id: correspondent-stoffladen\ntitle: Stoffladen\n"
           "topics:\n  - nahprojekte\n---\n"),
    SAW: _note("note-6a6a6a6a6a", "Neue Säge", ["werkstatt"]),
    HOMERS_BUTTON: _note("note-7b7b7b7b7b", "Hemdknopf", ["knopf"]),
    MARGES_BUTTON: _note("note-8c8c8c8c8c", "Mantelknopf", ["knopf"]),
    BUTTON_LIST: ("---\ntype: list\ncard_id: list-knopf\ntitle: Knöpfe\ntopics:\n  - knopf\n"
                  "---\n\n- [ ] Hemd [Hemdknopf](../../homer/knopf/notes/2026/10/hemd.md)\n"
                  "- [ ] Mantel [Mantelknopf](../../marge/knopf/notes/2026/10/mantel.md)\n"),
    SHOP_LIST: ("---\ntype: list\ncard_id: list-werkstatt\ntitle: Werkstatt\ntopics:\n"
                "  - werkstatt\n---\n\n# Werkstatt\n\n"
                "- [ ] Sägeblatt [Neue Säge](../../homer/werkstatt/notes/2026/10/saege.md)\n"),
    SHOP_ARCHIVE: ("---\ntype: list\nkind: archive\ncard_id: list-werkstatt-2026-09\n"
                   "title: Werkstatt\n---\n\n- [x] Leim ✅ 2026-09-03\n"),
    # A folder named like an ontology topic, and one that is no topic at all.
    EDUCATION: _note("note-4d4d4d4d4d", "Zeugnis", ["education"]),
    PHOTOS: _note("note-5e5e5e5e5e", "Sommer", ["fotos"]),
}


@pytest.fixture
def memory(tmp_path):
    """The Simpsons' memory with the sewing topic in the family and Homer's own."""
    with running_memory(tmp_path, FILES) as running:
        yield running


@pytest.fixture
def admin(memory):
    return memory.person("admin")


def _renames(admin, *args: str) -> dict:
    return admin.runs("memory", "topic", ["rename", *args])


def _moved(path: str) -> str:
    return path.replace("nahprojekte", "naehprojekte")


def test_a_rename_moves_the_topic_and_carries_its_new_id_in_one_commit(memory, admin):
    """Given the family's sewing topic with a note, a list linking it, an
    archived month and a card elsewhere tagged with it, when the admin
    renames it with a title, then one commit moves the folder, the list and
    its archive, the list links the note at its new path, the cards carry
    the new id, and the topic card keeps its id and shows the new title.

    Everything that found the topic before finds it at its new id.
    """
    answer = _renames(admin, "nahprojekte", "naehprojekte", "Nähprojekte")

    assert answer["committed"] is True
    assert len(memory.new_commits()) == 1
    for path in (TOPIC, PATTERN, LIST, ARCHIVE):
        assert memory.text(path) == ""
        assert memory.text(_moved(path))
    card = parse(memory.page(f"{NEW}/topic.md"))
    assert (card["card_id"], card["title"], card["former_topic_ids"]) == \
        ("topic-nahprojekte", "Nähprojekte", ["nahprojekte"])
    assert "# Nähprojekte" in memory.page(f"{NEW}/topic.md")
    lst = read_list(memory.page(_moved(LIST)), path=_moved(LIST))
    assert lst.topics == ["naehprojekte"]
    assert lst.items[0].links[0].target == _moved(PATTERN)
    assert parse(memory.page(_moved(PATTERN)))["tags"] == ["naehprojekte", "kleid"]
    assert parse(memory.page(FABRIC))["tags"] == ["naehprojekte"]
    assert memory.text(_moved(f"{OLD}/notes/2026/10/schnittmuster.json")) == \
        FILES[f"{OLD}/notes/2026/10/schnittmuster.json"]


def test_a_rename_names_the_links_written_by_hand_it_leaves_alone(memory, admin):
    """Given a card outside the topic that links its note by hand, when the
    admin renames the topic, then the link stays as it is and the admin is
    told which card holds it.

    A link inside a person's text can be anywhere in it; the admin fixes it.
    """
    answer = _renames(admin, "nahprojekte", "naehprojekte")

    assert answer["links_left"] == [
        {"path": FABRIC, "link": "../../../nahprojekte/notes/2026/10/schnittmuster.md"}]
    assert admin.was_told(FABRIC)


def test_without_a_title_the_topic_keeps_its_title(memory, admin):
    _renames(admin, "nahprojekte", "naehprojekte")

    assert parse(memory.page(f"{NEW}/topic.md"))["title"] == "Nahprojekte"


def test_a_personal_topic_is_renamed_in_its_bucket_alone(memory, admin):
    """Given Homer's own sewing topic beside the family's, when the admin
    renames Homer's, then only Homer's moves; the family's stays as it was.

    Topic ids are unique within a bucket only.
    """
    _renames(admin, "nahprojekte", "naehprojekte", "--bucket", "homer")

    assert memory.text(_moved(HOMERS))
    assert parse(memory.page(_moved(HOMERS)))["tags"] == ["naehprojekte"]
    assert memory.text(HOMERS) == ""
    assert all(path.startswith("homer/") for path in memory.forgejo.changed())
    assert memory.page(PATTERN) == FILES[PATTERN]


@pytest.mark.parametrize("old, new", [
    ("gartenarbeit", "garten"),          # no such topic
    ("nahprojekte", "Nähprojekte"),     # not a topic id
    ("nahprojekte", "lists"),           # a name the bucket uses
    ("nahprojekte", "notes"),
    ("nahprojekte", "people"),
    ("nahprojekte", "diary"),
    ("nahprojekte", "emails"),
    ("lists", "listen"),                # the bucket's own folders are no topic
    ("notes", "notizen"),
    ("people", "leute"),
    ("fotos", "bilder"),                # a folder of the bucket, no topic in it
    ("education", "schule"),            # an ontology topic
])
def test_a_rename_that_cannot_be_made_changes_nothing(memory, admin, old, new):
    answer = _renames(admin, old, new)

    assert "error" in answer
    assert memory.new_commits() == []


def test_a_second_rename_onto_a_taken_id_is_refused(memory, admin):
    """Given the topic renamed once, when the admin runs the same rename
    again, then it is refused and nothing changes: the old id is gone and
    the new one is taken."""
    _renames(admin, "nahprojekte", "naehprojekte")
    first = memory.forgejo.head()

    assert "error" in _renames(admin, "nahprojekte", "naehprojekte")
    assert memory.forgejo.head() == first


def test_a_list_of_the_new_id_makes_it_taken(memory, admin):
    memory.edited_in_forgejo({"family/lists/naehprojekte.md": (
        "---\ntype: list\ncard_id: list-naehprojekte\ntitle: Andere\n---\n\n- [ ] Faden\n")})
    before = memory.forgejo.head()

    assert "error" in _renames(admin, "nahprojekte", "naehprojekte")
    assert memory.forgejo.head() == before


def test_a_dry_run_says_what_would_move_and_writes_nothing(memory, admin):
    answer = _renames(admin, "nahprojekte", "naehprojekte", "--dry-run")

    assert answer["committed"] is False
    assert [PATTERN, _moved(PATTERN)] in answer["moved"]
    assert memory.new_commits() == []


def test_an_ontology_topic_is_not_renamed_and_the_admin_is_told_why(memory, admin):
    """The classifier tags documents in every bucket with an ontology id;
    renaming one is a change to the ontology, not to one topic."""
    answer = _renames(admin, "education", "schule")

    assert "ontology" in answer["error"]
    assert memory.new_commits() == []


def test_a_correspondent_that_names_the_topic_keeps_its_topics(memory, admin):
    """A correspondent's `topics` are not the topic's cards, and the rename
    does not stop at them: the shop stays as it was, the rename is made."""
    answer = _renames(admin, "nahprojekte", "naehprojekte")

    assert answer["committed"] is True
    assert memory.page(SHOP) == FILES[SHOP]


def test_nothing_is_filed_under_the_old_id_after_the_rename(memory, admin):
    """Given the topic renamed, when anyone writes a card into the old
    folder (the archivist with a binding it read before the rename, a
    command), then the curator refuses it and names the new folder: a file
    there would make the old topic again and split it."""
    _renames(admin, "nahprojekte", "naehprojekte")
    stray = FILES[PATTERN].replace("note-1a1a1a1a1a", "note-6f6f6f6f6f")

    with pytest.raises(Refused, match="family/naehprojekte/"):
        memory.api.commit([put(f"{OLD}/notes/2026/10/knopf.md", stray, base="")],
                          by="marge", kind="capture", title="Knopf")
    assert memory.text(f"{OLD}/notes/2026/10/knopf.md") == ""


def test_the_old_folder_is_not_left_empty_in_the_curator_s_clone(memory, admin):
    """Git has no folders; a folder left on disk reads as the topic still there."""
    _renames(admin, "nahprojekte", "naehprojekte")

    assert not (memory.vault / OLD).exists()


# ── A personal topic's list, in the shared bucket ───────────────────────

def test_a_personal_topic_takes_its_list_from_the_shared_bucket_along(memory, admin):
    """Given Homer's workshop topic, its list and archive in the family's
    lists, when the admin renames it in Homer's bucket, then the list and
    the archive move with it, the list carries the new id and links the
    note at its new path, and nothing else of the family's changes."""
    answer = _renames(admin, "werkstatt", "hobbykeller", "--bucket", "homer")

    assert answer["committed"] is True
    moved_list, moved_note = "family/lists/hobbykeller.md", "homer/hobbykeller/notes/2026/10/saege.md"
    assert (memory.text(SHOP_LIST), memory.text(SHOP_ARCHIVE)) == ("", "")
    assert memory.text("family/lists/archive/hobbykeller/2026-09.md") == FILES[SHOP_ARCHIVE]
    lst = read_list(memory.page(moved_list), path=moved_list)
    assert lst.topics == ["hobbykeller"]
    assert lst.items[0].links[0].target == moved_note
    family = [p for p in memory.forgejo.changed() if p.startswith("family/")]
    assert set(family) <= {SHOP_LIST, SHOP_ARCHIVE, moved_list,
                           "family/lists/archive/hobbykeller/2026-09.md"}


@pytest.mark.parametrize("taken", ["family/lists/hobbykeller.md",
                                   "family/lists/archive/hobbykeller/2026-08.md"])
def test_a_personal_topic_is_not_renamed_onto_a_list_the_family_has(memory, admin, taken):
    memory.edited_in_forgejo({taken: (
        "---\ntype: list\ncard_id: list-hobbykeller\ntitle: Hobbykeller\n---\n\n- [ ] Farbe\n")})
    before = memory.forgejo.head()

    assert "error" in _renames(admin, "werkstatt", "hobbykeller", "--bucket", "homer")
    assert memory.forgejo.head() == before


def test_a_list_the_family_s_topic_of_the_same_id_has_stays_with_it(memory, admin):
    """Given the family's sewing topic and Homer's of the same id, when the
    admin renames Homer's, then their one list stays the family's topic's,
    and the admin is told."""
    answer = _renames(admin, "nahprojekte", "naehprojekte", "--bucket", "homer")

    assert answer["list_kept"] == LIST
    assert memory.page(LIST) == FILES[LIST]
    assert admin.was_told(LIST)


# ── A capture between the rename's read and its commit ──────────────────

def test_a_rename_planned_before_a_capture_landed_is_refused_and_run_again(memory, admin):
    """Given the rename's plan read from the memory, when a capture is
    committed into the old folder before the rename commits, then the
    curator refuses the rename and says to run it again, the stray note
    stays where it is, and the rename run again moves it too.

    The plan is the command's own (`plan` over the memory as the curator
    serves it); the commit is the one the command makes.
    """
    from stacklets.memory.bot.cli.topic import plan
    from stacklets.memory.memory_api import MEMORY_BOT

    planned = plan({f.path: f for f in memory.api.files("")}, bucket="family",
                   shared="family", old="nahprojekte", new="naehprojekte")
    stray = f"{OLD}/notes/2026/10/knopf.md"
    memory.api.commit([put(stray, FILES[PATTERN].replace("note-1a1a1a1a1a", "note-6f6f6f6f6f"),
                           base="")], by="marge", kind="capture", title="Knopf")

    with pytest.raises(Refused, match="Run the rename again"):
        memory.api.commit(planned.changes, by=MEMORY_BOT, kind="rename", title="nahprojekte")
    assert memory.text(stray) and memory.text(TOPIC)

    assert _renames(admin, "nahprojekte", "naehprojekte")["committed"] is True
    assert memory.text(_moved(stray)) and not memory.text(stray)


# ── Renaming back ───────────────────────────────────────────────────────

def test_a_topic_renamed_back_is_its_own_id_again(memory, admin):
    """Given the topic renamed, when the admin renames it back, then it is
    under its first id again, its card lists only the id it had in between,
    and a note can be filed under its first id again."""
    _renames(admin, "nahprojekte", "naehprojekte", "Nähprojekte")

    answer = _renames(admin, "naehprojekte", "nahprojekte", "Nahprojekte")

    assert answer.get("committed") is True, answer
    assert parse(memory.page(TOPIC))["former_topic_ids"] == ["naehprojekte"]
    memory.api.commit([put(f"{OLD}/notes/2026/10/knopf.md",
                           FILES[PATTERN].replace("note-1a1a1a1a1a", "note-6f6f6f6f6f"), base="")],
                      by="marge", kind="capture", title="Knopf")
    assert memory.text(f"{OLD}/notes/2026/10/knopf.md")


# ── A list two people's topics share ─────────────────────────────────────

def test_a_list_another_person_s_topic_shares_stays_and_follows_the_moved_notes(memory, admin):
    """Given Homer's and Marge's own button topics and their one list in the
    family's lists, when the admin renames Homer's, then the list stays
    under its id with Marge's item as it was, Homer's item links his note
    at its new path, and no list item is named as a link left by hand."""
    answer = _renames(admin, "knopf", "knoepfe", "--bucket", "homer")

    assert answer["list_kept"] == BUTTON_LIST
    lst = read_list(memory.page(BUTTON_LIST), path=BUTTON_LIST)
    assert lst.topics == ["knopf"]
    assert [i.links[0].target for i in lst.items] == [
        "homer/knoepfe/notes/2026/10/hemd.md", MARGES_BUTTON]
    assert all(left["path"] != BUTTON_LIST for left in answer["links_left"])


# ── An ontology id ──────────────────────────────────────────────────────

def test_a_topic_is_not_renamed_onto_an_ontology_topic(memory, admin):
    answer = _renames(admin, "nahprojekte", "travel")

    assert "ontology" in answer["error"]
    assert memory.new_commits() == []
