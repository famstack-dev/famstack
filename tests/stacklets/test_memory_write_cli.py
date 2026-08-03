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

The `update_memory` seam is substituted, because the real one talks to
Forgejo over the network. The stand-in keeps the two behaviours the command
actually depends on -- the transform is handed the *current* text, and a
transform that raises `ValueError` becomes an error envelope -- since a stub
that dropped either would make these tests agree with nothing.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))


def _load(name: str, path: Path):
    """Import a stacklet CLI module by path, under a name of its own.

    Every stacklet has a `cli` package, so importing this one as `cli.write`
    hands back whichever stacklet got there first in the session. The lane
    runs them all, so it is not the same one twice.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


write_cli = _load("memory_cli_write",
                  _REPO_ROOT / "stacklets" / "memory" / "cli" / "write.py")

PAGE = """# Camping

- [ ] Wetter checken
- [x] Kühlbox mitbringen
"""


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A stand-in for the Forgejo write seam, recording what it was told.

    Mirrors `update_memory`: reads the current text, runs the transform,
    turns a rejected transform into an error envelope, and reports
    `committed=False` when the transform changed nothing.
    """

    class _Store:
        def __init__(self):
            self.page = PAGE
            self.commits = []

        def update_memory(self, config, repo_path, transform, *, actor, message):
            try:
                after = transform(self.page)
            except ValueError as e:          # the transform rejected the input
                return {"error": str(e)}
            if after == self.page:
                return {"ok": True, "committed": False}
            # A caller may describe its own edit only once the transform has
            # met the current text, so the subject may be a function of both.
            subject = message(self.page, after) if callable(message) else message
            self.page = after
            self.commits.append((actor, subject))
            return {"ok": True, "committed": True, "path": repo_path}

        @property
        def last_subject(self):
            return self.commits[-1][1]

    fake = _Store()
    monkeypatch.setattr(write_cli, "update_memory", fake.update_memory)
    return fake


def _run(store, buffer_text, *flags, path="family/camping/todos.md", by="marge",
         tmp=None):
    """Drive the command the way the agent does: payload in a file, flags in argv."""
    buffer_file = tmp / "buffer"
    buffer_file.write_text(buffer_text, encoding="utf-8")
    return write_cli.run([path, "--by", by, "--from", str(buffer_file), *flags],
                         None, {"data_dir": str(tmp)})


# ── replacing a page whole ───────────────────────────────────────────────

def test_a_rewrite_replaces_the_page_and_says_what_it_did(store, tmp_path):
    """The receipt is the point: an opaque rewrite becomes a reviewable one."""
    result = _run(store, PAGE.replace("- [ ] Wetter", "- [x] Wetter"), tmp=tmp_path)

    assert result["committed"] is True
    assert "ticked off" in result["summary"]
    assert "Wetter checken" in result["summary"]
    assert store.page.count("- [x] Wetter checken") == 1


def test_a_rewrite_that_drops_an_item_names_it_in_full(store, tmp_path):
    """The failure this whole path exists to catch.

    An edit that loses items must never render as an ordinary success,
    because the caller relays this sentence to the family verbatim.
    """
    result = _run(store, "# Camping\n\n- [ ] Wetter checken\n", tmp=tmp_path)

    assert result["destructive"] is True
    assert result["removed"] == ["Kühlbox mitbringen"]
    assert result["summary"].startswith("REMOVED"), (
        "a loss has to lead the sentence, not trail it"
    )


# ── patching a page ──────────────────────────────────────────────────────

def _edits(*pairs):
    return json.dumps([{"path": "vault/family/camping/todos.md", "action": "replace",
                        "old_text": old, "new_text": new} for old, new in pairs])


def test_a_patch_changes_only_what_it_names(store, tmp_path):
    """Everything the patch did not mention comes back untouched."""
    result = _run(store, _edits(("- [ ] Wetter checken", "- [x] Wetter checken")),
                  "--patch", tmp=tmp_path)

    assert result["committed"] is True
    assert store.page == PAGE.replace("- [ ] Wetter checken", "- [x] Wetter checken")
    assert "ticked off" in result["summary"]


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
    result = _run(store, _edits(("- [ ] Wetter checken", "- [x] Wetter checken")),
                  "--patch", "--dry-run", tmp=tmp_path)

    assert result["preview"] is True
    assert result["committed"] is False
    assert "ticked off" in result["summary"], "a preview still says what it would do"
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
    _run(store, _edits(("- [ ] Wetter checken", "- [x] Wetter checken")),
         "--patch", tmp=tmp_path)

    assert store.last_subject == (
        "chore(memory): marge ticked off 1: Wetter checken in camping"
    )


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
    store.page = ("# Camping\n\n"
                  + "".join(f"- [ ] Ausruestungsgegenstand Nummer {n}\n"
                            for n in range(1, 6)))
    result = _run(store, "# Camping\n\n- [ ] Ausruestungsgegenstand Nummer 1\n",
                  tmp=tmp_path)

    subject, _, body = store.last_subject.partition("\n\n")
    assert len(subject) <= 72, "the subject line stays readable"
    assert "family/camping/todos.md" in subject
    for gone in result["removed"]:
        assert gone in body, "every lost item survives in the body"


def test_a_path_that_is_not_a_page_is_refused(store, tmp_path):
    result = _run(store, "x", path="family/camping/photo.jpg", tmp=tmp_path)

    assert "not a page" in result["error"]


def test_writing_the_page_it_already_holds_commits_nothing(store, tmp_path):
    """Re-posting an unchanged list must not produce an empty commit."""
    result = _run(store, PAGE, tmp=tmp_path)

    assert result["committed"] is False
    assert store.commits == []
