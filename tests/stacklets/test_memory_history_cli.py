"""`stack memory history` — reading back what the family's memory remembers.

The vault is a git repository, so every version and every author is already
recorded. Nothing read it back, which left "what's new this week?", "who
changed Homer's profile?" and "when did this land on the list?" unanswerable
from inside famstack despite the answers sitting on disk.

These tests drive the command against a **real git repository** built in a
temp directory, not against canned `git log` output. The whole value of this
command is that it picks the git incantation that survives a rewritten page,
and a fixture of pre-baked output would agree with whatever incantation the
implementation happened to use. Only a real repo with a real rewrite in its
history can tell the right answer from the confident wrong one.

That rewrite is not hypothetical: the agent split one camping list into two
sections, rewriting all twenty-one lines, and `git blame` on the result
attributes every item to that commit.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


history = _load("memory_cli_history",
                _REPO_ROOT / "stacklets" / "memory" / "cli" / "history.py")


def _git(repo: Path, *argv, **env):
    subprocess.run(["git", "-C", str(repo), *argv], check=True,
                   capture_output=True, text=True)


def _commit(repo: Path, path: str, body: str, *, who: str, subject: str,
            when: str):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", subject,
         "--author", f"{who} <{who}@simpson>", "--date", when],
        check=True, capture_output=True, text=True,
        env={**_ENV, "GIT_COMMITTER_DATE": when},
    )


_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "seed", "GIT_AUTHOR_EMAIL": "seed@simpson",
    "GIT_COMMITTER_NAME": "seed", "GIT_COMMITTER_EMAIL": "seed@simpson",
    "HOME": "/tmp",
}


@pytest.fixture
def vault(tmp_path):
    """A vault whose history contains the rewrite that breaks `git blame`.

    Kühlbox is added early, under no heading. Later the whole page is
    rewritten into two sections, which moves every line. Any reader that
    answers "when was Kühlbox added" by line position will name the
    rewrite; the right answer is the earlier commit.
    """
    repo = tmp_path / "memory" / "vault"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "seed")
    _git(repo, "config", "user.email", "seed@simpson")

    # Profile pages first: they are what tells the command who the family is,
    # and this commit is the machinery's own, which is what the default view
    # exists to keep out.
    for person in ("homer", "marge", "lisa"):
        (repo / person).mkdir()
        (repo / person / "about.md").write_text(f"# {person}\n", encoding="utf-8")
    _commit(repo, "README.md", "# Family memory\n",
            who="stackadmin", subject="chore: seed the vault",
            when="2026-05-01T09:00:00")

    for path, body, who, subject, when in SEEDED:
        _commit(repo, path, body, who=who, subject=subject, when=when)
    return tmp_path


# The seeded history. Subjects are deliberately written in three different
# shapes -- one of them nothing like ours -- because the command must never
# read them. It asks git for the author and date as fields and carries the
# subject through untouched, so how we happen to word a commit today is not
# something reading it tomorrow can depend on.
SEEDED = [
    ("family/camping/todos.md",
     "# Camping\n\n- [ ] Zelt prüfen\n",
     "homer", "chore(memory): homer added 1: Zelt prüfen in camping",
     "2026-06-01T09:00:00"),
    ("family/camping/todos.md",
     "# Camping\n\n- [ ] Zelt prüfen\n- [ ] Kühlbox mitbringen\n",
     "marge", "Kühlbox drauf",                       # a person, in Forgejo
     "2026-06-14T09:00:00"),
    ("homer/about.md",
     "# Homer\n\nWorks at the plant.\n",
     "lisa", "docs(memory): refresh homer",          # the curator's own wording
     "2026-07-02T09:00:00"),
    # The rewrite: same items, every line moved.
    ("family/camping/todos.md",
     "# Camping\n\n## Ausruestung\n\n- [ ] Kühlbox mitbringen\n\n"
     "## Vorbereitung\n\n- [ ] Zelt prüfen\n",
     "marge", "chore(memory): marge moved 2 in camping",
     "2026-08-01T09:00:00"),
]


def _run(vault_root, *args):
    return history.run(list(args), None, {"data_dir": str(vault_root)})


# ── what changed lately ──────────────────────────────────────────────────

def test_recent_changes_come_back_newest_first(vault, capsys):
    """The default question: what has been happening."""
    result = _run(vault)

    assert [c["date"] for c in result["changes"]] == [
        "2026-08-01", "2026-07-02", "2026-06-14", "2026-06-01"]
    assert [c["by"] for c in result["changes"]] == [
        "marge", "lisa", "marge", "homer"]
    assert "2026-08-01" in capsys.readouterr().out


def test_a_commit_subject_is_carried_through_untouched(vault):
    """The command reads git's fields, never the message.

    How a commit is worded is the writer's business and changes over time:
    the vault carries our generated subjects, the curator's "refresh"
    lines, and whatever a person types in Forgejo. A reader that picked
    those apart would break on all three, so it takes the subject as
    opaque text and hands it on exactly as found.
    """
    result = _run(vault)

    assert [c["what"] for c in result["changes"]] == [
        subject for _, _, _, subject, _ in reversed(SEEDED)]


def test_history_is_not_only_about_lists(vault):
    """A profile edit is history too.

    Scoping this to todo lists would have built the narrow version of the
    idea; the vault is mostly pages that are not lists.
    """
    result = _run(vault, "homer")

    assert len(result["changes"]) == 1
    assert result["changes"][0]["by"] == "lisa"
    assert result["changes"][0]["date"] == "2026-07-02"


def test_a_scope_is_whatever_the_family_would_say(vault):
    """"camping" is a topic under `family/`, "homer" is a person at the root.

    The caller says the word; resolving it to a path is this command's job,
    not something to make an agent guess at.
    """
    assert len(_run(vault, "camping")["changes"]) == 3
    assert len(_run(vault, "family/camping")["changes"]) == 3


def test_changes_can_be_narrowed_to_one_person(vault):
    result = _run(vault, "--by", "marge")

    assert len(result["changes"]) == 2
    assert {c["by"] for c in result["changes"]} == {"marge"}


def test_a_time_window_is_git_s_own(vault):
    """Families ask in words ("last week"), and git already parses them."""
    result = _run(vault, "--since", "2026-07-01")

    assert len(result["changes"]) == 2, "only the July and August commits"


# ── whose changes count ──────────────────────────────────────────────────

def test_the_machinery_is_left_out_by_default(vault):
    """A vault's log is mostly not people.

    The curator regenerating pages, the archivist renaming a note it just
    filed, cleanup after a test run. Asked what Homer had been up to, the
    unfiltered log answered "chore: test cleanup t-bfdaba49" and a rename
    by archivist-bot: true, and useless.
    """
    result = _run(vault)

    assert "stackadmin" not in {c["by"] for c in result["changes"]}
    assert len(result["changes"]) == len(SEEDED)


def test_the_machinery_is_there_when_asked_for(vault):
    """Filtered out is not hidden. Debugging the vault needs the rest."""
    result = _run(vault, "--all")

    assert "stackadmin" in {c["by"] for c in result["changes"]}


def test_a_person_with_no_profile_page_is_still_a_person(vault):
    """The reason the machinery is named rather than the family.

    A roster of known people reads better, but a member whose profile has
    not been generated yet would vanish from the history with nothing to
    show why. Bart has captured something and has no page; he is in the
    history all the same.
    """
    _commit(vault / "memory" / "vault", "family/camping/todos.md",
            "# Camping\n\n- [ ] Skateboard\n",
            who="bart", subject="bart was here", when="2026-08-02T09:00:00")

    assert "bart" in {c["by"] for c in _run(vault)["changes"]}


def test_every_kind_of_bot_account_is_machinery(vault):
    """The framework names them all `<slug>-bot`, so the rule is one rule."""
    for bot in ("archivist-bot", "mail-bot", "curator-bot"):
        _commit(vault / "memory" / "vault", f"family/camping/{bot}.md",
                f"# {bot}\n", who=bot, subject=f"rename: {bot} tidied up",
                when="2026-08-02T10:00:00")

    assert not {c["by"] for c in _run(vault)["changes"]} & {
        "archivist-bot", "mail-bot", "curator-bot"}


def test_a_run_of_bot_commits_does_not_crowd_out_the_answer(vault):
    """Filtering happens after reading, so the window has to be generous.

    Twenty consecutive housekeeping commits would otherwise fill a
    ten-row read and leave the family's changes invisible behind them.
    """
    for n in range(20):
        _commit(vault / "memory" / "vault", f"family/camping/noise{n}.md",
                f"# {n}\n", who="archivist-bot", subject=f"chore: housekeeping {n}",
                when="2026-08-02T11:00:00")

    result = _run(vault, "--limit", "5")

    # Every one of the family's changes is still here, sitting behind
    # twenty housekeeping commits that a naive `-n 5` would have returned
    # instead.
    assert len(result["changes"]) == len(SEEDED)
    assert not any(_is_bot(c["by"]) for c in result["changes"])


def _is_bot(name):
    return name.endswith("-bot") or name == "stackadmin"


def test_naming_a_person_overrides_the_roster(vault):
    """`--by` is already an answer to "whose", so it is not second-guessed."""
    result = _run(vault, "--by", "stackadmin")

    assert [c["by"] for c in result["changes"]] == ["stackadmin"]


def test_an_empty_answer_says_which_filter_emptied_it(vault, capsys):
    """Otherwise "no changes" reads as "nothing ever happened here"."""
    _run(vault, "camping", "--by", "bart")

    out = capsys.readouterr().out
    assert "no changes" in out
    assert "in camping" in out and "by bart" in out


def test_an_unknown_scope_is_refused_rather_than_silently_widened(vault):
    """Answering about the whole vault when asked about one topic would be
    a wrong answer wearing a right one's clothes."""
    result = _run(vault, "atlantis")

    assert "atlantis" in result["error"]


