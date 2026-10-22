"""`stack memory reprocess`: refile one note, previewed first.

A preview writes nothing to the family memory and keeps its proposal
beside it; an apply commits exactly the previewed bytes as one commit
that names the commit it replaced, or refuses when the card or the memory
moved. The card browser builds on the JSON, so its shape is pinned here.

The memory is a real git clone of a bare repository standing in for
Forgejo. Two seams are substituted, both at an external boundary: the bot
runner, where the model is (`call`), and Forgejo's file API, whose stand-in
answers from the bare repository with git's own blob hashes and refuses a
write against a hash the file is no longer at, as Forgejo does. The write
itself goes through `write_card` and `update_memory` as on an instance.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))


def _load(name: str, path: Path):
    """Import a stacklet CLI module by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


reprocess = _load("memory_cli_reprocess",
                  _REPO_ROOT / "stacklets" / "memory" / "cli" / "reprocess.py")
memory_lib = sys.modules["lib"]

from stack.forgejo import ForgejoClient, ForgejoError  # noqa: E402

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

# What the model half says it read (`memory.note_card`).
SOURCES = [{**ORIGINAL, "quote": "homer calls the hotel thu, budget max 200"}]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
         "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
        capture_output=True, text=True, check=True).stdout.strip()


def _commit(repo: Path, files: dict[str, str], subject: str, author: str = "marge",
            email: str | None = None) -> str:
    """One commit of `files` (path to text) in `repo`, pushed."""
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
        _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", subject, f"--author={author} <{email or f'{author}@simpson'}>")
    _git(repo, "push", "-q", "origin", "HEAD")
    return _git(repo, "rev-parse", "HEAD")


class _Memory:
    """The family memory: Forgejo's repository, and the local clone the CLI reads."""

    def __init__(self, tmp: Path, files: dict[str, str] | None = None):
        self.data_dir = tmp / "data"
        self.origin, self.vault, self.forgejo = tmp / "origin.git", tmp / "data" / "memory" / "vault", tmp / "forgejo"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.forgejo)], check=True)
        self.filed = _commit(self.forgejo, files or {PATH: NOTE, SIDE: SIDECAR}, "capture: Trip")
        self.vault.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.vault)], check=True)

    def change(self, path: str, text: str, subject: str, author: str = "marge") -> str:
        """A commit in Forgejo, by the archivist or a person; the local clone is not pulled."""
        _git(self.forgejo, "pull", "-q", "--ff-only")
        return _commit(self.forgejo, {path: text}, subject, author)

    def pull(self) -> None:
        _git(self.vault, "pull", "-q", "--ff-only")

    def log(self, *args: str) -> str:
        return _git(self.vault, "log", *args)


def _memory(tmp_path, monkeypatch, files=None):
    memory = _Memory(tmp_path, files)
    memory.asked = []

    def call(entrypoint, command, *argv, input_text=None):
        memory.asked.append(json.loads(input_text))
        return json.dumps({"proposed": PROPOSED, "proposed_sidecar": PROPOSED_SIDECAR,
                           "title": "Trip to the hotel", "clean_rejected": "", "sources": SOURCES})

    class Forgejo(ForgejoClient):
        """Forgejo's file API over the bare repository: content and blob hash."""

        def get_file(self, owner, repo, path, ref="main"):
            _git(memory.forgejo, "pull", "-q", "--ff-only")
            if not (memory.forgejo / path).exists():
                return None
            return {"content": (memory.forgejo / path).read_text(encoding="utf-8"),
                    "sha": _git(memory.forgejo, "rev-parse", f"HEAD:{path}")}

        def change_files(self, owner, repo, changes, *, message, branch="main",
                         author_name=None, author_email=None):
            for c in changes:
                held = self.get_file(owner, repo, c.path)
                if (c.operation == "create" and held) or (
                        c.operation != "create" and c.sha != (held or {}).get("sha")):
                    raise ForgejoError(f"HTTP 409: {c.operation} {c.path} refused")
            memory.puts.append([c.path for c in changes])
            _commit(memory.forgejo, {c.path: c.content for c in changes}, message,
                    author=author_name or "memory-bot", email=author_email)
            return {"commit": {"sha": _git(memory.forgejo, "rev-parse", "HEAD")}}

    memory.puts, memory.call, memory.forgejo_api = [], call, Forgejo
    monkeypatch.setattr(reprocess, "call", call)
    monkeypatch.setattr(memory_lib, "ForgejoClient", Forgejo)
    # The curator, which would project the commit into the wiki's copy.
    monkeypatch.setattr(memory_lib, "propagate_write", lambda data_dir: True)
    return memory


@pytest.fixture
def memory(tmp_path, monkeypatch):
    return _memory(tmp_path, monkeypatch)


def _config(memory) -> dict:
    return {"data_dir": str(memory.data_dir),
            "secrets": {"memory__MEMORY_BOT_TOKEN": "t", "__code_url": "http://localhost:42040"},
            "stack": {"messages": {"server_name": "simpson"}}}


def _run(memory, *args):
    return reprocess.run(list(args), None, _config(memory))


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
                                 "msg": "it is Friday", "by": "@marge:simpson"}]


