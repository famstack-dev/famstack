"""`reprocess-note`: what a filed note would be refiled as, written nowhere.

The model half of `stack memory reprocess`. The host command finds the
card, checks its history and keeps the proposal; this half runs where the
model is, refiles the note through the archivist's own pipeline, and
hands back the file it would write instead of writing it:

    echo '{"path": "...", "text": "...", "sidecar": "...", "msg": "", "by": "",
           "prior": "...", "edits": [...], "ops": [...],
           "quotes_as_sent": {...}, "quote_edits": [...]}' | python cli_entrypoint.py reprocess-note
    {"proposed": "---\\ntype: note\\n...", "proposed_sidecar": "{...}", "title": "...",
     "clean_rejected": "", "sources": [{"role": "original", "person": "marge", ...}, ...]}
    {"refused": "the note keeps no words of its own to refile it from", "sources": [...]}

`text` and `sidecar` are the card and its sidecar as the host read them
from Forgejo; the proposal is made from them. The refile reads the note's
sources, never its summary (ADR-018): the ones the card keeps, the
replies in the thread of each of its messages the card does not hold
yet, read from the chat as the archivist, and `msg`, a correction by
`by`, appended as a `note` source (`stack.filing.refile.refile_sources`). `prior`, `edits` and `ops` are what the card's history
says (`stack.cards.history`, read by the host from the memory's git
history): the clean version before the first correction or edit, each
edit a person made to it, and each field edit. A note a person edited is
rebuilt as a card conversation from that clean version, and the field
edits are applied again to the card (ADR-018 §7); `left_out` names the
ones that no longer fit. Without them (a request with no `prior`) the
history is not read here: the bot runner leaves that to the host.
`quotes_as_sent` and `quote_edits` are a person's hand edits of a quote,
which are their correction (ADR-018 §4): the quote is put back to the
words as sent, keyed by source id, and each edit is appended as their
`note` source (a sidecar entry with its quote) before the replies the
chat adds. `by` is who asked for the refile either way, and the sidecar's
`made.requested_by` names them. `sources` is that list, each with its
quote, so what is shown is what the model read.

Nothing here writes: the vault is reached through a mirror that only
composes the files, with the topics of the card's place. Exit 0
with one JSON object on stdout for a proposal or a refusal, 1 when no
proposal could be made (no model answer, no mirror settings).
"""

from __future__ import annotations

import json
import sys

from ..capture_pipeline import CapturePipeline, words_refusal
from ..git_mirror import Filed
from ..pipeline import Classifier, PaperlessAPI

from ._chat import read_note_thread, source_of
from ._mirror import build_mirror_like_bot, read_bot_toml_settings
from ._shared import err
from stack.cards.events import edit_from_json
from stack.cards.fields import op_from_json
from stack.cards.history import CardHistory
from stack.cards.sources import (
    NOTE, ORIGINAL, REPLY, UNKNOWN, Source, SourcesMismatch, new_id, now, read_sidecar,
    source_from, words_of,
)
from stack.filing.refile import NoteRead, originals, refile_sources

from stacklets.memory.note_card import card_sources


class _PreviewMirror:
    """A mirror that reads the one card it was given and composes, never writes.

        preview = _PreviewMirror(mirror, "marge/notes/2026/10/trip-3a338e.md", text)
        await pipeline.reprocess(vault_path=preview.path, note=note, ...)
        preview.entry.content     # the card the refile would write
        preview.entry.sidecar     # and its sidecar
    """

    def __init__(self, mirror, path: str, text: str):
        self._mirror = mirror
        self.path = path
        self._text = text
        self.entry = None

    async def publish_capture(self, *, entity: str, existing_path: str | None = None,
                              changed_by: str | None = None, base: str | None = None,
                              sidecar_base: str | None = None, **entry) -> Filed:
        # Who the commit is by, and the version it is written on, are decided
        # where it is made: the host's `reprocess --apply`, not this composing
        # step (`changed_by`, `base` and `sidecar_base` are unused here).
        self.entry = self._mirror.capture_entry(
            target_path=self.path, existing=self._text, **entry)
        return Filed(self.path, str(self.entry.frontmatter.get("card_id") or ""))


class _AnsweredClassifier:
    """The classifier, noting whether the model answered.

    The pipeline files a capture without a model answer, under its old
    title and no tags, because a capture is still worth keeping then. A
    refile without one would replace a good card with that, so the
    preview says so instead of proposing it.
    """

    def __init__(self, classifier: Classifier):
        self._classifier = classifier
        self.answered = False

    async def classify_capture(self, **kwargs) -> dict:
        answer = await self._classifier.classify_capture(**kwargs)
        self.answered = bool(answer)
        return answer

    async def correct_note(self, **kwargs) -> dict:
        """The corrected text comes before the classification, which
        alone says whether the model answered."""
        return await self._classifier.correct_note(**kwargs)

    async def rebuild_note(self, **kwargs) -> dict:
        """As `correct_note`, for a note a person edited."""
        return await self._classifier.rebuild_note(**kwargs)


