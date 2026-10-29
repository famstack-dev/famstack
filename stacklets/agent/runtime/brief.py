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
import unicodedata
from datetime import datetime
from pathlib import Path

from turn import person_of

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


def _lists_with_open_items(vault: Path, topic: str) -> list[str]:
    """Slugs of the lists that name `topic` and have an open item."""
    return [path.stem for path in _lists_naming(vault, topic)
            if re.search(r"^\s*[-*+] \[ \] ", path.read_text(errors="ignore"), re.M)]


def _lists_naming(vault: Path, topic: str) -> list[Path]:
    """The list files (`family/lists/<slug>.md`) whose `topics:` name `topic`.

    Read without the framework, which this container does not carry: the
    `topics:` block of the frontmatter is all it needs.
    """
    found = []
    for path in sorted((vault / "family" / "lists").glob("*.md")):
        head = path.read_text(errors="ignore").partition("\n---\n")[0]
        block = re.search(r"^topics:\n((?:  - .*\n?)+)", head, re.M)
        named = [ln.strip()[2:].strip().strip('"') for ln in block[1].splitlines()] if block else []
        if topic in named:
            found.append(path)
    return found


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


# The archivist binds a room named "Thema: <Name>" or "Topic: <Name>" to the
# topic it derives from the name (stacklets/docs/bot/topic_rooms.py). This
# container cannot import that module, so the rule is restated here, and a
# unit test holds the two to the same answers. A room renamed after it was
# bound keeps its first slug, which a name cannot tell; such a room is missed.
# A topic renamed with `stack memory topic rename` is found: its card lists
# the id the room's name still derives (`_renamed`).
_TOPIC_ROOM = re.compile(r"^\s*(?:thema|topic)\s*:\s*(?P<body>\S.*?)\s*$", re.IGNORECASE)
_MAX_SLUG_CHARS = 40


def topic_slug(display: str) -> str:
    """The slug the archivist gives a topic room named `display`: `Einkäufe` → `einkaufe`."""
    folded = unicodedata.normalize("NFKD", display)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()
    folded = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    if len(folded) > _MAX_SLUG_CHARS:
        folded = folded[:_MAX_SLUG_CHARS]
        if (cut := folded.rfind("-")) > 0:
            folded = folded[:cut]
        folded = folded.strip("-")
    return folded


def _slug_candidates(raw: str) -> list[str]:
    """The topic slugs a room label could stand for, the archivist's first.

    Besides `Thema: …` and `Topic: …`, a label can be a room's alias
    (`#topic-camping`) or a bot-runner room's name (`Camping Room`).
    """
    label = str(raw).strip()
    candidates = []
    if room := _TOPIC_ROOM.match(label):
        candidates.append(topic_slug(room["body"]))
    alias = re.sub(r"^topic[-\s]+", "", label.lstrip("#").strip(), flags=re.I)
    candidates.append(topic_slug(re.sub(r"\s+Room$", "", alias, flags=re.I)))
    return [c for c in dict.fromkeys(candidates) if c]


def _topic_known(vault: Path, slug: str) -> bool:
    """A topic the vault knows: it has a page, or a list belongs to it.

    A room kept only for lists, such as a shopping room, has no page until
    something is filed there, and is still the place its lists belong.
    """
    return (vault / "family" / slug / "about.md").exists() or bool(_lists_naming(vault, slug))


def _renamed(vault: Path, slug: str) -> str:
    """The family topic's id now, when a rename gave the topic `slug` another; else "".

    The topic card, `family/<id>/topic.md`, lists the ids it had before in
    `former_topic_ids`. The rule of `stack.cards.lookup.renamed_topic`,
    restated because this container does not carry the framework.
    """
    family = vault / "family"
    # A folder with no file in it is no topic: git keeps files, not folders.
    if (family / slug).is_dir() and any(p.is_file() for p in (family / slug).rglob("*")):
        return ""
    for card in sorted(family.glob("*/topic.md")):
        head = card.read_text(errors="ignore").partition("\n---\n")[0]
        block = re.search(r"^former_topic_ids:\n((?:  - .*\n?)+)", head, re.M)
        former = [ln.strip()[2:].strip().strip('"') for ln in block[1].splitlines()] if block else []
        if slug in former:
            return card.parent.name
    return ""


