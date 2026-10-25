"""`stack memory reprocess`: refile one note, previewed first.

A preview writes nothing to the family memory and keeps its proposal
beside it; an apply commits exactly the previewed bytes as one commit
that names the commit it replaced, or refuses when the card or the memory
moved. The card browser builds on the JSON, so its shape is pinned here.

The command runs the whole way an instance runs it, in this process:
through core's card door to the curator's API, whose card store reads the
card, its sidecar and its history from the curator's clone and commits the
refile there, pushing it to Forgejo, a bare repository here
(`running_memory`, memory's test instance). One seam is substituted, at an external
boundary: the bot runner, where the model is (`call`).
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))


from stack.stacklet_code import load  # noqa: E402


# Afresh, as `./stack memory reprocess` loads it (`stack.stacklet_code`).
reprocess = load(_REPO_ROOT / "stacklets" / "memory",
                 _REPO_ROOT / "stacklets" / "memory" / "cli" / "reprocess.py")

from tests.memory.unit.instance import TOKEN, RunningMemory, running_memory  # noqa: E402

PATH = "family/itchy-scratchy-land/notes/2026/10/trip-3a338e.md"
SIDE = PATH.removesuffix(".md") + ".json"
NOTE = """---
type: note
card_id: note-7f3a9c21e4
title: Trip
date: "2026-10-20"
tags:
  - itchy-scratchy-land
---

# Trip

> [!summary]
> Homer will call the hotel.

## Sources

> homer calls the hotel thu, budget max 200
"""
ORIGINAL = {"role": "original", "person": "marge", "time": "2026-10-20T08:00:00+00:00",
            "channel": "chat", "kind": "text", "id": "$n:simpson"}
SIDECAR = json.dumps({"card_id": "note-7f3a9c21e4", "filed_by": "marge", "sources": [ORIGINAL],
                      "made": {"at": "2026-10-20T08:00:05+00:00", "model": "old"}}, indent=2) + "\n"
PROPOSED = NOTE.replace("""> [!summary]
> Homer will call the hotel.
""", "Homer calls the hotel on Thursday. Budget: at most 200.\n").replace(
    "title: Trip", "title: Trip to the hotel")
PROPOSED_SIDECAR = SIDECAR.replace('"model": "old"', '"model": "new"')
# The same note as it was filed before ADR-018.
OLD_NOTE = """---
type: note
card_id: note-7f3a9c21e4
title: Trip
filed_by: marge
capture_id: "$n:simpson"
---

# Trip

> [!quote]- Original paste
> homer calls the hotel thu, budget max 200
"""

# What the model half says it read (`stack.cards.sources`).
SOURCES = [{**ORIGINAL, "quote": "homer calls the hotel thu, budget max 200"}]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
         "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
        capture_output=True, text=True, check=True).stdout.strip()


class _Memory:
    """The family memory as reprocess reaches it, and as a test reads it back.

    `vault` is the curator's clone, `forgejo` Forgejo's repository, `filed`
    the commit that filed the note. `change` is a commit in Forgejo by a
    person or the archivist, which the curator pulls as Forgejo's push
    notice has it do; `puts` the paths of each commit the curator wrote.
    """

    def __init__(self, host: RunningMemory):
        self.host, self.vault, self.forgejo = host, host.vault, host.forgejo.repo
        self.data_dir = Path(host.config["data_dir"])
        self.filed = _git(self.forgejo, "rev-parse", "main")
        self.config = host.config

    def change(self, path: str, text: str, subject: str, author: str = "marge") -> str:
        self.host.edited_in_forgejo({path: text}, by=author, subject=subject)
        return _git(self.forgejo, "rev-parse", "main")

    def pull(self) -> None:
        """The curator's cycle, pulling what Forgejo has."""
        self.host.store.sync()

    def log(self, *args: str) -> str:
        return _git(self.vault, "log", *args)

    @property
    def puts(self) -> list[list[str]]:
        return self.host.written


def _memory(open_memory, monkeypatch, files=None, *, propose=None):
    """The memory holding `files` (the note and its sidecar), and the bot
    runner's model half standing in: it records what it was asked and
    answers with the proposal, or with `propose(asked)`."""
    memory = _Memory(open_memory(files or {PATH: NOTE, SIDE: SIDECAR}))
    memory.asked = []

    def call(entrypoint, command, *argv, input_text=None):
        asked = json.loads(input_text)
        memory.asked.append(asked)
        return json.dumps(propose(asked) if propose else {
            "proposed": PROPOSED, "proposed_sidecar": PROPOSED_SIDECAR,
            "title": "Trip to the hotel", "clean_rejected": "", "sources": SOURCES})

    memory.call = call
    monkeypatch.setattr(reprocess, "call", call)
    return memory


