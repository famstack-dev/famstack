"""`stack memory write` — the seam that lets a caller change a page.

Reading the vault has always been fs-shaped. Writing had no counterpart, so
callers reached for per-item verbs and a model that could describe the right
list perfectly still could not perform twenty string-matched calls to produce
it. This command is the write counterpart: a page in, and a sentence back
saying what that did.

Two shapes go in. The default is the finished page, for a real rewrite. With
`--patch` it is the edit list `apply_patch` produces, applied *here*, against
whatever the store hands back at this instant. That is the difference these
tests exist to pin: on the rig two writers hit one page two seconds apart and
the second silently reverted the first, because a whole-page write cannot
tell that the page moved. A patch can, and must say so rather than guess.

Each write runs the whole way an instance runs it, in this process:
through core's card door to the curator's API, whose card store commits
to the curator's clone and pushes to Forgejo, a bare repository here
(`running_memory`, memory's test instance). `store.page` is the page a test writes:
set it, and the page holds that when the command runs, as Marge left it in
Forgejo; read it, and it is what Forgejo has afterwards.

A new note is not written here at all: it is filed by the capture pipeline
in the bot runner, as a note sent in the chat is. The bot runner is
substituted too (`bot_runner`), for every test, so no test can reach a
container. The stand-in answers with the receipt `stack memory capture`
prints, and records what it was asked to file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


from tests.memory.unit.instance import RunningMemory, running_memory  # noqa: E402
from stack.stacklet_code import load  # noqa: E402
from stack.frontmatter import parse as parse_frontmatter  # noqa: E402

# The capture command's own argument reader, so the stand-in below reads
# what `write` asks for exactly as the real command would.
from stacklets.docs.bot.cli import capture as capture_cli  # noqa: E402

write_cli = load(_REPO_ROOT / "stacklets" / "memory",
                 _REPO_ROOT / "stacklets" / "memory" / "cli" / "write.py")

PAGE = """# Camping

