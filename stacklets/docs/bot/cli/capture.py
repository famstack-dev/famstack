"""`stack memory capture` — file a link, an image or pasted text into the vault.

The capture pipeline used to have exactly one door: the archivist reading
a Matrix room. Everything else that might want to file something (the
agent, a script, a person at a terminal) had no way in, which is why the
agent can read the whole family vault and add nothing to it.

This is that door, and it carries all three shapes a family actually
drops into a room:

    capture "Bart has a peanut allergy" --by homer      a note
    capture "https://example.com/tent"   --by homer     a bookmark, fetched
    capture --file ~/Downloads/receipt.jpg --by homer   an image or PDF

It runs the *same* pipeline the archivist runs, so what lands here is
indistinguishable from what lands through a room: same classifier, same
tag vocabulary, same mirror, same attribution.

WHERE THIS LIVES, AND WHY IT IS THE WRONG PLACE
    Filing into the vault is a memory concern -- memory owns the vault --
    so the command noun is `stack memory capture`, dispatched from
    `stacklets/memory/cli/capture.py`. The handler sits here, under docs,
    only because the pipeline it calls does. When the pipeline moves to
    the memory stacklet this module travels with it and the host
    dispatcher changes one constant. The seam callers depend on (the
    command, its arguments, its receipt) does not move.

WHERE THIS DIVERGES FROM THE ROOM, DELIBERATELY
    The archivist has a third rule: prose with a URL buried in it is
    treated as a URL drop, and the surrounding words become a hint for
    the classifier. That rule is guarded by `not mentioned` -- it exists
    to read raw family chatter, where the link is usually the point.
    Everything arriving here is deliberate, and a caller that writes a
    sentence and cites a source means the sentence. So prose stays a
    note, with its link preserved by TextExtractor, and only a bare URL
    is fetched as a bookmark.

THE RECEIPT IS THE POINT
    The agent relays this output to the family. A receipt that reads the
    same whether or not anything was filed is precisely what lets an
    agent report a success it never had, so a failure here never renders
    as a filing. See tests/stacklets/test_memory_capture_cli.py.
"""

from __future__ import annotations

import mimetypes
import sys
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from capture_pipeline import CapturePipeline
from capture_tags import CaptureTagCache
from extractors import TextExtractor, UrlExtractor
from pipeline import Classifier, PaperlessAPI
from text_utils import is_just_url

# `_DATA_DIR` is the bot's in-container session dir, and the same constant
# `_mirror` already resolves the bot's Forgejo creds from. Imported rather
# than restated so the CLI and the bot never disagree about where the
# archivist keeps its state.
from cli._mirror import _DATA_DIR, build_mirror_like_bot, read_bot_toml_settings
from cli._shared import err

_FILED = ("captured", "reclassified")


@dataclass(frozen=True)
class CaptureSpec:
    """One filing request: what to file, who is filing it, and where."""

    text: str = ""
    sender: str = ""
    bucket: str | None = None
    file: str | None = None
    stdin_name: str | None = None


def capture_kind(spec: CaptureSpec) -> str:
    """Which shape of capture this request is: file, link, or note.

    The same question the archivist asks of a room message, minus its
    chatter-reading rule (see the module docstring).
    """
    if spec.file or spec.stdin_name:
        return "file"
    return "link" if is_just_url(spec.text) else "note"


def parse_args(argv: list[str]) -> CaptureSpec:
    """Read the command line a caller wrote.

    Bare words are the body. They are rejoined with spaces because the
    agent reaches this command through a plaintext socket that splits on
    shlex: a quoted body arrives whole, an unquoted one arrives in
    pieces, and filing only the first word of a note is worse than
    failing outright.

    Raises ValueError with a message the caller can act on; the command
    turns that into a usage error rather than a traceback.
    """
    words: list[str] = []
    sender: str | None = None
    bucket: str | None = None
    file: str | None = None
    stdin_name: str | None = None

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--by" and i + 1 < len(argv):
            sender = argv[i + 1]
            i += 2
            continue
        if arg == "--bucket" and i + 1 < len(argv):
            bucket = argv[i + 1]
            i += 2
            continue
        if arg == "--file" and i + 1 < len(argv):
            file = argv[i + 1]
            i += 2
            continue
        if arg == "--stdin-file" and i + 1 < len(argv):
            stdin_name = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            raise ValueError(f"unknown flag {arg!r}")
        words.append(arg)
        i += 1

    if not sender:
        raise ValueError("a capture must name its author: --by <person>")
    text = " ".join(words).strip()
    if not text and not (file or stdin_name):
        raise ValueError("nothing to capture: give the text, a URL, or --file")
    # The pipeline reads a binary's meaning out of the bytes themselves and
    # has nowhere to put a caption, so accepting one would silently drop it.
    if text and (file or stdin_name):
        raise ValueError("--file takes no text alongside it; capture them separately")

    # The agent knows people as `@homer:simpson`, a person at a terminal
    # types `homer`. The vault attributes both to the same human.
    return CaptureSpec(
        text=text,
        sender=sender.split(":")[0].lstrip("@"),
        bucket=bucket,
        file=file,
        stdin_name=stdin_name,
    )


