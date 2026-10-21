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
began, and a page's text; `Memory.new` and `changes(...)` write a history
as the people and bots of an instance would. `commit(by=..., saying=...)`
is the commit a test expects; it equals a real one by that author whose
subject contains those words.

`Browser` opens a web page of the stack the way a browser does, through
the listener it arrives on; `Page` reads it without running its scripts:
its text by part, its links.

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
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
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

    @classmethod
    def new(cls, repo: Path) -> Memory:
        """A memory repository whose one commit touches no card, as a fresh
        install leaves it before anything is filed."""
        repo = Path(repo)
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        (repo / ".gitattributes").write_text("*.md text\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=stackadmin",
                        "-c", "user.email=stackadmin@springfield.example",
                        "commit", "-q", "-m", "seed: initial memory"], check=True)
        return cls(repo)

    def changes(self, subject: str, *, by: str, at: datetime, files: dict[str, str],
                trailers: tuple[str, ...] = ()) -> None:
        """One commit by `by`, made at `at`, writing `files` (path to text),
        its message ending in `trailers` (`Migration: memory/0002`)."""
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text, encoding="utf-8")
        message = subject + ("\n\n" + "\n".join(trailers) if trailers else "")
        when = at.isoformat()
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", f"user.name={by}",
                        "-c", f"user.email={by}@springfield.example", "commit", "-q", "-m", message],
                       check=True, env={**os.environ, "GIT_AUTHOR_DATE": when,
                                        "GIT_COMMITTER_DATE": when})

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


# ── A browser, and the page it shows ─────────────────────────────────────

class Page:
    """A page as a browser received it, read the way a person reads it:
    its text by part (`texts("sentence")`), its links, its headings.

    Read without running its scripts, so what it says is what a browser
    without JavaScript shows.
    """

    def __init__(self, status: int, kind: str, body: str):
        self.status = status
        self.kind = kind
        self.body = body
        self._root = _parse(body) if "html" in kind else _Node("", {})

    def texts(self, part: str) -> list[str]:
        """The text of every element of this class, in page order."""
        return [n.text for n in self._root.find(lambda n: part in n.classes)]

    def links(self, part: str = "") -> list[tuple[str, str]]:
        """(text, href) of every link, or of those inside elements of this class."""
        roots = self._root.find(lambda n: part in n.classes) if part else [self._root]
        return [(a.text, a.attrs.get("href", "")) for r in roots
                for a in ([r] if r.tag == "a" else r.find(lambda n: n.tag == "a"))]

    def attribute(self, tag: str, name: str) -> str:
        """`name` of the first `tag` element: `page.attribute("fs-widget", "state")`."""
        found = self._root.find(lambda n: n.tag == tag)
        return found[0].attrs.get(name, "") if found else ""

    @property
    def text(self) -> str:
        return self._root.text

    def __repr__(self) -> str:
        return f"<page {self.status} {self.kind}: {self.text[:120]!r}>"


class Browser:
    """A browser opening the core tools server's pages through one listener.

    `listener` is the port the request arrives on (`access.PUBLIC_PORT` is
    the one published to the network), `host` what the address bar says:
    `localhost:42000` on this Mac, the Mac's LAN address on a phone.
    """

    def __init__(self, app, *, listener: int, host: str):
        from fastapi.testclient import TestClient

        self.client = TestClient(app, base_url=f"http://testserver:{listener}")
        self.host = host

    def opens(self, path: str) -> Page:
        r = self.client.get(path, headers={"host": self.host, "accept-language": "en"},
                            follow_redirects=False)
        return Page(r.status_code, r.headers.get("content-type", ""), r.text)

    def reads(self, path: str) -> tuple[int, dict]:
        """The JSON a page's script reads from `path`, with its status."""
        r = self.client.get(path, headers={"host": self.host, "accept-language": "en"})
        return r.status_code, r.json()


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag, self.attrs, self.children = tag, attrs, []
        self.classes = set(attrs.get("class", "").split())

    @property
    def text(self) -> str:
        parts = [c if isinstance(c, str) else c.text for c in self.children
                 if isinstance(c, str) or c.tag not in ("script", "style", "template")]
        return " ".join("".join(parts).split())

    def find(self, match) -> list[_Node]:
        found = []
        for c in self.children:
            if isinstance(c, _Node):
                if match(c):
                    found.append(c)
                found += c.find(match)
        return found


_VOID = {"meta", "link", "br", "img", "input", "hr", "source", "wbr"}


def _parse(html: str) -> _Node:
    from html.parser import HTMLParser

    root = _Node("", {})
    stack = [root]

    class Builder(HTMLParser):
        def handle_starttag(self, tag, attrs):
            node = _Node(tag, {k: v or "" for k, v in attrs})
            stack[-1].children.append(node)
            if tag not in _VOID:
                stack.append(node)

        def handle_endtag(self, tag):
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].tag == tag:
                    del stack[i:]
                    break

        def handle_data(self, data):
            stack[-1].children.append(data)

    Builder(convert_charrefs=True).feed(html)
    return root
