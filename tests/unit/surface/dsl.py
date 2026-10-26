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
its text by part, its links. `Browser.follows` holds an event stream open
as `EventSource` does, and `EventStream` reads it frame by frame.

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
import json
import os
import subprocess
import time
from collections.abc import Iterator
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
                trailers: tuple[str, ...] = (), committed_by: str = "") -> None:
        """One commit by `by`, made at `at`, writing `files` (path to text),
        its message ending in `trailers` (`Migration: memory/0002`).

        `committed_by` is the account that wrote it when that is not `by`:
        a bot writing a change by or for a person (ADR-015). Left out, the
        commit is `by`'s own, as every commit was before bots committed as
        themselves."""
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text, encoding="utf-8")
        message = subject + ("\n\n" + "\n".join(trailers) if trailers else "")
        when = at.isoformat()
        committer = committed_by or by
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", f"user.name={by}",
                        "-c", f"user.email={by}@springfield.example", "commit", "-q", "-m", message],
                       check=True, env={**os.environ, "GIT_AUTHOR_DATE": when,
                                        "GIT_COMMITTER_DATE": when,
                                        "GIT_COMMITTER_NAME": committer,
                                        "GIT_COMMITTER_EMAIL": f"{committer}@springfield.example"})

    def __fspath__(self) -> str:
        return str(self.repo)

    def new_commits(self) -> list[Commit]:
        """Every commit made since the test began, oldest first."""
        log = self._git("log", "--reverse", "--format=%an%x00%s", f"{self._start}..HEAD")
        return [Commit(*line.split("\x00", 1)) for line in log.splitlines()]

    def authors(self) -> list[str]:
        """Who made each commit since the test began, oldest first, as git
        shows an author: `marge <marge@simpson>`."""
        log = self._git("log", "--reverse", "--format=%an <%ae>", f"{self._start}..HEAD")
        return log.splitlines()

    def committers(self) -> list[str]:
        """The account that wrote each commit since the test began, oldest
        first, as git shows a committer: `memory-bot <memory-bot@local>`."""
        log = self._git("log", "--reverse", "--format=%cn <%ce>", f"{self._start}..HEAD")
        return log.splitlines()

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

    def refiles(self, card_id: str, *, correction: str = "") -> dict:
        """`stack memory reprocess <card_id> [--msg <correction> --by <me>]`,
        then `--apply` the proposal the preview made, as a person at the
        terminal does. Without a correction nobody is named: the refile is
        the machine's, asked for by the admin."""
        args = [card_id, *(["--msg", correction, "--by", self.name] if correction else [])]
        preview = self.runs("memory", "reprocess", args)
        if "proposal_id" not in preview:
            return preview
        return self.runs("memory", "reprocess", ["--apply", preview["proposal_id"]])

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
    `language` is the one the browser asks for first (`de` for German).
    """

    def __init__(self, app, *, listener: int, host: str, language: str = "en"):
        from fastapi.testclient import TestClient

        self.client = TestClient(app, base_url=f"http://testserver:{listener}")
        self.host = host
        self.language = language

    def opens(self, path: str) -> Page:
        r = self.client.get(path, headers={"host": self.host, "accept-language": self.language},
                            follow_redirects=False)
        return Page(r.status_code, r.headers.get("content-type", ""), r.text)

    def reads(self, path: str) -> tuple[int, dict]:
        """The JSON a page's script reads from `path`, with its status."""
        r = self.client.get(path, headers={"host": self.host, "accept-language": self.language})
        return r.status_code, r.json()

    def posts(self, path: str, body: dict) -> tuple[int, dict]:
        """The JSON answer to `body` posted to `path`, with its status."""
        r = self.client.post(path, json=body, headers={"host": self.host})
        return r.status_code, r.json()

    @contextlib.contextmanager
    def follows(self, path: str, *, last_event_id: str = "") -> Iterator[EventStream]:
        """An event stream held open, as `EventSource` holds one;
        `last_event_id` is what a browser sends when it reconnects."""
        headers = {"host": self.host, "accept": "text/event-stream"}
        if last_event_id:
            headers["last-event-id"] = last_event_id
        with self.client.stream("GET", path, headers=headers) as r:
            yield EventStream(r)


@dataclass(frozen=True)
class Frame:
    """One frame of an event stream: an event (its SSE `id`, and `event`,
    the envelope its data holds), or a frame of comments and `retry` only."""

    id: str | None
    event: dict | None
    retry: int | None
    comment: str


class EventStream:
    """An open `text/event-stream` response, read as a browser parses it."""

    def __init__(self, response):
        self.status = response.status_code
        self.headers = response.headers
        self._lines = response.iter_lines()

    def next_frame(self) -> Frame:
        fields: dict[str, list[str]] = {}
        for line in self._lines:
            if not line:
                if fields:
                    break
                continue
            name, _, value = line.partition(":")
            fields.setdefault(name, []).append(value.removeprefix(" "))
        data = "\n".join(fields.get("data", []))
        return Frame(id=(fields.get("id") or [None])[-1],
                     event=json.loads(data) if data else None,
                     retry=int(fields["retry"][-1]) if "retry" in fields else None,
                     comment="\n".join(fields.get("", [])))

    def events_until(self, type: str, *, within: float = 5) -> list[Frame]:
        """The event frames up to and including the first whose envelope
        has `type`; comments and heartbeats are skipped."""
        deadline = time.monotonic() + within
        frames: list[Frame] = []
        while not (frames and frames[-1].event and frames[-1].event["type"] == type):
            assert time.monotonic() < deadline, f"no {type!r} within {within} s: {frames}"
            frame = self.next_frame()
            if frame.event is not None:
                frames.append(frame)
        return frames

    def frames_for(self, seconds: float) -> list[Frame]:
        """Every frame that arrives within `seconds`."""
        deadline = time.monotonic() + seconds
        frames = []
        while time.monotonic() < deadline:
            frames.append(self.next_frame())
        return frames


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
