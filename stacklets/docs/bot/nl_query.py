"""Natural-language query support for the archivist.

A question-mode search (`?` at the end of a chat message) routes
through `recall.resolve_search_query` to extract keywords, runs them
against the memory vault + Paperless, and then this module turns the
hit set into something the family can read directly: a synthesized
answer with `[N]` citations above a unified evidence list.

Why this lives in its own module:

  * `archivist.py` already carries the file pipeline, the Matrix
    event surface, the room-context and routing logic, and the
    classification glue. Adding the natural-language query pieces
    inline would push it past its useful size.
  * The functions here are pure -- they don't touch the bot's
    Matrix client or its long-running state. That makes them easy
    to unit-test without standing up a bot.
  * The Classifier method (`synthesize_answer`) is the single
    transport-level surface; everything else (evidence dedup,
    rendering, today-injection) is pure data shaping and belongs in
    one place where the archivist can call it without needing to
    know the prompt details.

The exported helpers:

  * `build_evidence(memory_results, paperless_results, ...)` --
    merges + dedups the two search backends into a single list of
    evidence dicts the synthesis prompt is shaped to consume.
  * `format_evidence_item(ev, n)` -- renders one row of the
    evidence list with the `[N]` prefix that matches the model's
    citation style.

Wiring into the Docker image: this file lives under
`stacklets/docs/bot/` which the bot-runner container bind-mounts at
`/stacklets:/stacklets:ro`. No Dockerfile change is required to ship
it -- the framework files in `stacklets/core/bot-runner/` are the
only ones that need explicit `COPY` lines because they live in the
runner's own build context.
"""

from __future__ import annotations

import re
from typing import Any

from search_format import memory_doc_url, paperless_doc_url
from pipeline import extract_bot_summary

# Memory lib is a sibling stacklet. The archivist already wires the
# path to sys.path on boot (see archivist.py's `_STACKLETS_DIR`
# manipulation), so the import resolves either in-container (bind
# mount) or from the source tree.
from memory.lib import body_only


# Max hits fed to the synthesis LLM. Each hit costs a title-line +
# persons + a small summary block of context. Eight fits comfortably
# in one prompt on every model the family is likely to run, and the
# LLM rarely needs more than the top few to answer a household
# question. Moves to stack.toml alongside the reformat cap when the
# docs stacklet grows its own bot config.
EVIDENCE_LIMIT = 8


def build_evidence(
    memory_results: list[dict],
    paperless_results: list[dict],
    *,
    code_public_url: str = "",
    mirror_org: str = "family",
    paperless_public_url: str = "",
    limit: int = EVIDENCE_LIMIT,
) -> list[dict]:
    """Merge memory + Paperless hits into the LLM's evidence list.

    Memory hits go first because their summaries are the curated
    layer -- the archivist wrote them with a structured format
    (prose, facts, parties), so an LLM reading them gets cleaner
    signal than from Paperless's note serialisation. Paperless hits
    fill the remainder up to `limit`.

    Dedup against the Paperless source: a memory mirror entry carries
    its `paperless_id` in frontmatter, so when both layers return the
    same document we keep the Memory hit (richer summary) and drop
    the Paperless one. This avoids the LLM seeing the same doc twice
    and counting two votes for one answer.

    Each item carries the fields the synthesis prompt expects (kind,
    title, date, persons, summary) plus a `url` for rendering so the
    evidence list shown to the family is clickable.
    """
    evidence: list[dict] = []
    seen_paperless_ids: set[int] = set()

    for r in memory_results:
        if len(evidence) >= limit:
            break
        pid = _coerce_int(r.get("paperless_id"))
        if pid is not None:
            seen_paperless_ids.add(pid)
        evidence.append({
            "kind": "Memory",
            "title": r.get("title") or r.get("rel") or "",
            "date": r.get("date") or "",
            "persons": list(r.get("persons") or []),
            # Prefer the structured summary; fall back to the excerpt
            # for vault files that predate the classifier.
            "summary": (r.get("summary") or r.get("excerpt") or "").strip(),
            "url": memory_doc_url(
                r.get("rel") or "",
                code_public_url=code_public_url,
                mirror_org=mirror_org,
            ),
            "rel": r.get("rel") or "",
        })

    for doc in paperless_results:
        if len(evidence) >= limit:
            break
        doc_id = doc.get("id")
        if isinstance(doc_id, int) and doc_id in seen_paperless_ids:
            continue
        evidence.append({
            "kind": "Paperless",
            "title": doc.get("title") or "Untitled",
            "date": (doc.get("created") or "")[:10],
            "persons": [],
            "summary": extract_bot_summary(doc),
            "url": paperless_doc_url(
                doc_id, public_url=paperless_public_url,
            ),
            "doc_id": doc_id,
        })

    return evidence


