"""Words for a surface test that opens the stack's web pages.

    page = Browser(app, listener=PUBLIC_PORT, host="localhost:42000").opens("/")
    assert page.texts("sentence") == [...]

`Browser` opens a web page of the stack the way a browser does, through
the listener it arrives on; `Page` reads it without running its scripts:
its text by part, its links. `Browser.follows` holds an event stream open
as `EventSource` does, and `EventStream` reads it frame by frame. Plain
values, so a plain `assert` and pytest's diff explain a failure. Memory's
words (people, commits, the memory repo) are in `tests/memory/unit/dsl.py`.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass


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

    def widget(self, name: str) -> Page:
        """The part of the page one widget's shell holds, read the same way:
        `page.widget("activity").attribute("fs-widget", "state")`."""
        part = Page(self.status, "", "")
        part._root.children = self._root.find(
            lambda n: n.tag == "fs-widget" and n.attrs.get("data-widget") == name)
        return part

    def texts(self, part: str) -> list[str]:
        """The text of every element of this class, in page order."""
        return [n.text for n in self._root.find(lambda n: part in n.classes)]

    def links(self, part: str = "") -> list[tuple[str, str]]:
        """(text, href) of every link, or of those inside elements of this class."""
        roots = self._root.find(lambda n: part in n.classes) if part else [self._root]
        return [(a.text, a.attrs.get("href", "")) for r in roots
                for a in ([r] if r.tag == "a" else r.find(lambda n: n.tag == "a"))]

    def scripts(self) -> list[str]:
        """The `src` of every script the page loads."""
        return [n.attrs.get("src", "") for n in self._root.find(lambda n: n.tag == "script")]

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

    def posts(self, path: str, body: dict, *, sent_as: str | None = "application/json") -> tuple[int, dict]:
        """The JSON answer to `body` posted to `path`, with its status.
        `sent_as` is the Content-Type the body goes out with, None for none:
        a page from another site sends JSON as `text/plain`, or as a Blob
        without a type."""
        headers = {"host": self.host} | ({"content-type": sent_as} if sent_as else {})
        r = self.client.post(path, content=json.dumps(body), headers=headers)
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