def _history(request: dict) -> CardHistory | None:
    """The card's history as the host read it (`prior`, `edits`, `ops`,
    `quotes_as_sent`, `quote_edits`); None for a request without one."""
    if request.get("prior") is None:
        return None
    return CardHistory(
        prior=str(request["prior"]),
        edits=tuple(edit_from_json(e) for e in request.get("edits") or [] if isinstance(e, dict)),
        ops=tuple(op_from_json(o) for o in request.get("ops") or [] if isinstance(o, dict)),
        quotes_as_sent=tuple((str(i), str(q))
                             for i, q in (request.get("quotes_as_sent") or {}).items()),
        quote_edits=tuple(source_from(s, str(s.get("quote") or ""))
                          for s in request.get("quote_edits") or [] if isinstance(s, dict)))


async def _from_the_chat(held: list[Source]) -> tuple[list[Source], list[Source]]:
    """Every original's message as the chat has it, and the replies in its
    thread, as sources; none for a message the chat cannot give."""
    messages, replies = [], []
    for original in originals(held):
        thread = await read_note_thread(original.id)
        if thread is None:
            continue
        messages.append(source_of(thread.message, ORIGINAL))
        replies.extend(source_of(event, REPLY) for event in thread.replies)
    return [m for m in messages if m], [r for r in replies if r]


def _refusal(sources: list[Source]) -> str:
    """Why the note cannot be refiled from these sources; "" when it can."""
    return words_refusal(words_of(sources), has_file=any(s.media_link for s in originals(sources)))


async def run(paperless: PaperlessAPI, classifier: Classifier, argv: list[str]) -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
        path, text = str(request["path"]), str(request["text"])
    except (ValueError, KeyError) as e:
        err(f"reprocess-note reads a JSON object with path and text on stdin ({e})")
        return 2
    side_text = str(request.get("sidecar") or "")
    sidecar = read_sidecar(side_text)
    msg = str(request.get("msg") or "").strip()
    by = str(request.get("by") or "").strip()
    history = _history(request)

    try:
        held = card_sources(text, sidecar)
    except SourcesMismatch as e:
        print(json.dumps({"refused": f"its card and its sidecar do not pair up: {e}", "sources": []}))
        return 0
    chat, replies = await _from_the_chat(held)
    correction = [Source(NOTE, by.split(":")[0].lstrip("@").lower() or UNKNOWN, now(), "cli", "text",
                         new_id("cli"), quote=msg)] if msg else []
    added = [*replies, *correction]
    sources = refile_sources(held, history=history, chat=chat, added=added)
    shown = [s.shown() for s in sources]
    if refused := _refusal(sources):
        print(json.dumps({"refused": refused, "sources": shown}))
        return 0

    mirror = build_mirror_like_bot()
    preview = _PreviewMirror(mirror, path, text)
    model = _AnsweredClassifier(classifier)
    settings = read_bot_toml_settings()
    pipeline = CapturePipeline(
        url_extractor=None, text_extractor=None,
        classifier=model, mirror=preview,
        paperless=paperless, bot_name="archivist-bot",
        classify_max_chars=int(settings.get("classify_max_chars", 100_000)),
        capture_keep_body=bool(settings.get("capture_keep_body", False)),
        vault=mirror.vault,
    )
    # The preview names no versions: the host's `--apply` writes on the
    # ones it read (`base_sha`, `sidecar_sha`).
    note = NoteRead(path, text, "", side_text, "", history)
    filer = str(sidecar.get("filed_by") or "")
    outcome = await pipeline.reprocess(
        vault_path=path, user_hint="", sender_mxid=by or filer or "archivist-bot",
        note=note, chat=chat, added=added,
        requested_by=by.split(":")[0].lstrip("@") or filer,
    )
    if not model.answered or preview.entry is None:
        err(f"No proposal for {path}: the model gave no answer ({outcome.status}).")
        return 1
    print(json.dumps({
        "proposed": preview.entry.content,
        "proposed_sidecar": preview.entry.sidecar,
        "title": str(preview.entry.frontmatter.get("title") or ""),
        "clean_rejected": outcome.clean_rejected,
        "sources": shown,
        "left_out": list(preview.entry.left_out),
    }))
    return 0
