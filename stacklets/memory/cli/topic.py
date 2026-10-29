"""stack memory topic <name>: a topic's overview, its wiki page.

Topics are first-class entities: the CLI noun and the `/go/topic/<name>`
link share one model, so `stack memory topic camping` mirrors
`/go/topic/camping`. The read is host-native, straight off the brain
projection on disk, so it works even when the model is down.

    stack memory topic camping     the topic's overview, as prose
    stack memory topic             the topics that have a page
    stack memory topic rename nahprojekte naehprojekte "Nähprojekte" [--bucket homer] [--dry-run]
                                   the topic under a new id, and a new title when given

A topic's lists are read and changed with `stack memory list`.

A rename runs on memory's side of the bot runner, which plans it and
commits it through the curator (`bot/cli/topic.py` has the whole plan);
this command sends it there and shows the report.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ..lib import brain_path_for
from ._common import dispatch_report
from ._curator import pull_now
from ._update import still_to_follow

HELP = "Show a topic's overview, or rename a topic"


def run(args, stacklet, config):
    if (args or [])[:1] == ["rename"]:
        return rename(list(args[1:]), config)
    words = [a for a in (args or []) if not a.startswith("-")]
    if len(words) > 1 and words[1] in ("todo", "todos"):
        return {"error": f"a topic's todos are a list now: "
                         f"stack memory list show {words[0]}"}
    scope = " ".join(words).strip()
    brain = _brain(config)
    if brain is None or not brain.exists():
        return {"error": "no brain projection found; is the memory stacklet installed?"}
    if not scope:
        topics = _known_topics(brain)
        print("\n".join(topics) if topics else "No topic pages yet.")
        return {"topics": topics}
    return show_topic(scope, config=config)


def _brain(config) -> Path | None:
    data_dir = config.get("data_dir") if config else None
    return brain_path_for(Path(data_dir)) if data_dir else None


def _known_topics(brain: Path) -> list[str]:
    """Every topic slug that currently has an about.md in brain."""
    return sorted({
        p.parent.name for p in brain.glob("*/*/about.md")
        if p.parent.parent != brain
    })


# Strip the machine bits so the about reads as prose: the frontmatter block, the
# generated-section markers the wiki writes, and citation markers like [8].
_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_GEN_MARKER = re.compile(r"<!--\s*(?:begin|end):\s*generated\s*-->\n?")
_CITE = re.compile(r"\[\d+(?:,\s*\d+)*\]")


def show_topic(scope: str, *, config) -> dict:
    """Print a topic's brain about page -- its curated overview.

    `stack memory topic <slug>` gives the topic's summary, so the agent can
    answer "what is this / summarise it" without guessing the vault
    path. Strips frontmatter, the generated-section markers, and citation markers
    for a clean read; the raw page is `brain/<bucket>/<slug>/about.md`.
    """
    brain = _brain(config)
    if brain is None or not brain.exists():
        return {"error": "no brain projection found; is the memory stacklet installed?"}
    scope = (scope or "").strip()

    matches = sorted(brain.glob(f"*/{scope}/about.md"))
    if not matches:
        known = _known_topics(brain)
        hint = ("  topics: " + ", ".join(known)) if known else ""
        return {"error": f"no page for topic {scope!r}\n{hint}".rstrip()}

    text = matches[0].read_text(encoding="utf-8")
    text = _FRONTMATTER.sub("", text)
    text = _GEN_MARKER.sub("", text)
    text = _CITE.sub("", text)
    print(text.strip())
    return {"ok": True, "scope": scope, "bucket": matches[0].parent.parent.name}


# ── Renaming a topic ─────────────────────────────────────────────────────

def rename(argv: list[str], config) -> dict:
    """`stack memory topic rename <old> <new> [<title>] [--bucket b] [--dry-run]`."""
    if behind := pull_now(config):
        print(behind, file=sys.stderr)
    report = dispatch_report("topic", "rename", *argv)
    if "error" in report:
        return report
    _told(report)
    if report.get("committed"):
        print(still_to_follow(pending=bool(report.get("pending")),
                              mirrored=bool(report.get("mirrored"))).lstrip("\n"))
    return report


def _told(report: dict) -> None:
    """Print what the rename moved and changed, or would."""
    done, where = report["committed"], f"{report['bucket']}/"
    moved, changed = report.get("moved") or [], report.get("changed") or []
    print(f"{'Renamed' if done else 'Would rename'} {where}{report['old']} to "
          f"{where}{report['new']}: {len(moved)} file(s) moved, {len(changed)} card(s) changed"
          + ("" if done else "; nothing written"))
    for before, after in moved:
        print(f"  {before} -> {after}")
    for path in changed:
        print(f"  {path}: carries {report['new']!r}")
    if kept := report.get("list_kept"):
        print(f"  {kept} stays: another bucket's topic {report['old']!r} has it too")
    if left := report.get("links_left"):
        print("Links into the old folder that stay as they are (change them by hand):")
        for found in left:
            print(f"  {found['path']}: {found['link']}")