- [ ] Wetter checken
- [x] Kühlbox mitbringen
"""


TODOS = "family/camping/todos.md"
# The subject of the edit that leaves a page as a test begins with it.
_AS_IT_BEGINS = "the page as the test begins"


class ThePage:
    """The page a test writes, in the real memory.

    `page = text` leaves the page holding `text` (`""`: not there) when the
    command runs, as Marge's edit in Forgejo; `page` reads what Forgejo has
    there now. `commits` are the commits since the test began, `(author,
    message)`, without those edits; `last_subject` is the last one's message.
    """

    def __init__(self, memory: RunningMemory):
        self.memory, self.path, self._staged = memory, TODOS, None

    @property
    def page(self) -> str:
        return self._staged if self._staged is not None else self.memory.text(self.path)

    @page.setter
    def page(self, text: str) -> None:
        self._staged = text

    def written_at(self, path: str) -> None:
        """The command is about to write `path`: leave it as the test set it."""
        self.path = path
        if self._staged is None:
            return
        if self._staged:
            self.memory.edited_in_forgejo({path: self._staged}, subject=_AS_IT_BEGINS)
        elif self.memory.text(path):
            self.memory.edited_in_forgejo({}, removed=(path,), subject=_AS_IT_BEGINS)
        self._staged = None

    @property
    def commits(self) -> list[tuple[str, str]]:
        return [(author, message) for _, author, message in self.memory.commits
                if message != _AS_IT_BEGINS]

    @property
    def last_subject(self) -> str:
        return self.commits[-1][1]

    @property
    def mirrored(self) -> bool:
        return self.memory.mirrored

    @mirrored.setter
    def mirrored(self, answer: bool) -> None:
        self.memory.mirrored = answer


@pytest.fixture
def store(tmp_path):
    """The memory as the write command reaches it, holding the camping todos."""
    with running_memory(tmp_path / "instance", {TODOS: PAGE}) as memory:
        yield ThePage(memory)


@pytest.fixture(autouse=True)
def bot_runner(monkeypatch):
    """A stand-in for the bot runner's capture command, recording what it filed.

    Answers as `stack memory capture` does: the receipt, with the path the
    pipeline chose under the bucket it was given and the card id it made.
    `refuses` makes it fail the way `stack.bot_runner.call` fails, with the
    last line the command wrote.
    """

    class _BotRunner:
        def __init__(self):
            self.filed = []          # (words, by, bucket) per capture asked for
            self.refuses = ""

        def call(self, entrypoint, command, *argv, input_text=None):
            assert command == "capture", command
            if self.refuses:
                raise RuntimeError(self.refuses)
            asked = capture_cli.parse_args(list(argv))  # read as the capture command reads it
            words, by = asked.text, asked.sender
            bucket = asked.bucket or by
            self.filed.append((words, by, bucket))
            return ("Captured: Grill needs charcoal\n"
                    f"  vault: {bucket}/notes/2026/10/grill-needs-charcoal-3a338e.md\n"
                    "  card: note-7f3a9c21e4\n"
                    f"  scope: {bucket}\n")

    fake = _BotRunner()
    monkeypatch.setattr(write_cli, "call", fake.call)
    return fake


def _run(store, buffer_text, *flags, path=TODOS, by="marge", tmp=None, home_url=""):
    """Drive the command the way the agent does: payload in a file, flags in argv."""
    store.written_at(path)
    buffer_file = tmp / "buffer"
    buffer_file.write_text(buffer_text, encoding="utf-8")
    config = {**store.memory.config, **({"home_url": home_url} if home_url else {})}
    return write_cli.run([path, "--by", by, "--from", str(buffer_file), *flags], None, config)


# ── replacing a page whole ───────────────────────────────────────────────

def test_a_rewrite_replaces_the_page_and_says_what_it_did(store, tmp_path):
    """The receipt is the point: an opaque rewrite becomes a reviewable one."""
    result = _run(store, PAGE.replace("Wetter checken", "Wetterbericht checken"), tmp=tmp_path)

    assert result["committed"] is True
    assert "reworded" in result["summary"]
    assert "Wetterbericht checken" in result["summary"]
    assert store.page.count("- [ ] Wetterbericht checken") == 1


def test_a_rewrite_that_drops_an_item_is_refused(store, tmp_path):
    """A whole-page rewrite must never lose a list item.

    Removals go through list_edit, so a write that would drop an item is
    refused with the item named, rather than committed and reported. This
    is the guard that makes a restructure safe to hand a model.
    """
    result = _run(store, "# Camping\n\n- [ ] Wetter checken\n", tmp=tmp_path)

    assert "error" in result
    assert "Kühlbox mitbringen" in result["error"]
    assert "list_edit" in result["error"]
    assert store.commits == [], "a refused write commits nothing"


# ── patching a page ──────────────────────────────────────────────────────

def _edits(*pairs):
    return json.dumps([{"path": "vault/family/camping/todos.md", "action": "replace",
                        "old_text": old, "new_text": new} for old, new in pairs])


def test_a_patch_changes_only_what_it_names(store, tmp_path):
    """Everything the patch did not mention comes back untouched."""
    result = _run(store, _edits(("- [ ] Wetter checken", "- [ ] Wetterbericht checken")),
                  "--patch", tmp=tmp_path)

    assert result["committed"] is True
    assert store.page == PAGE.replace("- [ ] Wetter checken", "- [ ] Wetterbericht checken")
    assert "reworded" in result["summary"]


def test_a_patch_is_matched_against_the_page_as_it_is_now(store, tmp_path):
    """Not against the copy the caller read. This is why patches go to the store.

    Somebody else edited the line in between, exactly as the archivist did
    on the rig two seconds before the agent wrote. The patch no longer
    fits, and saying so is what stops their change being reverted.
    """
    store.page = PAGE.replace("- [ ] Wetter checken", "- [x] Wetter checken (Homer)")

    result = _run(store, _edits(("- [ ] Wetter checken", "- [x] Wetter checken")),
                  "--patch", tmp=tmp_path)

    assert "error" in result
    assert "not found" in result["error"]
    assert "family/camping/todos.md" in result["error"], "name the page"
    assert "read it again" in result["error"], "and say how to recover"
    assert store.commits == [], "a patch that does not fit must not commit"


def test_an_ambiguous_patch_is_refused_rather_than_guessed(store, tmp_path):
    """Two identical lines under different headings is an ordinary list."""
    store.page = "## A\n- [ ] Milch\n\n## B\n- [ ] Milch\n"

    result = _run(store, _edits(("- [ ] Milch", "- [x] Milch")), "--patch",
                  tmp=tmp_path)

    assert "more than once" in result["error"]
    assert store.commits == []


def test_a_malformed_patch_is_rejected_before_anything_is_written(store, tmp_path):
    """`--patch` wants JSON; a page body under that flag is a caller bug."""
    result = _run(store, "# Camping\n\n- [ ] Wetter checken\n", "--patch",
                  tmp=tmp_path)

    assert "JSON" in result["error"]
    assert store.commits == []


# ── previewing ───────────────────────────────────────────────────────────

def test_a_preview_reports_the_change_without_making_it(store, tmp_path):
    """The caller is told it can validate without writing, so it must hold.

    A dropped flag turns "show me what this would do" into a change to
    the family's list.
    """
    result = _run(store, _edits(("- [ ] Wetter checken", "- [ ] Wetterbericht checken")),
                  "--patch", "--dry-run", tmp=tmp_path)

    assert result["preview"] is True
    assert result["committed"] is False
    assert "reworded" in result["summary"], "a preview still says what it would do"
    assert store.page == PAGE, "the page must be untouched"
    assert store.commits == []


def test_a_preview_still_refuses_a_patch_that_does_not_fit(store, tmp_path):
    """Otherwise a preview reports a change the real write could not make."""
    store.page = "# Camping\n\n- [x] Kühlbox mitbringen\n"

    result = _run(store, _edits(("- [ ] Wetter checken", "- [x] Wetter checken")),
                  "--patch", "--dry-run", tmp=tmp_path)

    assert "not found" in result["error"]


# ── the ordinary refusals ────────────────────────────────────────────────

# ── what the history ends up saying ──────────────────────────────────────

def test_the_commit_says_what_the_edit_did(store, tmp_path):
    """History is read, by a person in Forgejo and by the agent.

    Two hundred commits all reading "updated todos.md" answer no question
    anyone actually asks. What changed is already known at the moment of
    writing, so it costs nothing to record it where it lasts.
    """
    _run(store, _edits(("- [ ] Wetter checken", "- [ ] Wetterbericht checken")),
         "--patch", tmp=tmp_path)

    assert "reworded 1: Wetter checken -> Wetterbericht checken" in store.last_subject


def test_the_subject_names_the_topic_not_the_path(store, tmp_path):
    """Git already records the file, so the subject must not spend its
    budget saying it twice. Spelling the full path pushed an ordinary
    tick-off past the line limit, which demoted almost every real edit to
    the generic fallback -- the exact outcome this is meant to avoid."""
    store.page = "# Homer\n\nWorks at the plant.\n"
    _run(store, "# Homer\n\nWorks at the plant.\nLikes donuts.\n",
         path="homer/about.md", tmp=tmp_path)

    assert store.last_subject.endswith("in homer")


def test_a_page_that_is_not_a_list_still_says_something_true(store, tmp_path):
    """Lists are one kind of page; the vault is mostly other kinds.

    We cannot describe a profile edit in the family's terms without
    inventing meaning, so it gets the honest general answer instead of a
    confident wrong one.
    """
    store.page = "# Homer\n\nWorks at the plant.\n"
    result = _run(store, "# Homer\n\nWorks at the plant.\nLikes donuts.\n",
                  path="homer/about.md", tmp=tmp_path)

    assert result["summary"] == "changed +1/-0 lines"
    assert store.last_subject == (
        "chore(memory): marge changed +1/-0 lines in homer"
    )


def test_a_long_description_moves_below_the_subject_intact(store, tmp_path):
    """A removal names every item, so it is the case that overflows.

    Truncating would drop exactly the detail worth keeping, so the long
    form moves into the commit body, which git and Forgejo both show.
    """
    items = [f"Ausruestungsgegenstand Nummer {n}" for n in range(1, 6)]
    store.page = "# Camping\n\n" + "".join(f"- [ ] {item}\n" for item in items)
    _run(store, _edits(*[(f"- [ ] {item}\n", "") for item in items]), "--patch", tmp=tmp_path)

    subject, _, body = store.last_subject.partition("\n\n")
    assert len(subject) <= 72, "the subject line stays readable"
    assert TODOS in subject
    assert all(item in body for item in items), "every lost item survives in the body"


def test_a_path_that_is_not_a_page_is_refused(store, tmp_path):
    result = _run(store, "x", path="family/camping/photo.jpg", tmp=tmp_path)

    assert "not a page" in result["error"]


@pytest.mark.parametrize("page", ["family/diary/2026/10.md", "family/diary/2026/about.md",
                                  "family/diary/about.md"])
def test_a_page_the_diary_builds_is_refused(store, tmp_path, page):
    """The diary builds these from its entries, in the wiki's copy of the vault.

    A write puts a page at the same path in the family's own files, and the
    build skips a page it did not make, so the diary's month is gone for
    good. Asked for a calendar entry it had no place for, the agent did
    exactly that.
    """
    result = _run(store, "---\ntitle: Oktober 2026\n---\n\n# Termine\n", path=page, tmp=tmp_path)

    assert "the diary builds" in result["error"]
    assert "list_edit" in result["error"]
    assert store.commits == []


def test_a_diary_entry_is_still_written(store, tmp_path):
    """An entry is the family's own card; a correction to it is a write like any other."""
    store.page = ("---\ntype: diary\ncard_id: diary-3f9a0c1b2d\ntitle: Am See\ndate: \"2026-10-12\"\n"
                  "entry_id: 3f9a0c1b2d\nevent_ids:\n  - $AmSee1\n"
                  "timestamp: \"2026-10-12T15:00:00.000Z\"\n---\n\nWir waren schwimmen.\n")
    result = _run(store, store.page.replace("schwimmen", "baden"),
                  path="family/diary/entries/2026/10/2026-10-12-am-see.md", tmp=tmp_path)

    assert result.get("committed") is True, result