def render_receipt(outcome) -> str:
    """Turn a CaptureOutcome into the line the caller reads back.

    Only a genuine filing may start with "Captured:". Everything else
    says plainly that nothing was filed and why, so neither a person
    skimming a terminal nor an agent relaying to a room can mistake a
    failure for a success.
    """
    if outcome.status in _FILED:
        title = (outcome.classification or {}).get("title") or "(untitled)"
        lines = [f"Captured: {title}"]
        if outcome.vault_path:
            lines.append(f"  vault: {outcome.vault_path}")
        if outcome.scope:
            lines.append(f"  scope: {outcome.scope}")
        return "\n".join(lines)

    if outcome.status == "empty":
        return "Nothing captured: there was no text to file."
    if outcome.status == "no_mirror":
        return ("Nothing captured: the vault is not reachable "
                "(is the code stacklet up?).")

    what = {
        "url": "that link",
        "transcription": "that voice memo",
        "binary": "that file",
    }.get(getattr(outcome, "failure_reason", None), "the content")
    return f"Nothing captured: could not read {what}."


class _StderrNotifier:
    """The mid-flow status port, pointed at a terminal instead of a room.

    Capture posts "fetching …" before it pulls a link. In a room that is
    a chat message; here it is a progress line on stderr, so stdout stays
    exactly the receipt a caller parses.
    """

    async def status(self, key: str, **kwargs) -> None:
        err(f"… {key}")

    async def acknowledge(self) -> None:
        """No source message to react to on a command line."""


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    try:
        spec = parse_args(argv)
    except ValueError as e:
        err(str(e))
        err('Usage: capture "<text|url>" --by <person> [--bucket <scope>]')
        err('       capture --file <path> --by <person> [--bucket <scope>]')
        return 2

    kind = capture_kind(spec)
    payload, name = b"", ""
    if kind == "file":
        # `--stdin-file` is how a *host* file gets here: this process runs
        # in a container that cannot see the caller's disk, so the host
        # dispatcher reads the bytes and pipes them in. `--file` remains
        # for paths the container really can see, like anything under /data.
        if spec.stdin_name:
            payload, name = sys.stdin.buffer.read(), spec.stdin_name
        else:
            path = Path(spec.file).expanduser()
            try:
                payload, name = path.read_bytes(), path.name
            except OSError as e:
                err(f"Cannot read {path}: {e}")
                return 1
        if not payload:
            err(f"Cannot read {name}: no bytes arrived")
            return 1

    mirror = build_mirror_like_bot()
    if mirror is None:
        err("Mirror env missing — bring up `code` so CODE_URL / admin creds are set.")
        return 1

    settings = read_bot_toml_settings()
    tags = CaptureTagCache(_DATA_DIR / "capture-tags.json")
    tags.load()

    async with aiohttp.ClientSession() as http:
        pipeline = CapturePipeline(
            url_extractor=UrlExtractor(http),
            text_extractor=TextExtractor(),
            classifier=classifier,
            mirror=mirror,
            capture_tags=tags,
            paperless=paperless,
            bot_name="archivist-bot",
            classify_max_chars=int(settings.get("classify_max_chars", 100_000)),
            capture_keep_body=bool(settings.get("capture_keep_body", False)),
            capture_tag_prompt_size=int(settings.get("capture_tag_prompt_size", 50)),
            # No transcriber wired: an audio file fails cleanly with
            # "could not read that voice memo" rather than half-filing.
        )

        if kind == "link":
            outcome = await pipeline.capture_url(
                url=spec.text, sender_mxid=spec.sender,
                notifier=_StderrNotifier(), bucket=spec.bucket,
            )
        elif kind == "file":
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            outcome = await pipeline.capture_binary(
                file_data=payload, mime=mime, filename=name,
                # A room capture points `source_uri` at the Matrix mxc URL
                # so the entry links back to the original bytes. A local
                # file has no such home, and we deliberately do not copy it
                # into the vault, so the entry keeps what was read out of
                # the file and names the file it came from.
                source_uri=None, display_link=name,
                sender_mxid=spec.sender, bucket=spec.bucket,
            )
        else:
            outcome = await pipeline.capture_text(
                text=spec.text, sender_mxid=spec.sender, bucket=spec.bucket,
            )

    print(render_receipt(outcome))
    sys.stdout.flush()
    return 0 if outcome.status in _FILED else 1
