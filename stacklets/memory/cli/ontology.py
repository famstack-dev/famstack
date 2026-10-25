"""stack memory ontology — sync the memory's ontology with the shipped seed.

The classifier reads `ontology.toml` from the memory. When the famstack
seed gains new topics, keywords, or synonyms, this command writes the
seed over the memory's file, through the curator, without manual web-UI
edits.

    stack memory ontology              Show the difference and write the seed.
    stack memory ontology --dry-run    Show the difference and write nothing.

The write REPLACES the memory's `ontology.toml` wholesale: if the
household has hand-curated topics in Forgejo, those changes are
clobbered. Run `--dry-run` first when you suspect drift. The
non-destructive way to add a single topic to a curated vault is
still a Forgejo web-UI edit.
"""

from __future__ import annotations

import difflib

from ..lib import BOT_USERNAME, ONTOLOGY_PATH_IN_REPO, SEED_ONTOLOGY_PATH
from ._update import still_to_follow
from ..memory_api import CuratorDown, MemoryAPI, Refused, put

HELP = "Sync the live vault ontology with the shipped seed"


def run(args, stacklet, config):
    dry_run = "--dry-run" in (args or [])
    seed_text = SEED_ONTOLOGY_PATH.read_text(encoding="utf-8")
    memory = MemoryAPI.from_host(config)
    try:
        current = memory.file(ONTOLOGY_PATH_IN_REPO)
        if current is None:
            return {"error": f"the memory has no {ONTOLOGY_PATH_IN_REPO}; run `stack up memory` first"}
        if seed_text == current.text:
            print("Ontology is already in sync with the shipped seed.")
            return {"ok": True, "applied": False, "in_sync": True}

        # Always show the diff -- the admin's eyes on what is about to land
        # (or what would land in a dry run).
        for line in difflib.unified_diff(
                current.text.splitlines(), seed_text.splitlines(),
                fromfile=f"memory:{ONTOLOGY_PATH_IN_REPO}",
                tofile=str(SEED_ONTOLOGY_PATH.relative_to(SEED_ONTOLOGY_PATH.parents[3])),
                lineterm=""):
            print(line)
        if dry_run:
            print("\nDry run — nothing written. Drop `--dry-run` to apply.")
            return {"ok": True, "applied": False, "in_sync": False}

        written = memory.commit([put(ONTOLOGY_PATH_IN_REPO, seed_text, base=current.version)],
                                by=BOT_USERNAME, kind="chore(ontology)", title="sync from shipped seed")
    except (Refused, CuratorDown) as e:
        return {"error": str(e)}
    print(f"\nWrote the shipped seed to {ONTOLOGY_PATH_IN_REPO}."
          + still_to_follow(pending=written.pending, mirrored=written.mirrored))
    return {"ok": True, "applied": True, "pending": written.pending, "mirrored": written.mirrored}
