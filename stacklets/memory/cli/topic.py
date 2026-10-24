"""stack memory topic <name>: a topic's overview, its wiki page.

Topics are first-class entities: the CLI noun and the `/go/topic/<name>`
link share one model, so `stack memory topic camping` mirrors
`/go/topic/camping`. The read is host-native, straight off the brain
projection on disk, so it works even when the model is down.

    stack memory topic camping     the topic's overview, as prose
    stack memory topic             the topics that have a page

A topic's lists are read and changed with `stack memory list`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import brain_path_for  # noqa: E402

HELP = "Show a topic's overview"


def run(args, stacklet, config):
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
