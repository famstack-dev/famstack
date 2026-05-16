"""stack memory lookup <text> [--lang=en] — resolve a term against the ontology.

Returns the canonical id and localized name for whichever topic or
doctype matches `<text>`. Case-insensitive; matches against the
canonical name and the per-language synonym list.

Examples:

    stack memory lookup Insurance
      topic: insurance — Insurance

    stack memory lookup coverage
      topic: insurance — Insurance

    stack memory lookup --lang=de Versicherung
      topic: insurance — Versicherung

Returns `{"error": ...}` when nothing matches.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import get_ontology, vault_path_for  # noqa: E402

HELP = "Resolve a term against the memory ontology"


def _parse_args(args):
    lang = "en"
    text_parts = []
    for a in args:
        if a.startswith("--lang="):
            lang = a.split("=", 1)[1]
        else:
            text_parts.append(a)
    return " ".join(text_parts).strip(), lang


def _vault_path(config):
    data_dir = config.get("data_dir") if config else None
    return vault_path_for(Path(data_dir)) if data_dir else None


def run(args, stacklet, config):
    text, lang = _parse_args(args or [])
    if not text:
        return {"error": "usage: stack memory lookup <text> [--lang=en]"}

    ont = get_ontology(_vault_path(config))

    topic = ont.resolve_topic(text, lang=lang)
    if topic is not None:
        print(f"topic: {topic.id} — {topic.name(lang)}")
        return {"ok": True, "kind": "topic", "id": topic.id, "name": topic.name(lang)}

    doctype = ont.resolve_doctype(text, lang=lang)
    if doctype is not None:
        print(f"doctype: {doctype.id} — {doctype.name(lang)}")
        return {"ok": True, "kind": "doctype", "id": doctype.id, "name": doctype.name(lang)}

    return {"error": f"no match for {text!r} in language {lang!r}"}