def test_writing_the_page_it_already_holds_commits_nothing(store, tmp_path):
    """Re-posting an unchanged list must not produce an empty commit."""
    result = _run(store, PAGE, tmp=tmp_path)

    assert result["committed"] is False
    assert store.commits == []


# ── saying whether the change is visible yet ─────────────────────────────
#
# Forgejo has the commit the instant `update_memory` returns, but the
# agent reads a mount and the family reads Quartz, and both are fed by
# the curator. When the curator has not caught up yet the caller has to
# hear that as a delay, never as a failure — a model told anything less
# than "done" re-runs the edit, and a re-run edit is a duplicate.

def test_a_write_the_readers_already_show_reports_plainly(store, tmp_path, capsys):
    store.mirrored = True
    _run(store, PAGE.replace("- [ ] Wetter", "- [x] Wetter"), tmp=tmp_path)

    assert "catch up" not in capsys.readouterr().out


def test_a_write_the_readers_have_not_caught_up_with_is_still_done(store, tmp_path,
                                                                   capsys):
    store.mirrored = False
    result = _run(store, PAGE.replace("Wetter checken", "Wetterbericht checken"), tmp=tmp_path)

    assert result["committed"] is True, "the commit landed; only the view lags"
    assert result["mirrored"] is False
    out = capsys.readouterr().out
    assert "Wrote" in out and "catch up" in out


