"""Assemble a compact, per-turn "family briefing" from the vault.

This is the *content* half of the runtime-context shim (see README.md). Given
an inbound message, it produces a few short lines telling the agent WHO it is
speaking with and, when the room maps to a known topic, WHAT that topic is —
so a family room with several people works correctly (Homer's turn is primed
with Homer, Marge's with Marge) instead of leaning on nanobot's single-user
USER.md.

Design constraints that shaped this:
- **Compact.** These lines are recomputed on every turn (they vary per speaker,
  so they sit past the KV-cache boundary). Keep them to ~150 tokens: short
  summaries plus pointers, never full pages. The agent reads the full page via
  its file tools only if it needs depth.
- **Pointers, not dumps.** We hand it the essence + the vault path, not the
  whole profile.
- **Read-only, best-effort.** Never raise: a missing page or an unmapped room
  just yields fewer lines. A briefing is an optimisation, not a dependency.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Strip footnote/citation markers the curator leaves in generated pages, e.g.
# "Safety Inspector [8]" -> "Safety Inspector". They are noise in a briefing.
_CITE = re.compile(r"\s*\[\d+(?:,\s*\d+)*\]")


def _lead(path: Path, maxwords: int = 32) -> str:
    """A one-line essence of an about.md: its tagline plus the 'About' opener."""
    if not path.exists():
        return ""
    txt = path.read_text(errors="ignore")
    if txt.startswith("---"):  # drop YAML frontmatter
        txt = txt.split("---", 2)[-1]
    txt = txt.replace("<!-- begin: generated -->", "")
    tag, about = "", ""
    lines = txt.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(">") and not tag:
            tag = s.lstrip("> ").strip()
        if s.lower().startswith("## about"):
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    about = nxt.strip()
                    break
            break
    out = _CITE.sub("", (tag + " " + about).strip())
    words = out.split()
    return " ".join(words[:maxwords]) + ("..." if len(words) > maxwords else "")


def _open_todos(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.split("]", 1)[1].strip() for ln in path.read_text(errors="ignore").splitlines()
            if ln.strip().startswith("- [ ]")]


def _recent(vault: Path, topic: str, n: int = 3) -> list[str]:
    """Recent *human* activity in a topic, from git — skipping the curator's
    own 'refresh' regen commits, which are noise, not news."""
    try:
        out = subprocess.run(
            ["git", "-C", str(vault), "log", "--pretty=%s", "-n", "15", "--", f"family/{topic}/"],
            capture_output=True, text=True, timeout=5,
        ).stdout.splitlines()
    except Exception:
        return []
    return [s for s in out if "docs(memory): refresh" not in s][:n]


def _slug_candidates(raw: str) -> list[str]:
    """Map a Matrix room label to a vault topic slug, best-effort.

    Topic rooms are named 'Topic: <Title>' with alias '#topic-<slug>'; bot-runner
    rooms are '<Name> Room'. Strip those markers, then kebab-case. (Titles with
    non-ASCII characters won't round-trip cleanly here - the canonical alias is
    the robust source; using it is a follow-up.)
    """
    s = str(raw).strip().lstrip("#").strip()
    s = re.sub(r"^topic[:\-\s]+", "", s, flags=re.I)  # "Topic: X" or "topic-x"
    s = re.sub(r"\s+Room$", "", s, flags=re.I)
    base = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return [base] if base else []


def _topic_slug(msg, vault: Path) -> str:
    raw = (getattr(msg, "metadata", {}) or {}).get("room", "")
    for cand in _slug_candidates(raw):
        if (vault / "family" / cand / "about.md").exists():
            return cand
    return ""


def brief_lines(msg, workspace) -> list[str]:
    """Return the briefing as a list of short runtime-context lines (may be empty)."""
    vault = Path(workspace) / "vault"
    if not vault.exists():
        return []
    lines: list[str] = []

    # WHO — the speaker. sender_id is an mxid like "@homer:simpson"; the localpart
    # is the vault person slug. This is the reliable, high-value part: it fixes
    # multi-user rooms where "me/my" must resolve to whoever actually spoke.
    localpart = str(getattr(msg, "sender_id", "")).split(":")[0].lstrip("@")
    if localpart:
        essence = _lead(vault / localpart / "about.md")
        head = f"You are speaking with {localpart} (@{localpart})."
        lines.append(f"{head} {essence}" if essence else head)
        lines.append(f"(Their full profile: vault/{localpart}/about.md)")

    # WHAT — the topic, when the room maps to one. Best-effort.
    topic = _topic_slug(msg, vault)
    if topic:
        summary = _lead(vault / "family" / topic / "about.md")
        if summary:
            lines.append(f"Topic '{topic}': {summary}")
        if _open_todos(vault / "family" / topic / "todos.md"):
            # Presence, not the data, and not even the count: a number is still
            # recitable and still goes stale. Just that todos exist and how to
            # fetch the live list when someone actually asks.
            lines.append(f"This topic has open todos; run "
                         f"`stack memory topic {topic} todo` to see them.")
        recent = _recent(vault, topic)
        if recent:
            lines.append("Recently: " + "; ".join(recent))
        lines.append(f"(Full topic: vault/family/{topic}/)")

    return lines