def topic_for_room_label(raw: str, vault: Path) -> str:
    """The vault topic a room label maps to, or "" when it maps to none.

    Exposed (rather than folded into `_topic_slug`) because joining a
    room needs the same answer before there is any message to derive it
    from: a room with a topic gets a different greeting from a DM, and
    guessing wrong produces a bot that promises to describe a topic
    that does not exist.
    """
    for candidate in _slug_candidates(raw):
        if _topic_known(vault, candidate):
            return candidate
        if renamed := _renamed(vault, candidate):
            return renamed
    return ""


def _topic_slug(msg, vault: Path) -> str:
    raw = (getattr(msg, "metadata", {}) or {}).get("room", "")
    return topic_for_room_label(raw, vault)


def when_sent(now: datetime) -> str:
    """When this message was sent, in the household's clock.

    nanobot 0.3 states no date or time anywhere in the prompt, so without
    this line "by Friday" or "tomorrow" cannot become a date. The block it
    sits in stays with its message in the history, which also dates every
    earlier message for "what did we say yesterday".
    """
    return f"Sent {now:%A, %Y-%m-%d %H:%M} ({now.tzname()})."


# Stated with every message, next to it: the room's history in another
# language outweighs a rule at the top of the prompt.
ANSWER_LANGUAGE = "Answer in the language this message is written in."


def brief_lines(msg, workspace) -> list[str]:
    """Return the briefing as a list of short runtime-context lines (may be empty).

    nanobot's workspace holds the family memory as `vault/`.
    """
    return brief_for(msg, Path(workspace) / "vault")


def brief_for(msg, vault: Path) -> list[str]:
    """The briefing for a message, read from the family memory at `vault`,
    for a runtime that has the memory somewhere other than nanobot does.

    `msg` needs `sender_id` (the Matrix id) and `metadata["room"]` (the
    room's display name).
    """
    vault = Path(vault)
    if not vault.exists():
        return []
    lines: list[str] = []

    # WHO — the speaker. sender_id is an mxid like "@homer:simpson"; the localpart
    # is the vault person slug. This is the reliable, high-value part: it fixes
    # multi-user rooms where "me/my" must resolve to whoever actually spoke.
    localpart = person_of(getattr(msg, "sender_id", ""))
    if localpart:
        essence = _lead(vault / localpart / "about.md")
        head = f"You are speaking with {localpart} (@{localpart})."
        lines.append(f"{head} {essence}" if essence else head)
        lines.append(f"(Their full profile: vault/{localpart}/about.md)")

    # WHAT — the topic, when the room maps to one. Best-effort.
    topic = _topic_slug(msg, vault)
    if topic:
        # Pointers, not data: name the topic and how to fetch its overview, so the
        # model reads the real page instead of reciting a stale snippet (the trap
        # the todo list was). Consistent with the todos pointer just below.
        overview = (vault / "family" / topic / "about.md").exists()
        lines.append(f"This room's topic is '{topic}'"
                     + (f"; run `stack memory topic {topic}` for its overview." if overview else "."))
        lists = _lists_with_open_items(vault, topic)
        if lists:
            # Presence, not the data, and not even the count: a number is still
            # recitable and still goes stale. Just which lists have open items
            # and how to fetch one when someone actually asks.
            lines.append(f"This topic's lists with open items: {', '.join(lists)}; run "
                         f"`stack memory list show <list>` to see one.")
        recent = _recent(vault, topic)
        if recent:
            lines.append("Recently: " + "; ".join(recent))
        if overview:
            lines.append(f"(Full topic: vault/family/{topic}/)")

    return lines
