"""stack memory wiki - the family wiki: bring it up to date, or clear it.

    stack memory wiki                          list these verbs
    stack memory wiki update                   the latest filings into the wiki, and wait
    stack memory wiki update --all             the whole wiki, as at night: diary, sources, every page
    stack memory wiki update --home            just the household home page
    stack memory wiki update --member homer    just one member's page
    stack memory wiki update --members         every person's page, as the person files declare them
    stack memory wiki update --topic camping   just one topic's page
    stack memory wiki update --topics          every topic page, no home or members
    stack memory wiki update --dry-run         preview home, member and topic pages, write nothing
    stack memory wiki clean                    delete every generated page (asks first)

The curator keeps the wiki current on its own: it regenerates what a
filing touched once the filings settle, and rebuilds everything once a
night. `update` and `update --all` ask it for that work now and wait
until it is done (`_curator.py`), so the curator stays the brain's only
writer. The page flags regenerate the pages named, straight away, in the
bot-runner (`bot/cli/wiki.py`); the curator commits what they write.

Updates use a splice contract: the LLM-generated body lives inside
`<!-- begin: generated --> ... <!-- end: generated -->` markers in
each page. Everything outside the markers, a welcome line, custom
frontmatter, hand-written notes, survives every regeneration.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _curator  # noqa: E402
from _common import dispatch  # noqa: E402

HELP = "The family wiki: update it (the latest filings, a page, or all of it), or clean it"

VERBS = {
    "update": "bring the latest filings into the wiki; --all rebuilds all of it, "
              "--home/--member/--members/--topic/--topics regenerate those pages",
    "clean": "delete every generated page (asks first)",
}

# Flags that name pages to regenerate directly, rather than asking the curator.
PAGE_FLAGS = ("--home", "--member", "--members", "--topic", "--topics", "--dry-run", "--dry")


def run(args, stacklet, config):
    verb, rest = (args[0], list(args[1:])) if args else ("", [])
    if verb == "update":
        return _update(rest, config)
    if verb == "clean":
        return dispatch("wiki", "clean", *rest)
    if verb:
        return {"error": f"unknown wiki command {verb!r}; use one of: {', '.join(VERBS)}"}
    return {"commands": VERBS}


def _update(flags: list[str], config: dict) -> dict:
    pages_named = [f for f in flags if f in PAGE_FLAGS]
    if "--all" in flags and pages_named:
        return {"error": f"--all rebuilds every page; drop {' '.join(pages_named)} or --all"}
    if pages_named:
        return dispatch("wiki", *flags)
    vault = _curator.Vault.of(config)
    if vault.error:
        return {"error": vault.error}
    if "--all" in flags:
        return _everything(vault)
    return _latest(vault)


def _latest(vault: _curator.Vault) -> dict:
    target = vault.target_head()
    if not target:
        return {"error": "cannot resolve memory HEAD"}
    asked = time.time()
    mirrored, err = _curator.mirrored(vault, target)
    if err:
        return {"error": err}
    rebuilt, err = _curator.pages(vault, target, since=asked)
    if err:
        return {"error": err}
    return {"mirrored": mirrored, "rebuilt": rebuilt, "target": target}


def _everything(vault: _curator.Vault) -> dict:
    took, err = _curator.everything(vault)
    if err:
        return {"error": err}
    return {"ok": True, "seconds": took}
