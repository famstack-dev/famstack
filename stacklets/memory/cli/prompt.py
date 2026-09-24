"""stack memory prompt [--lang=de] — show the vocabulary the models are fed.

Prints every vocabulary block a model receives from the vault, read
fresh, each under a heading naming the prompts that use it: the topics
and document types from `ontology.toml`, the family members from their
person pages, and the known correspondents. Useful for reviewing what
the archivist and the diary see (does the model get the synonyms?) and
for previewing what shifts when the ontology or a person page changes.

The language is the household's (`[core] language`) unless `--lang`
names another. Nothing is written.

Example:

    stack memory prompt
    stack memory prompt --lang=de
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    DEFAULT_SHARED_BUCKET,
    brain_path_for,
    correspondents_prompt_section,
    get_ontology,
    load_correspondents_from_vault,
    load_persons_from_vault,
    persons_prompt_section,
    vault_path_for,
)

HELP = "Show the vocabulary the models are fed: topics, family, correspondents"


def _core(config) -> dict:
    return ((config or {}).get("stack") or {}).get("core") or {}


def _language(args, config) -> str:
    for a in args:
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return _core(config).get("language") or "en"


def _vault_path(config):
    data_dir = config.get("data_dir") if config else None
    return vault_path_for(Path(data_dir)) if data_dir else None


def _block(heading: str, used_by: str, body: str, empty: str) -> str:
    return f"## {heading}\nUsed by: {used_by}\n\n{body.strip() or empty}\n"


def run(args, stacklet, config):
    lang = _language(args or [], config)
    vault = _vault_path(config)
    bucket = _core(config).get("shared_bucket") or DEFAULT_SHARED_BUCKET

    ontology = get_ontology(vault).classifier_prompt_section(lang)
    # Person pages are generated projections and live in the brain.
    data_dir = (config or {}).get("data_dir")
    brain = brain_path_for(Path(data_dir)) if data_dir else None
    persons = persons_prompt_section(
        load_persons_from_vault(brain, bucket) if brain and brain.exists() else [])
    # So are correspondent pages.
    correspondents = correspondents_prompt_section(
        load_correspondents_from_vault(brain, bucket) if brain and brain.exists() else [])

    print("\n".join([
        _block("Topics and document types",
               "document classifier, search query rewrite, diary cards",
               ontology, "(the ontology is empty)"),
        # The document classifier reads both of these from the vault,
        # where generated pages no longer are, so today it receives
        # neither. The labels say so until it reads the brain.
        _block("Family members",
               "diary cards (the document classifier looks in the vault and finds none)",
               persons, "(no person pages yet)"),
        _block("Correspondents",
               "nothing yet (the document classifier looks in the vault and finds none)",
               correspondents, "(no correspondent pages yet)"),
    ]))
    return {"ok": True, "lang": lang, "length": len(ontology)}
