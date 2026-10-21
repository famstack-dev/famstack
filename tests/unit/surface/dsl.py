"""Words for writing a surface test the way its Given/When/Then reads.

    def test_...(memory, marge):
        marge.writes(LIST, text)

        assert memory.new_commits() == [commit(by="marge", saying="Tent -> Tent for four")]
        assert memory.page(LIST) == text
        assert marge.was_told(f"Wrote {LIST}")

`Person` runs commands as one member of the family, through the real entry
point (`Stack.run_cli_command`), and keeps what the last one answered and
what it showed them. `Memory` is the memory repository behind the Forgejo
stand-in, read the way a caller can see it: the commits since the test
began, and a page's text. `commit(by=..., saying=...)` is the commit a test
expects; it equals a real one by that author whose subject contains those
words.

These are plain values, so a test uses plain `assert` and pytest's own diff
explains a failure: each repr reads like what it stands for.

To add a verb, add a method to `Person` that builds the command's arguments
and hands them to `self.runs(stacklet, command, args)`. It hides the
plumbing (a temporary file, a flag), never the surface: the command still
runs as it does for a person. Add one when a test needs it, not before.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
from dataclasses import dataclass
from pathlib import Path


# ── Commits ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Commit:
    """A commit in the memory repo: who made it and its subject line."""

    by: str
    subject: str

    def __repr__(self) -> str:
        return f"Commit(by={self.by!r}, subject={self.subject!r})"


@dataclass(frozen=True, eq=False)
class commit:  # lower case: a word in a test, `commit(by=..., saying=...)`
    """The commit a test expects: by this person, its subject saying these words."""

    by: str
    saying: str

    def __eq__(self, other) -> bool:
        if not isinstance(other, Commit):
            return NotImplemented
        return other.by == self.by and self.saying in other.subject

    def __repr__(self) -> str:
        return f"commit(by={self.by!r}, saying={self.saying!r})"


# ── The memory repo ──────────────────────────────────────────────────────

class Memory:
    """The memory repository, as it stood when the test began and since.

    Path-like, so the Forgejo stand-in can serve it as it is.
    """

    def __init__(self, repo: Path):
        self.repo = Path(repo)
        self._start = self._git("rev-parse", "HEAD")

    def __fspath__(self) -> str:
        return str(self.repo)

    def new_commits(self) -> list[Commit]:
        """Every commit made since the test began, oldest first."""
        log = self._git("log", "--reverse", "--format=%an%x00%s", f"{self._start}..HEAD")
        return [Commit(*line.split("\x00", 1)) for line in log.splitlines()]

    def trailers(self, key: str) -> list[str]:
        """The `key:` trailer of each commit since the test began, oldest
        first; "" for a commit without one."""
        log = self._git("log", "--reverse", f"--format=%(trailers:key={key},valueonly)%x00",
                        f"{self._start}..HEAD")
        return [entry.strip() for entry in log.split("\x00")[:-1]]

    def page(self, path: str) -> str:
        """The text of a page as the repository has it now."""
        return self._git("show", f"HEAD:{path}", strip=False)

    def _git(self, *args: str, strip: bool = True) -> str:
        out = subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                             capture_output=True, text=True).stdout
        return out.strip() if strip else out

    def __repr__(self) -> str:
        return f"<memory at {self.repo}>"


# ── People ───────────────────────────────────────────────────────────────

class Person:
    """One member of the family, running `./stack` commands as themselves.

    `answer` is what the last command returned; `was_told` checks what it
    showed them: what it printed, and its error, which `./stack` prints.
    """

    def __init__(self, name: str, stack, workdir: Path):
        self.name = name
        self.stack = stack
        self.workdir = Path(workdir)
        self.answer: dict = {}
        self.told = ""

    def runs(self, stacklet: str, command: str, args: list[str]) -> dict:
        """`./stack <stacklet> <command> <args>`, through the real entry point."""
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.answer = self.stack.run_cli_command(stacklet, command, args) or {}
        self.told = printed.getvalue() + str(self.answer.get("error", ""))
        return self.answer

    def writes(self, path: str, text: str, *, dry_run: bool = False) -> dict:
        """`stack memory write <path> --by <me> --from <file> [--dry-run]`."""
        source = self.workdir / f"{self.name}-page.md"
        source.write_text(text, encoding="utf-8")
        args = [path, "--by", self.name, "--from", str(source)]
        return self.runs("memory", "write", args + (["--dry-run"] if dry_run else []))

    def edits(self, card_id: str, *changes: str) -> dict:
        """`stack memory card edit <card_id> <changes> --as <me>`, the changes
        as typed: `marge.edits("person-bart", "--add", "aliases=Bartman")`."""
        return self.runs("memory", "card", ["edit", card_id, *changes, "--as", self.name])

    def checks_the_format(self) -> dict:
        """`stack memory check-format`."""
        return self.runs("memory", "check-format", [])

    def was_told(self, *words: str) -> bool:
        """Whether the last command showed them every one of these words."""
        return all(w in self.told for w in words)

    def __repr__(self) -> str:
        return f"<{self.name}, last told {self.told!r}>"