@pytest.fixture
def open_memory(tmp_path):
    """Opens the memory with the files a test names; closed after the test.
    The note is filed by Marge through the archivist, as `capture: Trip`."""
    with contextlib.ExitStack() as opened:
        yield lambda files: opened.enter_context(running_memory(
            tmp_path / "data", files, seeded_by="marge", seeded_as="capture: Trip"))


@pytest.fixture
def memory(open_memory, monkeypatch):
    return _memory(open_memory, monkeypatch)


def _run(memory, *args):
    return reprocess.run(list(args), None, memory.config)


class TestAPreviewWritesNothing:

    def test_the_memory_is_as_it_was(self, memory):
        _run(memory, "note-7f3a9c21e4")
        assert memory.log("-1", "--format=%H") == memory.filed
        assert (memory.vault / PATH).read_text() == NOTE
        assert (memory.vault / SIDE).read_text() == SIDECAR
        assert _git(memory.vault, "status", "--porcelain") == ""

    def test_it_shows_the_card_now_and_as_proposed_with_the_diff(self, memory):
        result = _run(memory, "note-7f3a9c21e4")
        assert (result["current"]["file"], result["current"]["sidecar"]) == (NOTE, SIDECAR)
        assert (result["proposed"]["file"], result["proposed"]["sidecar"]) == (
            PROPOSED, PROPOSED_SIDECAR)
        assert result["current"]["rendered"].startswith("# Trip\n")
        assert result["proposed"]["rendered"].startswith("# Trip to the hotel\n")
        assert "-> Homer will call the hotel." in result["diff"]
        assert "+Homer calls the hotel on Thursday. Budget: at most 200." in result["diff"]
        assert '+    "model": "new"' in result["diff"], "the sidecar's change is shown too"
        assert result["proposal_id"].startswith("rp-")
        assert (result["card_id"], result["path"]) == ("note-7f3a9c21e4", PATH)

    def test_it_shows_what_the_model_read(self, memory, capsys):
        assert _run(memory, "note-7f3a9c21e4")["sources"] == SOURCES
        assert ("original by marge 2026-10-20T08:00:00+00:00 (chat, text): homer calls"
                in capsys.readouterr().out)

    def test_the_proposal_is_kept_outside_the_memorys_git_tree(self, memory):
        result = _run(memory, "note-7f3a9c21e4")
        kept = list(memory.data_dir.rglob(f"{result['proposal_id']}.json"))
        assert kept and memory.vault not in kept[0].parents

    def test_the_model_is_given_the_card_and_the_correction(self, memory):
        _run(memory, "note-7f3a9c21e4", "--msg", "it is Friday", "--by", "@marge:simpson")
        assert memory.asked == [{"path": PATH, "text": NOTE, "sidecar": SIDECAR,
                                 "msg": "it is Friday", "by": "@marge:simpson",
                                 "prior": "", "edits": [], "ops": [],
                                 "quotes_as_sent": {}, "quote_edits": []}]


class TestAnApplyCommitsWhatWasPreviewed:

    def test_the_file_is_the_previewed_bytes_in_one_commit(self, memory):
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        result = _run(memory, "--apply", proposal)
        assert result["ok"] and result["replaces"] == memory.filed
        assert (memory.vault / PATH).read_text() == PROPOSED
        assert (memory.vault / SIDE).read_text() == PROPOSED_SIDECAR
        assert memory.puts == [sorted([PATH, SIDE])], "card and sidecar in one commit"
        assert memory.log("--format=%P", "-1") == memory.filed, "one commit on top"
        assert memory.log("--format=%s|%an", "-1") == "reprocess: Trip to the hotel|memory-bot"
        assert f"Replaces: {memory.filed}" in memory.log("--format=%b", "-1")
        assert len(memory.asked) == 1, "no second model call"

    def test_one_git_revert_undoes_it(self, memory):
        result = _run(memory, "--apply", _run(memory, "note-7f3a9c21e4")["proposal_id"])
        _git(memory.vault, "revert", "--no-edit", result["commit"])
        assert (memory.vault / PATH).read_text() == NOTE
        assert (memory.vault / SIDE).read_text() == SIDECAR

    def test_a_correction_is_the_persons_commit(self, memory):
        proposal = _run(memory, "note-7f3a9c21e4", "--msg", "it is Friday", "--by", "homer")
        _run(memory, "--apply", proposal["proposal_id"])
        assert memory.log("--format=%an", "-1") == "homer"
        body = memory.log("--format=%b", "-1")
        assert "it is Friday" in body

    def test_a_note_in_the_older_format_gets_its_sidecar(self, open_memory, monkeypatch):
        memory = _memory(open_memory, monkeypatch, {PATH: OLD_NOTE})
        result = _run(memory, "--apply", _run(memory, "note-7f3a9c21e4")["proposal_id"])
        assert result["ok"]
        assert memory.puts == [sorted([PATH, SIDE])]
        assert (memory.vault / SIDE).read_text() == PROPOSED_SIDECAR

    def test_a_proposal_is_applied_once(self, memory):
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        _run(memory, "--apply", proposal)
        assert _run(memory, "--apply", proposal)["refusal"] == "no-proposal"