class TestAnApplyCommitsWhatWasPreviewed:

    def test_the_file_is_the_previewed_bytes_in_one_commit(self, memory):
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        result = _run(memory, "--apply", proposal)
        assert result["ok"] and result["replaces"] == memory.filed
        assert (memory.vault / PATH).read_text() == PROPOSED
        assert (memory.vault / SIDE).read_text() == PROPOSED_SIDECAR
        assert memory.puts == [[PATH, SIDE]], "card and sidecar in one commit"
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
        assert "it is Friday" in body and "Correction: stack memory reprocess" in body

    def test_a_note_in_the_older_format_gets_its_sidecar(self, tmp_path, monkeypatch):
        memory = _memory(tmp_path, monkeypatch, {PATH: OLD_NOTE})
        result = _run(memory, "--apply", _run(memory, "note-7f3a9c21e4")["proposal_id"])
        assert result["ok"]
        assert memory.puts == [[PATH, SIDE]]
        assert (memory.vault / SIDE).read_text() == PROPOSED_SIDECAR

    def test_a_proposal_is_applied_once(self, memory):
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        _run(memory, "--apply", proposal)
        assert _run(memory, "--apply", proposal)["refusal"] == "no-proposal"


class TestAnApplyRefusesWhenTheMemoryMoved:

    def test_the_proposal_is_based_on_the_hashes_forgejo_had(self, memory):
        result = _run(memory, "note-7f3a9c21e4")
        stored = json.loads(next(memory.data_dir.rglob(f"{result['proposal_id']}.json")).read_text())
        assert stored["base_sha"] == _git(memory.forgejo, "rev-parse", f"HEAD:{PATH}")
        assert stored["sidecar_sha"] == _git(memory.forgejo, "rev-parse", f"HEAD:{SIDE}")

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

    def test_the_memory_is_behind_its_remote(self, memory):
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        memory.change("family/other.md", "x\n", "capture: other")
        result = _run(memory, "--apply", proposal)
        assert result["refusal"] == "behind"
        assert "stack memory pull" in result["error"]

    def test_the_memory_has_uncommitted_changes(self, memory):
        proposal = _run(memory, "note-7f3a9c21e4")["proposal_id"]
        (memory.vault / PATH).write_text(NOTE + "a hand edit\n")
        result = _run(memory, "--apply", proposal)
        assert result["refusal"] == "uncommitted"
        assert PATH in result["error"]

    def test_an_unknown_proposal(self, memory):
        assert _run(memory, "--apply", "rp-000000000000")["refusal"] == "no-proposal"


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

    def test_a_note_whose_sidecar_somebody_changed_by_hand(self, memory):
        memory.change(SIDE, SIDECAR.replace("marge", "homer", 1), "Update sidecar", author="homer")
        memory.pull()
        assert _run(memory, "note-7f3a9c21e4")["refusal"] == "hand-edited"

    def test_a_note_somebody_changed_by_hand(self, memory):
        memory.change(PATH, NOTE.replace("budget max 200", "budget max 250"),
                      f"Update '{PATH}'", author="homer")
        memory.pull()
        result = _run(memory, "note-7f3a9c21e4")
        assert result["refusal"] == "hand-edited"
        assert "homer" in result["error"]
        assert memory.asked == [], "no model call"

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
        from stack.forgejo import ForgejoClient
        from stack.output import CollectorOutput

        (tmp_path / "stack.toml").write_text(f'[core]\ndomain = ""\ndata_dir = "{memory.data_dir}"\n')
        (tmp_path / "stacklets").mkdir()
        (tmp_path / "stacklets" / "memory").symlink_to(_REPO_ROOT / "stacklets" / "memory")
        stck = Stack(root=tmp_path, data=memory.data_dir, output=CollectorOutput())
        stck.secrets.set("memory", "MEMORY_BOT_TOKEN", "bottoken")
        # The bot runner and Forgejo, stood in where every import reaches them.
        monkeypatch.setattr("stack.bot_runner.call", memory.call)
        monkeypatch.setattr(ForgejoClient, "get_file", memory.forgejo_api.get_file)

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


class TestACardWriteIsBasedOnTheCardItWasMadeFrom:
    """`write_card` is the one way a reviewed card change reaches the memory:
    the apply above, and a card write from the card browser. It writes the
    card and its sidecar in one commit, only while both are at the hashes
    the new text was made from."""

    def _write(self, memory, text=PROPOSED, sidecar=PROPOSED_SIDECAR, card_id="note-7f3a9c21e4"):
        card = memory_lib.read_memory_file(_config(memory), PATH)
        side = memory_lib.read_memory_file(_config(memory), SIDE)
        return lambda: memory_lib.write_card(
            _config(memory), card_id, text, base_sha=card["sha"], sidecar=sidecar,
            sidecar_sha=side.get("sha", ""), actor="marge", message="reprocess: Trip to the hotel\n")

    def test_it_commits_card_and_sidecar_as_the_person(self, memory):
        result = self._write(memory)()
        assert result["committed"] and result["path"] == PATH
        assert memory.puts == [[PATH, SIDE]]
        assert (memory.vault / SIDE).read_text() == PROPOSED_SIDECAR
        assert memory.log("--format=%an", "-1") == "marge"

    def test_it_refuses_a_card_that_moved_and_writes_nothing(self, memory):
        write = self._write(memory)
        memory.change(PATH, NOTE.replace("Trip", "Hotel"), "update: Hotel")
        assert write().get("moved") is True
        assert memory.puts == []

    def test_it_refuses_a_sidecar_that_moved_and_writes_nothing(self, memory):
        write = self._write(memory)
        memory.change(SIDE, SIDECAR.replace("marge", "homer", 1), "reply: Trip")
        assert write().get("moved") is True
        assert memory.puts == []

    def test_it_refuses_text_that_is_another_card(self, memory):
        result = self._write(memory, text=PROPOSED.replace("note-7f3a9c21e4", "note-0b6e2d91aa"))()
        assert "note-0b6e2d91aa" in result["error"]
        assert memory.puts == []

    def test_it_refuses_a_sidecar_of_another_card(self, memory):
        result = self._write(memory, sidecar=PROPOSED_SIDECAR.replace(
            "note-7f3a9c21e4", "note-0b6e2d91aa"))()
        assert "note-0b6e2d91aa" in result["error"]
        assert memory.puts == []