# The reply to an edit cites the page it touched. A note moves when it is
# re-scoped or refiled, so its link is keyed by the card id in its
# frontmatter, the same `/go/capture/<id>` the filing reply posted.

NOTE = """---
type: note
card_id: note-3a338e9c0d
title: Campsite booked
---

Lake Springfield, pitch 12.
"""


def test_an_edited_note_answers_with_its_card_id_link(store, tmp_path, capsys):
    store.page = NOTE
    _run(store, NOTE.replace("pitch 12", "pitch 14"), path="homer/notes/2026/08/campsite-3a338e.md",
         by="homer", tmp=tmp_path, home_url="https://home.example.org")

    out = capsys.readouterr().out
    assert "https://home.example.org/go/note-3a338e9c0d" in out
    assert "campsite" not in out.split("/go/", 1)[1]



def test_a_page_without_a_stable_link_answers_with_no_link(store, tmp_path, capsys):
    """A page written by hand, with no frontmatter, has no card id yet and
    no name route. The reply names the page and stops; the bare `/go`
    address opens nothing."""
    page = "# Campsite invoice\n\nLake Springfield, pitch 12.\n"
    store.page = page
    _run(store, page.replace("pitch 12", "pitch 14"), path="family/documents/2026/08/campsite-invoice.md",
         by="homer", tmp=tmp_path, home_url="https://home.example.org")

    out = capsys.readouterr().out
    assert "Wrote family/documents/2026/08/campsite-invoice.md" in out
    assert "/go" not in out

# ── person files ─────────────────────────────────────────────────────────

