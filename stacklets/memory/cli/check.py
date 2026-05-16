"""stack memory check — verify the seed ontology mirrors the taxonomy.

The Archivist still seeds Paperless tags from `stacklets/docs/taxonomy.toml`.
The memory stacklet seeds Forgejo from `stacklets/memory/seeds/ontology.toml`.
Both files describe the *same* vocabulary in two shapes — the taxonomy
is a flat list of names per language, the ontology is structured with
ids, synonyms, keywords, and cross-refs.

This command guarantees the two stay in sync. It reads both files
directly (no Forgejo needed) and reports:

  - taxonomy entries with no matching ontology entry
  - ontology entries with no matching taxonomy entry

Exits 1 with a list of drifted names when anything is missing.

This is the pre-install / CI-friendly sync gate. The same logic backs
`tests/stacklets/test_ontology_taxonomy_sync.py`, which invokes this
command as a subprocess.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Set

# Make sibling lib.py importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import load_seed_ontology  # noqa: E402

# The taxonomy lives in another stacklet; we read it by path, not import.
from stack._compat import tomllib  # noqa: E402

HELP = "Check that the memory ontology mirrors the docs taxonomy"


TAXONOMY_PATH = (
    Path(__file__).resolve().parents[3]   # repo root
    / "stacklets" / "docs" / "taxonomy.toml"
)


# ─── Helpers ─────────────────────────────────────────────────────────────

def _taxonomy_names(path: Path) -> Dict[str, Dict[str, Set[str]]]:
    """Return `{lang: {"tags": set(...), "types": set(...)}}` from taxonomy.toml."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {
        lang: {
            "tags": set(section.get("tags") or []),
            "types": set(section.get("types") or []),
        }
        for lang, section in data.items()
    }


def _ontology_names_by_lang(ont) -> Dict[str, Dict[str, Set[str]]]:
    """Project the loaded ontology into the same shape for comparison."""
    langs = _languages_in(ont)
    return {
        lang: {
            "tags":  {t.name(lang)  for t in ont.topics.values()},
            "types": {dt.name(lang) for dt in ont.doctypes.values()},
        }
        for lang in langs
    }


def _languages_in(ont) -> Set[str]:
    """Collect every language code that appears anywhere in the ontology."""
    langs: Set[str] = set()
    for t in ont.topics.values():
        langs.update(t.names.keys())
    for dt in ont.doctypes.values():
        langs.update(dt.names.keys())
    return langs


def _diff(taxonomy: Dict, ontology: Dict) -> List[str]:
    """Return human-readable lines describing drift in either direction."""
    issues: List[str] = []
    for lang in sorted(set(taxonomy) | set(ontology)):
        tax = taxonomy.get(lang, {"tags": set(), "types": set()})
        ont = ontology.get(lang, {"tags": set(), "types": set()})
        for kind in ("tags", "types"):
            missing_in_ont = sorted(tax[kind] - ont[kind])
            missing_in_tax = sorted(ont[kind] - tax[kind])
            for name in missing_in_ont:
                issues.append(f"  [{lang}] {kind}: {name!r} in taxonomy.toml but not in ontology.toml")
            for name in missing_in_tax:
                issues.append(f"  [{lang}] {kind}: {name!r} in ontology.toml but not in taxonomy.toml")
    return issues


# ─── Entry point ─────────────────────────────────────────────────────────

def run(args, stacklet, config):
    ontology = load_seed_ontology()
    taxonomy = _taxonomy_names(TAXONOMY_PATH)
    ont_names = _ontology_names_by_lang(ontology)

    issues = _diff(taxonomy, ont_names)

    if issues:
        print("memory ontology / docs taxonomy drift:")
        for line in issues:
            print(line)
        return {"error": f"{len(issues)} drift(s) detected", "issues": issues}

    counts = {lang: {kind: len(v) for kind, v in by_kind.items()}
              for lang, by_kind in ont_names.items()}
    print("memory ontology and docs taxonomy are in sync.")
    for lang in sorted(counts):
        c = counts[lang]
        print(f"  [{lang}] tags: {c['tags']}, types: {c['types']}")
    return {"ok": True, "counts": counts}
