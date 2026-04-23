"""Pipeline classification eval — one test per case under `cases/`.

For each `<case>.pdf` + `<case>.yaml` pair: upload to a real Paperless,
wait for OCR, run `enrich_document` against the live AI stacklet,
score the classification, print a scorecard. Always passes — quality
regressions show up as failing rows in the printed scorecard, not as
red pytest output. Aggregate score lands in the session summary.

Per-case artifacts (OCR text, classification JSON, expected ground
truth, scorecard) land in `runs/<stamp>_<model>/<case>/` so a failing
case can be diagnosed without re-running the eval. Diff two runs with
`diff -ru runs/<a> runs/<b>`.

Reads the case YAML lazily inside each test so adding a case is just
"drop two files".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from tests.integration.eval.scoring import Scorecard, score_classification


CASES_DIR = Path(__file__).parent / "cases"


def _discover_cases() -> list[tuple[str, Path, Path]]:
    """Pair each case YAML with its document. Test ids = case stem."""
    pairs: list[tuple[str, Path, Path]] = []
    for yaml_path in sorted(CASES_DIR.glob("*.yaml")):
        stem = yaml_path.stem
        # Look for any matching binary; PDF first, then images.
        for ext in (".pdf", ".png", ".jpg", ".jpeg"):
            candidate = CASES_DIR / f"{stem}{ext}"
            if candidate.exists():
                pairs.append((stem, yaml_path, candidate))
                break
        else:
            raise FileNotFoundError(
                f"No document file found for case {stem!r} "
                f"(expected {stem}.pdf, .png, .jpg, or .jpeg)"
            )
    return pairs


_CASES = _discover_cases()
_AGGREGATE: list[Scorecard] = []


@pytest.fixture(scope="session", autouse=True)
def _print_aggregate():
    """Final summary across every case — runs once at session end."""
    yield
    if not _AGGREGATE:
        return
    total = sum(sc.total for sc in _AGGREGATE)
    passed = sum(sc.passed for sc in _AGGREGATE)
    pct = 100.0 * passed / total if total else 0.0
    print("\n" + "═" * 64)
    print(f"  Eval summary: {passed}/{total} field assertions ({pct:.0f}%)")
    print(f"  Cases scored: {len(_AGGREGATE)}")
    print("═" * 64 + "\n")


def _paperless_url() -> str:
    """Best-effort Paperless URL for kept-docs links — falls back to the
    test instance's port when the env doesn't carry a public URL."""
    return (os.environ.get("PAPERLESS_PUBLIC_URL")
            or os.environ.get("PAPERLESS_URL")
            or "http://localhost:42020").rstrip("/")


@pytest.mark.parametrize(
    "stem,yaml_path,doc_path",
    _CASES,
    ids=[c[0] for c in _CASES],
)
async def test_pipeline_quality(stem, yaml_path, doc_path,
                                eval_upload, ai_classifier, paperless,
                                paperless_scope, eval_run_dir):
    """Score the pipeline's output on one case. Always passes."""
    from pipeline import enrich_document

    expected = yaml.safe_load(yaml_path.read_text()).get("expected") or {}

    doc = await eval_upload(doc_path)

    # When the case has visual content, hand attachments to
    # enrich_document so the multimodal path is exercised (gated by
    # the classifier's cached vision-capability probe).
    #   image cases → one attachment, file bytes as-is
    #   scanned PDF → one per rendered page (mirrors archivist's
    #                 on_file logic so eval and prod take the same path)
    #   text-layer PDF → no attachments (text-only classify, as in prod)
    from pipeline import ImageAttachment

    images: list[ImageAttachment] | None = None
    suffix = doc_path.suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif",
        }[suffix]
        images = [ImageAttachment(data=doc_path.read_bytes(), mime=mime)]
    elif suffix == ".pdf":
        from archivist import _has_pdf_text_layer
        from pdf_render import render_pages
        pdf_bytes = doc_path.read_bytes()
        if not _has_pdf_text_layer(pdf_bytes):
            rendered = render_pages(pdf_bytes)
            if rendered:
                images = [ImageAttachment(data=p, mime="image/png")
                          for p in rendered]

    # Run only the classify half of the pipeline. Reformat + summary
    # writes are exercised by the e2e; the eval is about classification
    # quality, where prompt changes have the biggest signal.
    result = await enrich_document(
        paperless=_PassthroughPaperless(paperless),
        classifier=ai_classifier,
        doc=doc,
        images=images,
    )

    if result.llm_error:
        kind, detail = result.llm_error
        msg = f"LLM {kind} — {detail}"
        print(f"\n✗ {stem}: {msg}")
        # Still write what we have so the run dir tells the full story.
        eval_run_dir.write_case(
            name=stem, doc_id=doc["id"],
            ocr_text=doc.get("content") or "",
            actual={"error": msg}, expected=expected,
            scorecard_text=f"LLM error: {msg}",
            passed=0, total=0,
        )
        return

    actual = {
        "title":         (result.classification or {}).get("title"),
        "topics":        result.resolved_topics,
        "persons":       result.resolved_persons,
        "correspondent": result.resolved_correspondent,
        "document_type": result.resolved_type,
        "date":          (result.updates_applied.get("created")
                          or (result.classification or {}).get("date")),
        "summary":       (result.classification or {}).get("summary"),
        "facts":         (result.classification or {}).get("facts"),
    }

    sc = score_classification(stem, actual, expected)
    print(sc.render())
    _AGGREGATE.append(sc)

    case_dir = eval_run_dir.write_case(
        name=stem, doc_id=doc["id"],
        ocr_text=doc.get("content") or "",
        actual=actual, expected=expected,
        scorecard_text=sc.render(),
        passed=sc.passed, total=sc.total,
        raw_classification=result.classification,
    )
    print(f"  → artifacts:    {case_dir}")
    if os.environ.get("EVAL_KEEP_DOCS", "").lower() in ("1", "true", "yes"):
        print(f"  → kept in Paperless: {_paperless_url()}/documents/{doc['id']}/details")


