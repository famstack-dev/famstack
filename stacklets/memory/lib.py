"""In-process API for the memory stacklet.

The memory stacklet is a host stacklet — no container, no port. The
two surfaces it provides are:

  - **CLI commands** under `stacklets/memory/cli/` (this lib backs them).
  - **An in-process API** other stacklets import (e.g. the Archivist
    bot pulls `get_ontology(stack, lang)` to feed its classifier
    prompt).

Both surfaces ultimately resolve to the same `Ontology` instance.
Where the data comes from depends on the runtime:

  - **Pre-install (seeds only).** Right after a fresh checkout, the
    Forgejo `memory` repo does not exist yet. `load_seed_ontology()`
    reads the file shipped with the stacklet under `seeds/`. This
    path is used by `stack memory check` (sync verification against
    `stacklets/docs/taxonomy.toml`) and by tests.
  - **Live (Forgejo-backed).** After the `on_install_success` hook
    has pushed the seeds to Forgejo, `get_ontology(stack)` will fetch
    the live copy via `ForgejoClient` and cache the parsed object
    in-process. (Wired in a follow-up Phase 1 commit.)

For now the seed path is the only loader. The live path is a
placeholder so callers can pin their import shape.
"""

from __future__ import annotations

from pathlib import Path

from stack.ontology import Ontology


STACKLET_DIR = Path(__file__).resolve().parent
SEED_ONTOLOGY_PATH = STACKLET_DIR / "seeds" / "ontology.toml"


# ─── Seed loader ─────────────────────────────────────────────────────────
#
# The seed is the canonical *starting* state of an instance's memory.
# It mirrors `stacklets/docs/taxonomy.toml` with id + synonyms + keywords
# + type cross-refs added. After install, the live copy may have drifted
# (hand edits, system additions); the seed remains the version-controlled
# baseline that ships with this release.

def load_seed_ontology() -> Ontology:
    """Read the shipped seed ontology and return a parsed `Ontology`."""
    return Ontology.load(SEED_ONTOLOGY_PATH)