def format_evidence_item(ev: dict, n: int) -> str:
    """Render one evidence row to match the `[N]` citation style the
    synthesis prompt uses.

    The bracket prefix (instead of `1.`) keeps the family's eye
    anchored to the citation marks in the answer above -- if the LLM
    says "the last one was on 2026-03-22 [1]", `[1]` is the row
    directly below. Same shape across Memory and Paperless hits so
    the family doesn't have to context-switch when cross-referencing.
    """
    title = (ev.get("title") or "Untitled").strip()
    url = ev.get("url") or ""
    head = f"[{n}] [{title}]({url})" if url else f"[{n}] **{title}**"
    meta_bits: list[str] = []
    if ev.get("kind"):
        meta_bits.append(str(ev["kind"]))
    if ev.get("date"):
        meta_bits.append(str(ev["date"]))
    persons = [p for p in (ev.get("persons") or []) if p]
    if persons:
        meta_bits.append(", ".join(persons))
    if doc_id := ev.get("doc_id"):
        meta_bits.append(f"#{doc_id}")
    if meta_bits:
        head += " — " + " · ".join(meta_bits)
    return head


# Default number of evidence rows to show when the synthesized answer
# carried no citations -- typically a "no answer in results" deferral
# or a model that ignored the citation rule. Three is the elbow: enough
# for the family to scan options without re-introducing the
# every-hit-listed noise the citation filter is meant to remove.
EVIDENCE_FALLBACK_TOP = 3


# `[N]` and `[N, M, ...]` patterns the synthesis prompt produces.
# Anchored to digits + commas inside square brackets so it doesn't
# pick up markdown link tails like `](url)`.
_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def extract_citations(text: str) -> list[int]:
    """Pull citation numbers out of a synthesized answer.

    Matches `[N]`, `[N, M]`, and back-to-back `[N][M]` patterns the
    model is most likely to emit. Returns the unique numbers in
    first-seen order so the caller can both filter the evidence and
    keep the original numbering aligned with the answer's brackets.
    """
    seen: list[int] = []
    for match in _CITATION_RE.finditer(text or ""):
        for part in match.group(1).split(","):
            stripped = part.strip()
            if not stripped.isdigit():
                continue
            n = int(stripped)
            if n not in seen:
                seen.append(n)
    return seen


def select_evidence_for_display(
    evidence: list[dict],
    citations: list[int],
    fallback_top: int = EVIDENCE_FALLBACK_TOP,
) -> list[tuple[int, dict]]:
    """Pick which evidence rows to display under a synthesized answer.

    When the answer cited specific hits, return only those (in cited
    order, preserving the original 1-based numbering so the answer's
    `[N]` still points at the right row -- renumbering would break
    the citation link). When no citations are present, return the
    top `fallback_top` rows so the family always has *something*
    actionable to look at, even when the model said "I don't know"
    or skipped citation entirely.

    Citations that fall outside the evidence range (a model that
    hallucinates a hit number) are silently dropped -- they'd render
    as empty placeholders otherwise.
    """
    if citations:
        out: list[tuple[int, dict]] = []
        for n in citations:
            if 1 <= n <= len(evidence):
                out.append((n, evidence[n - 1]))
        return out
    return [(i + 1, ev) for i, ev in enumerate(evidence[:fallback_top])]


