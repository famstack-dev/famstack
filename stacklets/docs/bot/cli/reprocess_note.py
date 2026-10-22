"""`reprocess-note`: what a filed note would be refiled as, written nowhere.

The model half of `stack memory reprocess`. The host command finds the
card, checks its history and keeps the proposal; this half runs where the
model is, refiles the note through the archivist's own pipeline, and
hands back the file it would write instead of writing it:

    echo '{"path": "...", "text": "...", "sidecar": "...", "msg": "", "by": "",
           "prior": "...", "edits": [...], "ops": [...]}' | python cli_entrypoint.py reprocess-note
    {"proposed": "---\\ntype: note\\n...", "proposed_sidecar": "{...}", "title": "...",
     "clean_rejected": "", "sources": [{"role": "original", "person": "marge", ...}, ...]}
    {"refused": "the note keeps no words of its own to refile it from", "sources": [...]}

`text` and `sidecar` are the card and its sidecar as the host read them
from Forgejo; the proposal is made from them. The refile reads the note's
sources, never its summary (ADR-018): the ones the card keeps, the
replies in its message's thread the card does not hold yet, read from the
chat as the archivist, and `msg`, a correction by `by`, appended as a
`note` source. `prior`, `edits` and `ops` are what the card's history
says (`memory.card_history`, read by the host from the memory's git
history): the clean version before the first correction or edit, each
edit a person made to it, and each field edit. A note a person edited is
rebuilt as a card conversation from that clean version, and the field
edits are applied again to the card (ADR-018 §7); `left_out` names the
ones that no longer fit. Without them (a request with no `prior`) the
history is not read here: the bot runner leaves that to the host. `by`
is who asked for the refile either way, and the sidecar's
`made.requested_by` names them. `sources` is that list as
`memory.note_card.note_sources` gives it, each with its quote, so what is
shown is what the model read.

Nothing here writes: the vault through a mirror that only composes the
files, the tag vocabulary through a cache that does not record. Exit 0
with one JSON object on stdout for a proposal or a refusal, 1 when no
proposal could be made (no model answer, no mirror settings).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from capture_pipeline import CapturePipeline, words_refusal
from capture_tags import CaptureTagCache
from git_mirror import Filed
from pipeline import Classifier, PaperlessAPI

from cli._chat import read_note_thread
from cli._mirror import _DATA_DIR, build_mirror_like_bot, read_bot_toml_settings
from cli._shared import err

# Sibling stacklets resolve through `stacklets/` on sys.path: in the
# bot-runner container that directory is the read-only mount, locally it
# is the source tree. The path goes on here, before the import that needs
# it, rather than relying on who loads whom (as in `pipeline.py`).
_STACKLETS_DIR = Path(__file__).resolve().parents[3]
if str(_STACKLETS_DIR) not in sys.path:
    sys.path.insert(0, str(_STACKLETS_DIR))
from memory.card_events import edit_from_json  # noqa: E402
from memory.card_history import CardHistory  # noqa: E402
from memory.field_ops import op_from_json  # noqa: E402
from memory.note_card import (  # noqa: E402
    ORIGINAL, UNKNOWN, Source, SourcesMismatch, card_sources, note_sources, read_sidecar,
    sidecar_path,
)


class _PreviewMirror:
    """A mirror that reads the one card it was given and composes, never writes.

        preview = _PreviewMirror(mirror, "marge/notes/2026/10/trip-3a338e.md", text)
        await pipeline.reprocess(vault_path=preview.path, ...)
        preview.entry.content     # the card the refile would write
        preview.entry.sidecar     # and its sidecar
    """

    def __init__(self, mirror, path: str, text: str, sidecar: str = ""):
        self._mirror = mirror
        self.path = path
        self._files = {path: text, sidecar_path(path): sidecar or None}
        self._text = text
        self.entry = None

    async def read_capture(self, path: str) -> str | None:
        return self._files.get(path)

    async def card_history(self, path: str, card_id: str) -> None:
        """The host reads the card's history and sends it with the request;
        a request without one refiles from the card as it reads now."""
        return None

    async def publish_capture(self, *, entity: str, existing_path: str | None = None,
                              changed_by: str | None = None, **entry) -> Filed:
        # Who the commit is by is decided where it is made: the host's
        # `write_card`, not this composing step (`changed_by` is unused here).
        self.entry = self._mirror.capture_entry(
            target_path=self.path, existing=self._text, **entry)
        return Filed(self.path, str(self.entry.frontmatter.get("card_id") or ""))


class _UnrecordedTags:
    """The capture-tag vocabulary, read as the bot reads it; a preview adds nothing to it."""

    def __init__(self, tags: CaptureTagCache):
        self._tags = tags

    def top(self, n: int) -> list[str]:
        return self._tags.top(n)

    def record(self, *_args, **_kwargs) -> None:
        """A preview's tags are not the family's until it is applied."""

    def save(self) -> None:
        """Nothing was recorded."""


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
    """The card's history as the host read it (`prior`, `edits`, `ops`); None
    for a request without one."""
    if request.get("prior") is None:
        return None
    return CardHistory(
        prior=str(request["prior"]),
        edits=tuple(edit_from_json(e) for e in request.get("edits") or [] if isinstance(e, dict)),
        ops=tuple(op_from_json(o) for o in request.get("ops") or [] if isinstance(o, dict)))


def _refusal(sources: list[Source]) -> str:
    """Why the note cannot be refiled from these sources; "" when it can."""
    original = next((s for s in sources if s.role == ORIGINAL), None)
    if original is None:
        return words_refusal("")
    return words_refusal(original.words, has_file=bool(original.media_link))


def _filer(sidecar: dict, sources: list[Source]) -> str:
    """Who filed the note: its sidecar says so, else its original's sender."""
    original = next((s for s in sources if s.role == ORIGINAL), None)
    person = str(sidecar.get("filed_by") or (original.person if original else ""))
    return "" if person == UNKNOWN else person


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
    original = next((s for s in held if s.role == ORIGINAL), None)
    chat = await read_note_thread(original.id if original else "")
    sources = note_sources(text, sidecar, chat=chat.message if chat else None,
                           thread=chat.replies if chat else (), msg=msg, by=by)
    shown = [s.shown() for s in sources]
    if refused := _refusal(sources):
        print(json.dumps({"refused": refused, "sources": shown}))
        return 0

    mirror = build_mirror_like_bot()
    if mirror is None:
        err("Mirror env missing: bring up `code` so CODE_URL and the admin creds are set.")
        return 1
    preview = _PreviewMirror(mirror, path, text, side_text)
    model = _AnsweredClassifier(classifier)
    settings = read_bot_toml_settings()
    tags = CaptureTagCache(_DATA_DIR / "capture-tags.json")
    tags.load()
    pipeline = CapturePipeline(
        url_extractor=None, text_extractor=None,
        classifier=model, mirror=preview, capture_tags=_UnrecordedTags(tags),
        paperless=paperless, bot_name="archivist-bot",
        classify_max_chars=int(settings.get("classify_max_chars", 100_000)),
        capture_keep_body=bool(settings.get("capture_keep_body", False)),
        capture_tag_prompt_size=int(settings.get("capture_tag_prompt_size", 50)),
        vault=mirror.vault,
    )
    filer = _filer(sidecar, sources)
    outcome = await pipeline.reprocess(
        vault_path=path, user_hint="", sender_mxid=filer or by or "archivist-bot",
        sources=sources, requested_by=by.split(":")[0].lstrip("@") or filer,
        history=history,
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
