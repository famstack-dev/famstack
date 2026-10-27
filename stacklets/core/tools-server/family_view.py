"""The family on the dashboard: the pills, the greeting, and the first run's words.

The family is what `stack family` shows (`stack.family`), read through the
host API on every page, so a change is on the next page without a restart
of core. Every dashboard page opens with the family as pills; while the
family is empty the page is the first run instead, which sets it up in the
browser (`dashboard/first-run.js`) and writes it through the host API's
`family` set, the same path `stack family set` takes.

This module holds what the server says about the family: the pills as
served, read without JavaScript (each person with the plain word for their
relation), the household's name as each language addresses it, and every
word the first run and the family bar show, in English and German. The
scripts take their words from the page; none of them writes its own.

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


def is_empty(family: dict | None) -> bool:
    """Whether the family is known to have nobody in it yet. Unknown (the host
    API did not answer) is not empty: the first run is never shown on a guess."""
    return family is not None and not family.get("people")


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
#
# `{called}` is the household as `called` gives it, `{name}` a person's
# first name, `{you}` the username of whoever is setting up, `{domain}`
# the web address typed in.

WORDS = {
    "en": {
        "bar": {
            "label": "The family",
            "hint": "Tap your name, and this device greets you",
            "greeting": {"morning": "Good morning", "afternoon": "Good afternoon",
                         "evening": "Good evening"},
        },
        "noscript": "Setting up the family needs JavaScript in this browser. In the terminal, "
                    "./stack family set --from family.toml does the same.",
        "open": {
            "line": "hello.",
            "sub": "Let's set up your family's own server. What's your name?",
            "ask": "Your first name",
            "note": "You'll be the one who looks after it. Everything stays here, on this Mac.",
            "button": "Let's begin",
        },
        "greet": {"sub": "Nice to meet you."},
        "setup": {
            "label": "Your family server is running",
            "heading": "Who else is at home?",
            "lede": "Everyone you add gets their own account: in the chat, in Photos, "
                    "for their own memories.",
            "family": "Your family's name",
            "family_called": "famstack will call you “the {called}”.",
            "family_empty": "Your family's name, as famstack will call you.",
            "people": "Your family",
            "you": "you",
            "add": "First name",
            "add_placeholder": "First name, then Enter",
            "add_button": "Add",
            "rule": "The first two people are marked as parents. Tap a name to change it, "
                    "or to choose words like sister or brother for them.",
            "domain": "Your family's own web address",
            "optional": "optional",
            "domain_placeholder": "for example simpsons.com",
            "domain_empty": "If your family owns a web address, famstack uses it for everyone's "
                            "sign-in and chat, like {you}@yourfamily.com. Your website and email "
                            "stay as they are. If you don't have one, leave this empty.",
            "domain_warn": "That doesn't look like a web address yet. It is written like simpsons.com.",
            "domain_ok": "Everyone signs in with it, you as {you}@{domain}, and the chat uses it "
                         "too. It stays for good, so only use an address your family owns.",
            "continue": "Continue",
            "saving": "Saving…",
            "failed": "The family was not saved: {error}",
        },
        "editor": {
            "remove": "Remove",
            "relation": "In the family",
            "relations": [["parent", "Parent"], ["child", "Child"],
                          ["grandparent", "Grandparent"], ["other", "Other"]],
            "words": "For words like sister or brother",
            "word_choices": [["she", "she"], ["he", "he"], ["", "just the name"]],
            "looks_after": "Looks after the server",
            "yes_no": [["yes", "Yes"], ["no", "No"]],
            "so": "So {name} is {list}.",
            "of": "{name}'s {word}",
            "and": "and",
        },
        "welcome": {
            "line": "Welcome home, {called}.",
            "sub": "Your family's server is ready. Everything you add from here stays in this house.",
            "button": "Start your journey",
        },
    },
    "de": {
        "bar": {
            "label": "Die Familie",
            "hint": "Tippe auf deinen Namen, dann begrüßt dich dieses Gerät",
            "greeting": {"morning": "Guten Morgen", "afternoon": "Guten Tag",
                         "evening": "Guten Abend"},
        },
        "noscript": "Die Familie einzurichten braucht JavaScript in diesem Browser. Im Terminal "
                    "tut ./stack family set --from familie.toml dasselbe.",
        "open": {
            "line": "hallo.",
            "sub": "Richten wir euren eigenen Familienserver ein. Wie heißt du?",
            "ask": "Dein Vorname",
            "note": "Du kümmerst dich um ihn. Alles bleibt hier, auf diesem Mac.",
            "button": "Los geht's",
        },
        "greet": {"sub": "Schön, dich kennenzulernen."},
        "setup": {
            "label": "Euer Familienserver läuft",
            "heading": "Wer wohnt noch bei euch?",
            "lede": "Alle, die du hinzufügst, bekommen ein eigenes Konto: im Chat, in Fotos, "
                    "für ihre eigenen Erinnerungen.",
            "family": "Euer Familienname",
            "family_called": "famstack nennt euch „{called}“.",
            "family_empty": "Euer Familienname, so nennt famstack euch.",
            "people": "Eure Familie",
            "you": "du",
            "add": "Vorname",
            "add_placeholder": "Vorname, dann Enter",
            "add_button": "Hinzufügen",
            "rule": "Die ersten beiden sind als Eltern eingetragen. Tippe auf einen Namen, um das "
                    "zu ändern oder Wörter wie Schwester oder Bruder zu wählen.",
            "domain": "Eure eigene Webadresse",
            "optional": "optional",
            "domain_placeholder": "zum Beispiel simpsons.de",
            "domain_empty": "Wenn eurer Familie eine Webadresse gehört, nutzt famstack sie für "
                            "die Anmeldung und den Chat aller, etwa {you}@eurefamilie.de. Eure "
                            "Website und E-Mail bleiben, wie sie sind. Habt ihr keine, lass das "
                            "Feld leer.",
            "domain_warn": "Das sieht noch nicht wie eine Webadresse aus. Sie wird so geschrieben: "
                           "simpsons.de.",
            "domain_ok": "Alle melden sich damit an, du als {you}@{domain}, und der Chat nutzt sie "
                         "auch. Sie bleibt für immer, also nimm nur eine Adresse, die eurer "
                         "Familie gehört.",
            "continue": "Weiter",
            "saving": "Wird gespeichert…",
            "failed": "Die Familie wurde nicht gespeichert: {error}",
        },
        "editor": {
            "remove": "Entfernen",
            "relation": "In der Familie",
            "relations": [["parent", "Elternteil"], ["child", "Kind"],
                          ["grandparent", "Großelternteil"], ["other", "Andere"]],
            "words": "Für Wörter wie Schwester oder Bruder",
            "word_choices": [["she", "sie"], ["he", "er"], ["", "nur der Name"]],
            "looks_after": "Kümmert sich um den Server",
            "yes_no": [["yes", "Ja"], ["no", "Nein"]],
            "so": "{name} ist also {list}.",
            "of": "{word} von {name}",
            "and": "und",
        },
        "welcome": {
            "line": "Willkommen zu Hause, {called}.",
            "sub": "Euer Familienserver ist bereit. Alles, was ihr von hier aus dazunehmt, "
                   "bleibt in diesem Haus.",
            "button": "Auf geht's",
        },
    },
}
