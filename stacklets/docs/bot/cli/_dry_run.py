"""DryRunPaperless — no-op writer for `--dry-run` modes.

Wraps a real PaperlessAPI so `enrich_document` / `reformat_document`
compute their plan without touching Paperless. Reads pass through;
writes return synthetic ids and True.
"""

from __future__ import annotations

from pipeline import PaperlessAPI


class DryRunPaperless:
    """Read-through wrapper that stubs every write.

    Safe to pass wherever pipeline expects a PaperlessAPI. Reads delegate
    to the real instance; writes return synthetic ids (so `tag_ids.append`
    still works downstream) and True for update_doc.
    """

    def __init__(self, real: PaperlessAPI):
        self._real = real
        self._fake_id = 10_000_000

    async def get_doc(self, doc_id): return await self._real.get_doc(doc_id)
    async def get_tags(self): return await self._real.get_tags()
    async def get_doc_types(self): return await self._real.get_doc_types()
    async def get_correspondents(self): return await self._real.get_correspondents()

    async def update_doc(self, *a, **kw): return True

    async def create_tag(self, *a, **kw):
        self._fake_id += 1
        return self._fake_id

    async def create_doc_type(self, *a, **kw):
        self._fake_id += 1
        return self._fake_id

    async def create_correspondent(self, *a, **kw):
        self._fake_id += 1
        return self._fake_id

    # Notes — stubbed so the summary write path is exercised but nothing
    # lands on Paperless. list_notes returns empty so the idempotent
    # delete sweep is a no-op; get_current_user_id mirrors the real one
    # so the planner takes the same branch it would in a live run.
    async def get_current_user_id(self): return await self._real.get_current_user_id()
    async def list_notes(self, doc_id): return []
    async def add_note(self, *a, **kw): return True
    async def delete_note(self, *a, **kw): return True
