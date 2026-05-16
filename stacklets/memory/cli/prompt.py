"""stack memory prompt [--lang=en] — render the classifier prompt section.

Outputs the topics + doctypes block the Archivist injects into its
classification prompt. Useful for debugging classifier behaviour
(does the LLM actually see the synonyms?) and for previewing what
shifts when the ontology evolves.

Example:

    stack memory prompt --lang=de
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import load_seed_ontology  # noqa: E402

HELP = "Render the classifier prompt section for a language"


def _parse_args(args):
    lang = "en"
    for a in args:
        if a.startswith("--lang="):
            lang = a.split("=", 1)[1]
    return lang


def run(args, stacklet, config):
    lang = _parse_args(args or [])
    ont = load_seed_ontology()
    section = ont.classifier_prompt_section(lang)
    print(section)
    return {"ok": True, "lang": lang, "length": len(section)}
