"""Find where a capture lives right now, by the id it was captured with.

`/go/capture/<id>` promises a link that survives the file moving. Every
other logical path can be resolved by rewriting the string, but this one
cannot: the id names *which* capture, and says nothing about where it is
today. Something has to look.

WHY A SCAN AND NOT AN INDEX

The obvious design is an index the curator maintains during the mirror.
It would be faster and it would be one more thing that can be wrong: an
index is a second copy of the truth, so it can go stale, disagree with
the tree, or be missing on a fresh clone, and every one of those failures
looks like a dead link.

A scan reads the same files the wiki serves, so it cannot disagree with
them, and there is nothing to rebuild or invalidate. A family vault is
hundreds to low thousands of small markdown files and a redirect happens
when a human clicks something, so the cost lands in the right place. If
a vault ever grows past that, an index can be added *behind this same
function* without touching the resolver or the link format.

WHY FRONTMATTER AND NOT THE BODY

`capture_id` is a frontmatter field, and the search path already treats
frontmatter as the machine-readable half of a page. Reading only the head
of each file keeps a scan cheap and avoids matching an id someone quoted
in prose.
"""

from __future__ import annotations

from pathlib import Path

# Frontmatter sits at the top; no capture page has a hundred lines of it.
# Bounding the read keeps a scan over a large vault from paging in whole
# documents to answer a question the header already answers.
_HEAD_BYTES = 4096

_FIELD = "capture_id:"


def _capture_id_of(path: Path) -> str:
    """The `capture_id` declared in a file's frontmatter, or "".

    Only the frontmatter block counts. An id quoted in the body of a
    note — a reply discussing another capture, say — must not make this
    file answer to it.
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return ""
    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            return ""
        if line.startswith(_FIELD):
            return line[len(_FIELD):].strip().strip("\"'")
    return ""


def find_capture(capture_id: str, *, brain_dir: Path) -> str | None:
    """Wiki-relative target for a capture id, or None if nothing carries it.

    The target is the path the wiki serves, so the `.md` suffix comes off
    — same shape `resolve_topic_target` returns for a topic page.

    Returns None rather than a guess when the id is unknown: a 404 tells
    the person the capture is gone, while a redirect to something else
    would quietly show them the wrong note.
    """
    wanted = (capture_id or "").strip()
    if not wanted:
        return None
    brain_dir = Path(brain_dir)
    if not brain_dir.is_dir():
        return None
    for path in sorted(brain_dir.rglob("*.md")):
        if _capture_id_of(path) == wanted:
            rel = path.relative_to(brain_dir)
            return str(rel.with_suffix(""))
    return None
