"""stack memory correspondents — inspect the wiki's correspondent layer.

Subcommands:
    stack memory correspondents              List every correspondent in the wiki.
    stack memory correspondents show <name>  Print one correspondent's full record.

Each correspondent is a markdown page under
`<vault>/wiki/correspondents/`. The frontmatter is the machine view —
this command prints what the archivist's classifier prompt would see.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    load_correspondents_from_vault,
    vault_path_for,
)

HELP = "Inspect correspondent wiki pages"


def _vault(config):
    data_dir = config.get("data_dir") if config else None
    return vault_path_for(Path(data_dir)) if data_dir else None


def run(args, stacklet, config):
    vault = _vault(config)
    if vault is None:
        return {"error": "stack data_dir not configured"}

    correspondents = load_correspondents_from_vault(vault)

    if args and args[0] == "show":
        if len(args) < 2:
            return {"error": "usage: stack memory correspondents show <name>"}
        return _show(correspondents, args[1])

    return _list(correspondents, vault)


def _list(correspondents, vault):
    if not correspondents:
        print(f"No correspondent pages under {vault}/wiki/correspondents/")
        return {"ok": True, "count": 0}

    for c in correspondents:
        alias_note = f"  ({', '.join(c.aliases)})" if c.aliases else ""
        print(f"  {c.canonical}{alias_note}")
    print(f"\n  {len(correspondents)} correspondent(s)")
    return {"ok": True, "count": len(correspondents)}


def _show(correspondents, name: str):
    lower = name.lower()
    for c in correspondents:
        if c.canonical.lower() == lower or lower in (a.lower() for a in c.aliases):
            print(f"canonical:  {c.canonical}")
            if c.aliases:
                print(f"aliases:    {', '.join(c.aliases)}")
            if c.topics:
                print(f"topics:     {', '.join(c.topics)}")
            if c.address:
                print(f"address:    {c.address}")
            if c.phone:
                print(f"phone:      {c.phone}")
            if c.email:
                print(f"email:      {c.email}")
            if c.website:
                print(f"website:    {c.website}")
            if c.source_path:
                print(f"source:     {c.source_path}")
            return {
                "ok": True,
                "canonical": c.canonical,
                "aliases": c.aliases,
                "topics": c.topics,
            }
    return {"error": f"No correspondent matches {name!r}"}