def test_a_person_file_the_readers_would_skip_is_refused(store, tmp_path):
    """Every reader leaves out a broken person file; the write says so first.

    `aliases: Margaret` is one name where the format asks for a list
    (`schemas/cards/person.json`).
    """
    result = _run(store, "---\ntype: person\ntitle: Maggie Simpson\naliases: Margaret\n---\n",
                  path="family/people/maggie.md", tmp=tmp_path)

    assert "error" in result
    assert "`aliases` must be a list" in result["error"]
    assert store.commits == []


def test_a_person_file_in_the_format_is_written(store, tmp_path):
    result = _run(store, "---\ntype: person\ntitle: Maggie Simpson\naliases:\n"
                         "  - Margaret\n---\n", path="family/people/maggie.md", tmp=tmp_path)

    assert result["committed"] is True


def test_a_list_file_is_guarded_like_a_todo_page(store, tmp_path):
    """Lists under `lists/` hold ticks too, so a rewrite may not drop one."""
    store.page = PAGE
    result = _run(store, "# Camping\n\n- [ ] Wetter checken\n",
                  path="family/lists/camping.md", tmp=tmp_path)
    assert "Kühlbox mitbringen" in result["error"]


def test_adding_or_ticking_items_through_a_page_write_is_refused(store, tmp_path):
    """Item changes go through list_edit, which keeps sections and finds duplicates.

    An agent that rewrote the page to add groceries put one item on the list
    twice and another under the wrong section.
    """
    added = _run(store, PAGE + "- [ ] Joghurt\n", tmp=tmp_path)
    ticked = _run(store, _edits(("- [ ] Wetter checken", "- [x] Wetter checken")),
                  "--patch", tmp=tmp_path)

    assert "Joghurt" in added["error"] and "list_edit" in added["error"]
    assert "Wetter checken" in ticked["error"]
    assert store.commits == []



# ── the card id ──────────────────────────────────────────────────────────

def test_a_new_page_gets_a_card_id_of_its_own(store, tmp_path):
    """A page written by hand says nothing of what it is: it is a note (ADR-017)."""
    store.page = ""
    _run(store, "# Ideas for Lisa's birthday\n\n- a saxophone reed\n",
         path="marge/lisa-birthday.md", tmp=tmp_path)
    fields = parse_frontmatter(store.page)
    assert (fields["type"], fields["title"]) == ("note", "Ideas for Lisa's birthday")
    assert re.fullmatch(r"note-[0-9a-f]{10}", fields["card_id"])
    assert store.page.endswith("---\n# Ideas for Lisa's birthday\n\n- a saxophone reed\n")


def test_a_rewrite_that_drops_the_card_id_gets_it_back(store, tmp_path):
    """The id is made once; links and the chat name the card by it."""
    store.page = "---\ntype: list\ncard_id: list-camping\ntitle: Camping\n---\n\n- [ ] Zelt\n"
    _run(store, "---\ntype: list\ntitle: Camping trip\n---\n\n- [ ] Zelt\n",
         path="family/lists/camping.md", tmp=tmp_path)
    assert store.page == "---\ntype: list\ncard_id: list-camping\ntitle: Camping trip\n---\n\n- [ ] Zelt\n"


def test_a_page_already_there_without_an_id_is_written_as_it_comes(store, tmp_path):
    """The curator gives it its id; the write changes only what it says."""
    store.page = "# Camping\n\nThe big tent leaks.\n"
    _run(store, "# Camping\n\nThe big tent leaks. Bring the tarp.\n",
         path="marge/camping.md", tmp=tmp_path)
    assert store.page == "# Camping\n\nThe big tent leaks. Bring the tarp.\n"


# ── a new note ───────────────────────────────────────────────────────────
#
# A note written into the memory is filed as one sent in the chat is: by the
# capture pipeline, which writes the clean version, the quote of the words
# under `## Sources` and the sidecar (ADR-018). Writing the page as given
# would make a note with no sources, which a refile cannot read again.

GRILL = "the grill needs new charcoal before saturday"


def test_a_new_note_is_filed_by_the_capture_pipeline(store, bot_runner, tmp_path, capsys):
    """Given no page at `homer/notes/grill.md`, when Homer writes his words
    there, then they are filed as his note in his bucket, at the path the
    pipeline chooses, and the answer names the card id and that path.

    The page is not written as given: the pipeline's commit is the only one.
    """
    store.page = ""

    result = _run(store, GRILL + "\n", path="homer/notes/grill.md", by="homer", tmp=tmp_path)

    assert bot_runner.filed == [(GRILL, "homer", "homer")]
    assert result["card_id"] == "note-7f3a9c21e4"
    assert result["path"] == "homer/notes/2026/10/grill-needs-charcoal-3a338e.md"
    assert store.commits == [] and store.page == "", "nothing written by the page write"
    out = capsys.readouterr().out
    assert "note-7f3a9c21e4" in out
    assert "homer/notes/2026/10/grill-needs-charcoal-3a338e.md" in out


