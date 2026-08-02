"""Search-result formatters — memory and Paperless hits as Matrix blocks.

The archivist calls these once per hit when assembling a `_handle_search`
reply. They live here so the bot stays focused on event-loop work and
so the formatting is easy to unit-test against canned hit dicts.

Visual contract (one hit per block):

    1. [Title](https://forgejo.example/...) — 2026-02-10 · Homer
       _Brummen aus dem linken Hinterrad ab 60 km/h_

Title carries the source link when a public URL is configured;
otherwise the title is bolded and the relative path appears in
backticks on the second line. The excerpt is rendered as a quiet
italic line so a fast scan picks up the relevance signal without
the path noise.

The order of metadata (date · persons) mirrors how the family
talks about documents: when something happened, then who it
involved. Paperless hits get (date · #doc_id) for the same reason
-- date first, identifier second.
"""

from __future__ import annotations

from typing import Optional

from stack.links import go_capture, go_docs, public


def memory_doc_url(
    rel: str, *,
    code_public_url: str = "",
    mirror_org: str = "family",
    repo_name: str = "memory",
    branch: str = "main",
) -> str:
    """Build a Forgejo blob URL for a memory-relative path.

    Returns "" when no public URL is configured -- the formatter
    falls back to a path-in-backticks view rather than emitting an
    unclickable container hostname.
    """
    if not code_public_url:
        return ""
    base = code_public_url.rstrip("/")
    return f"{base}/{mirror_org}/{repo_name}/src/branch/{branch}/{rel}"


def paperless_doc_url(
    doc_id: Optional[int], *,
    link_base_url: str = "",
) -> str:
    """Build a persistent `/go/docs/<id>` link for a doc id.

    Search results live in Matrix history as long as the room does, so
    the link is logical rather than a Paperless URL: it resolves to the
    document wherever it lives at click time. Returns "" without a link
    base, and the formatter falls back to a bold title.
    """
    if not (link_base_url and doc_id):
        return ""
    return public(go_docs(doc_id), link_base_url)


def memory_hit_url(
    r: dict, *,
    link_base_url: str = "",
    code_public_url: str = "",
    mirror_org: str = "family",
) -> str:
    """The best durable link for one memory hit, or "" for none.

    A capture is a record, so when the hit carries the id it was
    captured with, the link is `/go/capture/<id>` and survives the file
    being re-scoped, its topic renamed, or its title corrected.

    Everything else falls back to the Forgejo blob URL. That link
    freezes today's address and today's path, which is exactly what the
    `/go` namespace exists to avoid — but a hand-written wiki page has
    no id to key on, and a worse link still beats none while that is
    true. Anything that grows a stable id should move to a record kind
    rather than widening this fallback.
    """
    if capture_id := (r.get("capture_id") or "").strip():
        if url := public(go_capture(capture_id), link_base_url):
            return url
    return memory_doc_url(
        r.get("rel", ""), code_public_url=code_public_url, mirror_org=mirror_org,
    )


def format_memory_hit(
    r: dict, n: int, *,
    code_public_url: str = "",
    mirror_org: str = "family",
    link_base_url: str = "",
) -> str:
    """Render one memory hit as a Matrix-markdown block.

    Output is 1-3 lines depending on what the hit carries:
      * title line (always; numbered, linked when possible)
      * `path` line (only when no public URL -- the user still
        needs to know where to find the doc)
      * italic excerpt (only when the search hit produced one)
    """
    title = (r.get("title") or r.get("rel") or "").strip()
    rel = r.get("rel", "")
    date = r.get("date") or ""
    persons = ", ".join(r.get("persons") or [])
    meta = " · ".join(p for p in [date, persons] if p)

    # Escape the dot after the number so python-markdown doesn't see
    # this as an ordered-list item. We join hits with blank lines for
    # paragraph separation, and a loose `<ol>` ends up rendering each
    # marker on its own line in Element; plain "1. Foo" text avoids
    # the list machinery entirely.
    url = memory_hit_url(
        r, link_base_url=link_base_url,
        code_public_url=code_public_url, mirror_org=mirror_org,
    )
    if url:
        head = f"{n}\\. [{title}]({url})"
    else:
        head = f"{n}\\. **{title}**"
    if meta:
        head += f" — {meta}"

    lines = [head]
    if not url and rel:
        # Fallback path display: gives the human enough to find the
        # file even without a public Forgejo URL.
        lines.append(f"   `{rel}`")
    excerpt = (r.get("excerpt") or "").strip()
    if excerpt:
        lines.append(f"   _{excerpt}_")
    return "\n".join(lines)


def format_paperless_hit(
    doc: dict, n: int, *,
    link_base_url: str = "",
) -> str:
    """Render one Paperless hit as a single Matrix-markdown line.

    Paperless hits are simpler than memory hits: there's no excerpt
    column and the doc_id replaces the relative path as the
    identifier. One line per hit keeps the section dense and
    skimmable.
    """
    title = (doc.get("title") or "Untitled").strip()
    doc_id = doc.get("id")
    created = (doc.get("created") or "")[:10]
    meta = " · ".join(p for p in [created, f"#{doc_id}" if doc_id else ""] if p)

    url = paperless_doc_url(doc_id, link_base_url=link_base_url)
    if url:
        head = f"{n}\\. [{title}]({url})"
    else:
        head = f"{n}\\. **{title}**"
    if meta:
        head += f" — {meta}"
    return head