# ── Sync-paperless adapter ──────────────────────────────────────────────
#
# `enrich_document` expects an async PaperlessAPI (the bot's class).
# The session-scoped `paperless` fixture from the parent conftest is the
# sync test client. Wrapping it in a thin async shim is cheaper than
# spinning up the bot's aiohttp client *just* for the read calls
# enrich_document needs (tags / doc_types / correspondents + a PATCH).

class _PassthroughPaperless:
    """Async adapter over the sync test PaperlessAPI client.

    enrich_document uses these methods only:
      - get_tags / get_doc_types / get_correspondents — entity dicts
      - update_doc — PATCH (we accept and discard, since the eval is
        scoring the *plan*, not measuring whether Paperless persists it)
      - create_tag / create_doc_type / create_correspondent — used when
        the LLM produces a value not yet in Paperless. Synthesized id
        is fine; the value is what we score.
      - notes API (get_current_user_id / list_notes / add_note /
        delete_note) — exercised by the summary write but inert here:
        the eval scores the *summary text*, not the note round-trip.
    """

    def __init__(self, sync_client):
        self._sync = sync_client
        self._fake_id = 9_900_000

    @staticmethod
    def _to_name_id(items: list[dict]) -> dict[str, int]:
        return {it["name"]: it["id"] for it in items}

    async def get_tags(self):           return self._to_name_id(self._sync.list_tags())
    async def get_doc_types(self):      return self._to_name_id(self._sync.list_document_types())
    async def get_correspondents(self): return self._to_name_id(self._sync.list_correspondents())

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

    async def get_current_user_id(self): return None
    async def list_notes(self, doc_id):  return []
    async def add_note(self, *a, **kw):  return True
    async def delete_note(self, *a, **kw): return True
