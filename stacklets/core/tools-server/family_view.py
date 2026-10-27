"""The family on the dashboard: the pills and the greeting.

The family is what `stack family` shows (`stack.family`), read through the
host API on every page, so a change is on the next page without a restart
of core. Every dashboard page opens with the family as pills.

This module holds what the server says about the family: the pills as
served, read without JavaScript (each person with the plain word for their
relation), the household's name as each language addresses it, and every
word the family bar shows, in English and German. The scripts take their
words from the page; none of them writes its own.

Pure: no web framework, so it is tested through the server's routes.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from stack.users import family_plural

# The pair words the pills label people with, shared with family.js.
KIN_WORDS = Path(__file__).parent / "dashboard" / "components" / "kin-words.json"


def language(code: str) -> str:
    return "de" if code == "de" else "en"


def called(name: str, code: str) -> str:
    """The household as the page addresses it: "Simpsons" in English, "Familie
    Simpson" in German; "" without a name."""
    name = (name or "").strip()
    if not name:
        return ""
    return f"Familie {name}" if language(code) == "de" else family_plural(name)


def answer(family: dict, code: str) -> dict:
    """The family as `GET <base>/api/family` answers it: what `stack family`
    shows, and how the page addresses the household (`called`)."""
    return {**family, "called": called(family.get("name", ""), code)}



# ── The pills, as served ─────────────────────────────────────────────────

def pills(family: dict | None, code: str) -> str:
    """The family's pills, each with the plain word for the person's relation,
    and the hint to tap one's own name. Empty when there is nobody.
    `family-bar.js` labels them relative to whoever this device remembers."""
    people = (family or {}).get("people") or []
    if not people:
        return ""
    words = WORDS[language(code)]["bar"]
    relations = _kin_words(code)["relations"]
    items = "".join(
        f'<button class="fs-pill" type="button" data-person="{_e(p["id"])}" aria-pressed="false">'
        f'<span class="fs-avatar" data-tone="{i % 7}" aria-hidden="true">{_e(p["name"][:1].upper())}</span>'
        f'<span class="fs-pill-name">{_e(_first(p["name"]))}</span>'
        + (f'<span class="fs-pill-label">{_e(relations.get(p["relation"], ""))}</span>'
           if p.get("relation") else "")
        + '</button>'
        for i, p in enumerate(people))
    return (f'<nav class="fs-pills family-bar" aria-label="{_e(words["label"])}">{items}'
            f'<span class="fs-pills-hint">{_e(words["hint"])}</span></nav>')


def page_data(code: str, base: str) -> str:
    """What the page's scripts read before anything else, as a JSON script
    element: where the dashboard is, and the words in the page's language.
    The family itself they read from `GET <base>/api/family`."""
    data = {"base": base, "language": language(code), "words": WORDS[language(code)]}
    text = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/json" id="fs-page-data">{text}</script>'


def _kin_words(code: str) -> dict:
    table = json.loads(KIN_WORDS.read_text(encoding="utf-8"))
    return table.get(language(code), table["en"])


def _first(name: str) -> str:
    return name.split()[0] if name.split() else name


def _e(text: str) -> str:
    return escape(str(text), quote=True)


# ── The words ────────────────────────────────────────────────────────────

WORDS = {
    "en": {
        "bar": {
            "label": "The family",
            "hint": "Tap your name, and this device greets you",
            "greeting": {
                "morning": "Good morning",
                "afternoon": "Good afternoon",
                "evening": "Good evening",
            },
        },
    },
    "de": {
        "bar": {
            "label": "Die Familie",
            "hint": "Tippe auf deinen Namen, dann begrüßt dich dieses Gerät",
            "greeting": {
                "morning": "Guten Morgen",
                "afternoon": "Guten Tag",
                "evening": "Guten Abend",
            },
        },
    },
}
