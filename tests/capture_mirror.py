"""A stand-in for the archivist's mirror, the writer a capture pipeline files through.

    mirror = FakeMirror()
    pipeline = CapturePipeline(..., mirror=mirror)
    await pipeline.capture_text(text="Buy charcoal", sender_mxid="@homer:simpson")
    mirror.captures[-1]["kind"]          # 'note': what the pipeline asked to write
    mirror._stored = {path: raw}         # what `read_capture` answers, for a correction

It answers the filing core's `Writer` (`stack.filing.file`) without a
memory: nothing is on file, every write lands at a fixed path, and what was
asked is kept for the test to read. The pipeline's own tests use it to pin
their branching; the filing into a real curator is tested through
`tests/unit/stacklets/test_archivist_files_into_the_memory.py`.
"""

from __future__ import annotations

from stack.filing.write import Filed


class FakeMirror:
    def __init__(self):
        self.captures: list[dict] = []
        self.emails: list[dict] = []

    async def card_history(self, card_id):
        """No history read: the note is refiled from the card as it reads now."""
        return None

    async def capture_on_file(self, **_):
        """No note is on file here: every capture is a first filing."""
        return None

    async def publish_capture(self, **kwargs):
        self.captures.append(kwargs)
        # Mirrors the publish_capture contract: where the capture landed
        # and its card id (or None on failure). Deterministic, so the
        # envelope-emission branch in _publish executes.
        kind = kwargs.get("kind") or "bookmark"
        entity = "homer"
        return Filed(f"{entity}/{kind}s/test-capture.md", f"{kind}-7f3a9c21e4")

    async def publish_email_message(self, **kwargs):
        # Email folds into a thread file via its own mirror entrypoint;
        # the path is keyed by the thread, not the individual message.
        self.emails.append(kwargs)
        entity = kwargs.get("entity") or "homer"
        return Filed(f"{entity}/emails/test-thread.md", "email-0b6e2d91aa")

    async def read_capture(self, path):
        # The simplest re-readable shape for reprocess tests.
        return self._stored.get(path)

    _stored: dict = {}