# ── Deep-dive on deferral ────────────────────────────────────────────────

# Max characters of full-document content per hit when we re-feed the
# LLM for a second-turn answer. A short invoice fits comfortably; a
# 20-page contract gets truncated. The synthesizer is expected to
# answer from whatever fits -- if it can't, the deferral fires a
# second time and the bot surfaces the row to the user without
# pretending it has more.
DEEP_DIVE_MAX_CHARS = 4000


# Deferral pattern the synthesis prompt asks the model to use. We
# match a loose substring rather than the exact phrasing so a model
# that paraphrases ("would need to read [2] more carefully") still
# triggers the deep-dive turn. Case-insensitive so capitalization
# variants don't slip through.
_DEFERRAL_HINT_RE = re.compile(r"\bneed\s+to\s+read\b", re.IGNORECASE)


def is_deferral(answer: str) -> bool:
    """True when the synthesizer punted instead of answering.

    The synthesis prompt instructs the model to reply with
    "I'd need to read [N] in detail to answer that." when the
    summaries alone aren't enough. The bot uses that signal as the
    trigger for an automatic deep-dive turn -- read the cited docs
    in full, re-synthesize against the bigger context, give the
    family the actual answer.
    """
    return bool(_DEFERRAL_HINT_RE.search(answer or ""))


def expand_to_full_content(
    evidence_subset: list[tuple[int, dict]],
    memory_results: list[dict],
    paperless_results: list[dict],
    *,
    max_chars: int = DEEP_DIVE_MAX_CHARS,
) -> list[dict]:
    """Build a fresh evidence list whose summaries are full doc text.

    For each (original_n, ev) in `evidence_subset`, finds the matching
    source hit and replaces the `summary` field with the full content:

      * Memory: reads the file from disk and strips the YAML
        frontmatter (the body is the prose the model needs to read).
      * Paperless: uses the `content` field the search response
        already carried -- it's the OCR text the API serves back, no
        extra round-trip needed.

    Truncates each piece to `max_chars` so a single long PDF doesn't
    blow the LLM context. Returns a new list of dicts (does not
    mutate the originals). The returned items keep `url` / `doc_id` /
    `rel` so the second-turn renderer can still link back to the
    source the family can open.
    """
    expanded: list[dict] = []
    for _orig_n, ev in evidence_subset:
        full = ""
        if ev.get("kind") == "Memory":
            rel = ev.get("rel")
            for r in memory_results:
                if r.get("rel") != rel:
                    continue
                path = r.get("path")
                if path is None:
                    break
                try:
                    raw = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    raw = ""
                full = body_only(raw).strip()
                break
        elif ev.get("kind") == "Paperless":
            doc_id = ev.get("doc_id")
            for doc in paperless_results:
                if doc.get("id") == doc_id:
                    full = (doc.get("content") or "").strip()
                    break
        # Fall back to the existing summary if for some reason the
        # full text couldn't be loaded. The synthesizer still gets
        # *something* to work with on the second pass.
        if not full:
            full = (ev.get("summary") or "").strip()
        if len(full) > max_chars:
            full = full[:max_chars] + "\n..."
        new_ev = dict(ev)
        new_ev["summary"] = full
        expanded.append(new_ev)
    return expanded


# ── Helpers ─────────────────────────────────────────────────────────────

def _coerce_int(value: Any) -> int | None:
    """Best-effort int conversion for frontmatter values that may
    arrive as ints, strings, or missing entirely.

    A memory file's `paperless_id` is the dedup key against the
    Paperless source. Frontmatter parsing keeps numbers as strings
    today, so this converts cleanly; non-numeric / blank values come
    back as None so the dedup set skips them.
    """
    if value in (None, "", 0):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