def test_a_new_note_in_a_topic_folder_is_filed_into_the_topic(store, bot_runner, tmp_path):
    """The folder the notes are in is the bucket, as a topic room's is."""
    store.page = ""

    result = _run(store, "the tent pole is bent\n", path="family/camping/notes/tent.md",
                  by="marge", tmp=tmp_path)

    assert bot_runner.filed == [("the tent pole is bent", "marge", "family/camping")]
    assert result["path"].startswith("family/camping/notes/")


def test_a_new_note_files_its_words_not_its_frontmatter(store, bot_runner, tmp_path):
    """The words are what is filed; the pipeline makes the title and the
    fields, as it does for a chat message, and takes no title from the caller."""
    store.page = ""

    _run(store, f"---\ntitle: Grill\ntype: note\n---\n\n{GRILL}\n",
         path="homer/notes/grill.md", by="homer", tmp=tmp_path)

    assert bot_runner.filed == [(GRILL, "homer", "homer")]


def test_a_new_page_of_another_type_is_written_as_today(store, bot_runner, tmp_path):
    """Only a note is filed. A page that says it is something else is the
    caller's page, written as given."""
    store.page = ""

    result = _run(store, "---\ntype: list\ntitle: Grill\n---\n\n- [ ] Charcoal\n",
                  path="homer/notes/grill.md", by="homer", tmp=tmp_path)

    assert bot_runner.filed == []
    assert result["committed"] is True
    assert parse_frontmatter(store.page)["type"] == "list"


def test_a_new_page_outside_a_notes_folder_is_written_as_today(store, bot_runner, tmp_path):
    store.page = ""

    _run(store, "# Ideas for Lisa's birthday\n\n- a saxophone reed\n",
         path="marge/lisa-birthday.md", tmp=tmp_path)

    assert bot_runner.filed == []
    assert store.commits[-1][1].startswith("chore(memory): marge")


def test_an_existing_note_is_changed_as_today(store, bot_runner, tmp_path):
    """Somebody editing a note changes that file, with today's subject."""
    store.page = NOTE

    result = _run(store, NOTE.replace("pitch 12", "pitch 14"),
                  path="homer/notes/2026/08/campsite-3a338e.md", by="homer", tmp=tmp_path)

    assert bot_runner.filed == []
    assert result["committed"] is True
    assert store.last_subject == "chore(memory): homer changed +1/-1 lines in 08"


def test_a_patch_to_a_missing_note_is_not_filed(store, bot_runner, tmp_path):
    store.page = ""

    result = _run(store, _edits(("old words", "new words")), "--patch",
                  path="homer/notes/grill.md", by="homer", tmp=tmp_path)

    assert bot_runner.filed == []
    assert "not found" in result["error"]


def test_a_preview_of_a_new_note_files_nothing(store, bot_runner, tmp_path):
    """A preview cannot ask the pipeline: filing is the only thing it does."""
    store.page = ""

    result = _run(store, GRILL + "\n", "--dry-run", path="homer/notes/grill.md", by="homer",
                  tmp=tmp_path)

    assert bot_runner.filed == []
    assert result["preview"] is True and result["committed"] is False
    assert store.commits == []


@pytest.mark.parametrize("refusal", [
    "stack-core-bot-runner is not running, bring core up first: stack up core",
    "capture in stack-core-bot-runner failed (exit 1): Nothing captured: "
    "the vault is not reachable (is the code stacklet up?).",
])
def test_a_note_the_pipeline_cannot_file_is_not_written_another_way(store, bot_runner,
                                                                      tmp_path, refusal):
    """Given the bot runner down, or a pipeline that refuses, the command
    fails with that message and the page is not written as given instead:
    a plain file would be a note without its sources, reported as filed."""
    store.page = ""
    bot_runner.refuses = refusal

    result = _run(store, GRILL + "\n", path="homer/notes/grill.md", by="homer", tmp=tmp_path)

    assert refusal in result["error"]
    assert store.commits == [] and store.page == ""
