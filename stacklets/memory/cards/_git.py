"""Git, the way the card store runs it: one command at a time, no shell,
a timeout on each, and git's own words in English so they can be read.

The store's only adapter: nothing else in the package starts a process.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# A local git command: a commit, a history read over the whole memory.
TIMEOUT_S = 60

# A fetch or push to Forgejo on the stack network takes well under a
# second. A write makes two (fetch, push) behind at most the curator's
# own sync, and its caller waits 30 s (`stack.http_json.TIMEOUT_S`, core's
# door), so each gets 5 s: Forgejo that does not answer leaves the write
# pending, answered in time, instead of reported failed and made again.
NETWORK_TIMEOUT_S = 5

# What `git push` exits with when Forgejo answered and refused a ref; it
# exits 128 when it could not get an answer.
PUSH_REFUSED = 1

# Git asks for credentials on a terminal when a remote refuses them. The
# store has none, and a prompt would hang the curator, so git fails instead.
_ENV = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "LANGUAGE": "C"}


class GitFailed(Exception):
    """Git could not do what it was asked; the text is git's own."""


@dataclass(frozen=True)
class Ran:
    """What one git command did: its exit code and what it printed."""

    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def complaint(self) -> str:
        """The line git explained a failure with: its own `fatal:` line,
        which says why it gave up (authentication failed, a host it cannot
        reach), else the first line, which for a refused push is the
        server's reason. A server's own line comes before git's in the
        output, and on a refused login says less (`remote: Unauthorized`)."""
        lines = [line.strip() for line in (self.err or self.out).splitlines() if line.strip()]
        fatal = [line for line in lines if line.startswith("fatal: ")]
        said = (fatal or lines or [""])[0]
        return said.removeprefix("fatal: ").removeprefix("error: ")


class Git:
    """The git commands of one repository.

        git = Git(Path("/data/memory/vault"))
        git("rev-parse", "HEAD").out          # the commit the clone is at
        git("commit", "-F", "-", input=message, env={"GIT_AUTHOR_NAME": "marge", ...})
    """

    def __init__(self, repo: Path, *, timeout: float = TIMEOUT_S,
                 env: dict[str, str] | None = None):
        self.repo = repo
        self.timeout = timeout
        self.env = {**_ENV, **(env or {})}

    def __call__(self, *args: str, input: str | None = None,
                 env: dict[str, str] | None = None, timeout: float | None = None) -> Ran:
        # Bytes in and out, decoded here: text mode would turn CR LF into
        # LF, and a file's version is the blob id of its bytes as they are.
        limit = self.timeout if timeout is None else timeout
        try:
            done = subprocess.run(["git", "-C", str(self.repo), *args],
                                  input=None if input is None else input.encode("utf-8"),
                                  capture_output=True, timeout=limit,
                                  env={**os.environ, **self.env, **(env or {})})
        except subprocess.TimeoutExpired:
            return Ran(124, "", f"git {args[0]} did not finish within {limit:.0f} seconds")
        except FileNotFoundError:
            return Ran(127, "", "git is not installed")
        return Ran(done.returncode, done.stdout.decode("utf-8"),
                   done.stderr.decode("utf-8", errors="replace"))

    def raw(self, *args: str, input: bytes) -> bytes:
        """What git prints, as bytes: for output measured in bytes, such as
        `cat-file --batch`. A failure raises `GitFailed`."""
        try:
            done = subprocess.run(["git", "-C", str(self.repo), *args], input=input,
                                  capture_output=True, timeout=self.timeout,
                                  env={**os.environ, **self.env})
        except subprocess.TimeoutExpired as e:
            raise GitFailed(f"git {args[0]} did not finish within {self.timeout:.0f} seconds") from e
        except FileNotFoundError as e:
            raise GitFailed("git is not installed") from e
        if done.returncode != 0:
            raise GitFailed(Ran(done.returncode, "", done.stderr.decode(errors="replace")).complaint)
        return done.stdout

    # Each answer below tells "not there" from "git failed": the first is an
    # answer, the second raises `GitFailed` with git's words, so a broken
    # clone is never read as an empty one.

    def blob(self, text: str) -> str:
        """The version a text would have as a file: its git blob id."""
        return self._must("hash-object", "--stdin", input=text).out.strip()

    def version(self, path: str, rev: str = "HEAD") -> str:
        """The blob id of the file `path` at `rev`, "" when there is none."""
        listed = self._tree("ls-tree", "-z", rev, "--", path, rev=rev)
        mode_type_id, _, listed_path = listed.rstrip("\0").partition("\t")
        _, kind, blob_id = (mode_type_id.split() + ["", "", ""])[:3]
        return blob_id if kind == "blob" and listed_path == path else ""

    def listed(self, under: str = "", rev: str = "HEAD") -> list[tuple[str, str]]:
        """(path, blob id) of every file under the directory `under` at
        `rev`, at any depth, or of every file with no `under`; none when
        there is no such directory."""
        spec = ("--", f"{under}/") if under else ()
        out = self._tree("-c", "core.quotePath=false", "ls-tree", "-r", "-z", rev, *spec, rev=rev)
        found = []
        for entry in filter(None, out.split("\0")):
            mode_type_id, _, path = entry.partition("\t")
            _, kind, blob_id = (mode_type_id.split() + ["", "", ""])[:3]
            if kind == "blob":
                found.append((path, blob_id))
        return found

    def text(self, path: str, rev: str = "HEAD") -> str | None:
        """The text of the file `path` at `rev`, None when there is none."""
        found = self.version(path, rev)
        return self._must("cat-file", "blob", found).out if found else None

    def head(self, ref: str = "HEAD") -> str:
        """The commit `ref` names, "" when it names none (no such branch yet)."""
        found = self("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if found.code == 1:
            return ""
        return self._checked(found).out.strip()

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self._checked(self("merge-base", "--is-ancestor", older, newer), allowed=(1,)).ok

    def _tree(self, *args: str, rev: str) -> str:
        """What a listing of the tree at `rev` printed: nothing when `rev`
        is a branch that has no commit yet, as a new memory's has none."""
        listed = self(*args)
        if not listed.ok and not self.head(rev):
            return ""
        return self._checked(listed).out

    def _must(self, *args: str, input: str | None = None) -> Ran:
        return self._checked(self(*args, input=input))

    @staticmethod
    def _checked(ran: Ran, *, allowed: tuple[int, ...] = ()) -> Ran:
        if ran.ok or ran.code in allowed:
            return ran
        raise GitFailed(ran.complaint or f"git exited with {ran.code}")