class TestAnApplyRefusesWhenTheMemoryMoved:

    def test_the_proposal_is_based_on_the_versions_forgejo_had(self, memory):
        result = _run(memory, "note-7f3a9c21e4")
        stored = json.loads(next(memory.data_dir.rglob(f"{result['proposal_id']}.json")).read_text())
        assert stored["base_sha"] == _git(memory.forgejo, "rev-parse", f"main:{PATH}")
        assert stored["sidecar_sha"] == _git(memory.forgejo, "rev-parse", f"main:{SIDE}")

    def test_the_sidecar_changed_after_the_preview(self, memory):
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        memory.change(SIDE, SIDECAR.replace("marge", "homer", 1), "reply: Trip")
        memory.pull()
        assert _run(memory, "--apply", proposal)["refusal"] == "changed"
        assert memory.puts == []

    def test_the_card_changed_after_the_preview(self, memory):
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        memory.change(PATH, NOTE.replace("Trip", "Hotel"), "update: Hotel")
        memory.pull()
        result = _run(memory, "--apply", proposal)
        assert result["refusal"] == "changed"
        assert memory.puts == [], "nothing written"
        assert (memory.vault / PATH).read_text() != PROPOSED

    def test_another_card_filed_meanwhile_does_not_stop_it(self, memory):
        """Only the note's own files decide: a filing elsewhere in the memory
        after the preview is joined, and the refile goes on top of it."""
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        other = memory.change("family/other.md", "x\n", "capture: other")
        result = _run(memory, "--apply", proposal)
        assert result["ok"]
        assert memory.log("--format=%P", "-1") == other
        assert (memory.vault / "family/other.md").read_text() == "x\n"

    def test_an_edit_made_in_the_clone_and_never_committed_is_not_overwritten(self, memory):
        """The curator's clone is no place to edit; an edit made there anyway
        is somebody's work, and the refile is refused rather than destroy it."""
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        (memory.vault / PATH).write_text(NOTE + "a hand edit\n")
        result = _run(memory, "--apply", proposal)
        assert result["refusal"] == "write-failed"
        assert PATH in result["error"] and "never committed" in result["error"]
        assert (memory.vault / PATH).read_text() == NOTE + "a hand edit\n"

    def test_an_unknown_proposal(self, memory):
        assert _run(memory, "--apply", "rp-000000000000")["refusal"] == "no-proposal"


class TestANoteSomebodyEditedIsRefiled:
    """A person's edit is read from the card's history and kept through the
    refile (ADR-018 §7), so a note somebody changed by hand is refiled, not
    refused."""

    def test_a_text_edit_goes_to_the_model_with_who_made_it(self, memory):
        edited = NOTE.replace("> Homer will call the hotel.", "Homer calls the hotel on Friday.")
        memory.change(PATH, edited, f"Update '{PATH}'", author="homer")
        memory.pull()
        result = _run(memory, "note-7f3a9c21e4")
        assert "proposal_id" in result, result
        [asked] = memory.asked
        assert asked["edits"] == [{"person": "homer", "at": asked["edits"][0]["at"], "changes": [
            ["> Homer will call the hotel.", "Homer calls the hotel on Friday."]]}]

    def test_a_note_whose_sidecar_somebody_changed_by_hand(self, memory):
        memory.change(SIDE, SIDECAR.replace("marge", "homer", 1), "Update sidecar", author="homer")
        memory.pull()
        assert "proposal_id" in _run(memory, "note-7f3a9c21e4")