# ── when did this arrive ─────────────────────────────────────────────────

def test_when_an_item_arrived_survives_the_page_being_rewritten(vault, capsys):
    """The reason this is a command and not a documented `git blame`.

    Kühlbox was added in June and the page was rewritten in August, moving
    every line. Blame would credit the August rewrite. The honest answer is
    June, and by marge.
    """
    result = _run(vault, "--item", "Kühlbox mitbringen")

    assert result["added"] == "2026-06-14", "the June commit, not the August rewrite"
    assert result["by"] == "marge"
    assert "first appeared 2026-06-14" in capsys.readouterr().out


def test_only_the_arrival_is_claimed_because_only_it_is_knowable(vault, capsys):
    """"Last touched" is not answerable this way, so it is not offered.

    Git leaves an item that a rewrite merely moved sitting in the diff as
    *context*, so it appears in no commit's added or removed lines: here
    the August rewrite reports nothing for Kühlbox, under either `-S` or
    `-G`. A "last touched" that silently means "unless anyone reorganised
    the page" is worse than no answer, so the command claims the arrival
    and stops.
    """
    result = _run(vault, "--item", "Kühlbox mitbringen")

    assert "last touched" not in capsys.readouterr().out
    assert set(result) == {"ok", "item", "added", "by"}


def test_an_unquoted_item_is_still_one_item(vault):
    """The agent reaches this through a socket that splits on shlex.

    "Kühlbox mitbringen" arrives as two words. Searching for "Kühlbox"
    alone would match a different line, or nothing, and neither failure is
    visible to whoever asked.
    """
    result = _run(vault, "--item", "Kühlbox", "mitbringen")

    assert result["added"] == "2026-06-14"


def test_an_item_nobody_ever_wrote_says_so(vault):
    result = _run(vault, "--item", "Schneeketten")

    assert "Schneeketten" in result["error"]


def test_an_item_search_can_be_scoped(vault):
    """Scoping keeps a common word from matching across the whole vault."""
    assert _run(vault, "camping", "--item", "Kühlbox mitbringen")["by"] == "marge"
    assert "error" in _run(vault, "homer", "--item", "Kühlbox mitbringen")


# ── the vault has to exist ───────────────────────────────────────────────

def test_a_vault_that_is_not_a_repository_yet_says_what_to_do(tmp_path):
    (tmp_path / "memory" / "vault").mkdir(parents=True)

    result = history.run([], None, {"data_dir": str(tmp_path)})

    assert "stack up memory" in result["error"]