class TestWhatIsNotRefiled:

    def test_a_card_that_is_not_a_note(self, memory):
        memory.change("family/bookmarks/tent.md", NOTE.replace("type: note", "type: bookmark")
                      .replace("note-7f3a9c21e4", "bookmark-0b6e2d91aa"), "capture: Tent")
        memory.pull()
        assert _run(memory, "bookmark-0b6e2d91aa")["refusal"] == "not-a-note"

    def test_a_note_written_in_the_memory_without_sources(self, memory):
        memory.change("family/notes/own.md", "---\ntype: note\ncard_id: note-0b6e2d91aa\n"
                      "title: Own\n---\n\n# Own\n\nMy words.\n", "capture: own")
        memory.pull()
        assert _run(memory, "note-0b6e2d91aa")["refusal"] == "no-sources"


    def test_a_cards_migration_is_no_hand_edit(self, memory):
        memory.change(PATH, NOTE + "\n", "cards: give 1 card(s) their kind", author="memory-bot")
        memory.pull()
        assert "proposal_id" in _run(memory, "note-7f3a9c21e4")

    def test_a_note_the_model_half_refuses(self, memory, monkeypatch):
        monkeypatch.setattr(reprocess, "call", lambda *a, **k: json.dumps(
            {"refused": "the note keeps no words of its own to refile it from"}))
        result = _run(memory, "note-7f3a9c21e4")
        assert result == {"error": "the note keeps no words of its own to refile it from",
                          "refusal": "words"}

    def test_an_id_no_card_carries(self, memory):
        assert _run(memory, "note-0000000000")["refusal"] == "no-card"


class TestTheCommandThroughTheStack:
    """`./stack memory reprocess` loads the command from its file through
    the framework's plugin loader (`Stack.run_cli_command`), not as an
    import, so what the module does while it loads (its dataclasses) has
    to work there too."""

    def test_a_preview_runs_through_the_plugin_loader(self, memory, tmp_path, monkeypatch):
        from stack import Stack
        from stack.output import CollectorOutput

        port = memory.config["stack"]["core"]["cards_port"]
        (tmp_path / "stack.toml").write_text(f'[core]\ndomain = ""\ndata_dir = "{memory.data_dir}"\n'
                                             f'cards_port = {port}\n')
        (tmp_path / "stacklets").mkdir()
        (tmp_path / "stacklets" / "memory").symlink_to(_REPO_ROOT / "stacklets" / "memory")
        stck = Stack(root=tmp_path, data=memory.data_dir, output=CollectorOutput())
        stck.secrets.set("memory", "CARDS_WRITE_TOKEN", TOKEN)
        # The bot runner, stood in where every import reaches it.
        monkeypatch.setattr("stack.bot_runner.call", memory.call)

        result = stck.run_cli_command("memory", "reprocess", ["note-7f3a9c21e4"])

        assert "error" not in result, result
        assert result["proposal_id"].startswith("rp-")
        assert result["proposed"]["file"] == PROPOSED


class TestTheCommandLine:

    def test_apply_with_a_card_is_refused_naming_both(self, memory):
        error = _run(memory, "--apply", "rp-1", "note-7f3a9c21e4")["error"]
        assert "--apply" in error and "card" in error

    def test_a_message_needs_its_author(self, memory):
        assert "--by" in _run(memory, "note-7f3a9c21e4", "--msg", "it is Friday")["error"]


class TestARefileIsTheCardItIsAbout:
    """A refile changes what a card says, never which card it is: the apply
    refuses a proposal whose card or sidecar carries another card's id, and
    writes nothing. The model half composes the files, so a mistake there
    must not move one card's words into another."""

    @staticmethod
    def _proposing(open_memory, monkeypatch, *, text=PROPOSED, sidecar=PROPOSED_SIDECAR):
        return _memory(open_memory, monkeypatch, propose=lambda asked: {
            "proposed": text, "proposed_sidecar": sidecar, "title": "Trip to the hotel",
            "clean_rejected": "", "sources": SOURCES})

    def test_a_card_that_carries_another_id_is_refused(self, open_memory, monkeypatch):
        memory = self._proposing(open_memory, monkeypatch,
                                 text=PROPOSED.replace("note-7f3a9c21e4", "note-0b6e2d91aa"))
        result = _run(memory, "--apply", _run(memory, "note-7f3a9c21e4")["proposal_id"])
        assert "note-0b6e2d91aa" in result["error"]
        assert memory.puts == []

    def test_a_sidecar_of_another_card_is_refused(self, open_memory, monkeypatch):
        memory = self._proposing(open_memory, monkeypatch,
                                 sidecar=PROPOSED_SIDECAR.replace("note-7f3a9c21e4", "note-0b6e2d91aa"))
        result = _run(memory, "--apply", _run(memory, "note-7f3a9c21e4")["proposal_id"])
        assert "note-0b6e2d91aa" in result["error"]
        assert memory.puts == []
