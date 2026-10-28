"""The activity widget: what changed in the memory, in a sentence each.

The dashboard's first widget, and the pattern the next one copies
(`dashboard/widgets/README.md`). It reads the memory's history from the
cards API (`GET /history`, `stack.cards.activity`), which says
what each change was, and words it for the people reading the page:

    Marge filed a note: Trip prep
    Homer replied to a note: Tent is torn
    The memory was updated (20 notes)
    The diary was updated (42 entries)

No commit subjects, hashes or file paths: a card's details are one step
deeper, on the card.

`view()` builds what the widget shows, as JSON: the days with their
entries, each sentence worded, each card with the link that opens it for
the person looking, and for every part the sizes that show it
(`show`, the size contract in the README). The page renders that view
as HTML (`html()`), so it reads complete without JavaScript; the
widget's script (`fs-activity.js`) renders the same view when it
refreshes. One wording, in one place.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha1
from html import escape
from urllib.parse import urlencode

# What the large size shows first, and how many more each "Show more" adds.
LIMIT = 12
STEP = 20
MOST = 100
# A sentence names up to this many cards; past that, the first two and
# "and N more".
CARDS_NAMED = 3
# The medium size shows the most recent day's first entries, this many.
MEDIUM_ENTRIES = 4
# Changes that are about the whole memory rather than something a person did.
_WHOLE = ("migrated", "seeded")
# Kinds of the stack's own work that name a person only because the cards
# are theirs (the memory decides `machine`): the page names no one for them.
_MADE_FOR = ("updated", "listed", "declared", "moved", "transcribed")
# Avatar colours: a person keeps theirs (`--tone-<n>` in tokens.css), by a
# hash of their id, so it never changes and needs no roster.
_TONES = 7

ALL, MEDIUM_UP, LARGE, SMALL, MEDIUM = ("small medium large", "medium large", "large",
                                        "small", "medium")


# ── Words ─────────────────────────────────────────────────────────────────
# Per language, as the card browser has them: German, else English. A type
# is (one, with its article, as the sentence needs it; several; one, as the
# subject of a sentence).

WORDS = {
    "en": {
        "title": "Activity", "see_all": "See all", "more": "Show more",
        "empty": "Nothing has happened yet.", "error": "The memory does not answer right now.",
        "quiet": "Quiet so far. The first letter, photo or memory shows up here.",
        "retry": "Try again", "today": "Today", "yesterday": "Yesterday", "just_now": "just now",
        "minutes": ("{n} minute ago", "{n} minutes ago"), "hours": ("{n} hour ago", "{n} hours ago"),
        "glance": ("Nothing new today", "1 change today", "{n} changes today"),
        "weekdays": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
        "months": ("January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"),
        "date": "{weekday}, {day} {month}", "date_year": "{weekday}, {day} {month} {year}",
        "kinds": {"filed": "filed {thing}", "updated": "updated {thing}",
                  "replied": "replied to {thing}", "corrected": "corrected {thing}",
                  "edited": "edited {thing}", "refiled": "refreshed {thing}",
                  "removed": "removed {thing}", "changed": "changed {thing}"},
        "refreshed": "{things} refreshed", "requested": "{text} at {name}'s request",
        "and": " and ", "rest": " and {n} more",
        "whole": {"migrated": "The memory was updated ({things})",
                  "seeded": "The memory was set up ({things})"},
        "diary_updated": "The diary was updated ({entries})", "entries": ("1 entry", "{n} entries"),
        "types": {"note": ("a note", "notes", "Note"), "bookmark": ("a bookmark", "bookmarks", "Bookmark"),
                  "document": ("a document", "documents", "Document"),
                  "diary": ("a diary entry", "diary entries", "Diary entry"),
                  "list": ("a list", "lists", "List"), "person": ("a person", "people", "Person"),
                  "topic": ("a topic", "topics", "Topic"),
                  "correspondent": ("a correspondent", "correspondents", "Correspondent"),
                  "email": ("an email", "emails", "Email")},
        "card": ("a card", "cards", "Card"),
    },
    "de": {
        "title": "Aktivität", "see_all": "Alle zeigen", "more": "Mehr zeigen",
        "empty": "Noch ist nichts passiert.", "error": "Das Gedächtnis antwortet gerade nicht.",
        "quiet": "Noch ist es ruhig. Der erste Brief, das erste Foto oder die erste Erinnerung erscheint hier.",
        "retry": "Erneut versuchen", "today": "Heute", "yesterday": "Gestern", "just_now": "gerade eben",
        "minutes": ("vor {n} Minute", "vor {n} Minuten"), "hours": ("vor {n} Stunde", "vor {n} Stunden"),
        "glance": ("Heute nichts Neues", "1 Änderung heute", "{n} Änderungen heute"),
        "weekdays": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
        "months": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                   "September", "Oktober", "November", "Dezember"),
        "date": "{weekday}, {day}. {month}", "date_year": "{weekday}, {day}. {month} {year}",
        "kinds": {"filed": "hat {thing} abgelegt", "updated": "hat {thing} aktualisiert",
                  "replied": "hat auf {thing} geantwortet", "corrected": "hat {thing} korrigiert",
                  "edited": "hat {thing} bearbeitet", "refiled": "hat {thing} aufgefrischt",
                  "removed": "hat {thing} entfernt", "changed": "hat {thing} geändert"},
        "refreshed": "{things} aufgefrischt", "requested": "{text}, auf Wunsch von {name}",
        "and": " und ", "rest": " und {n} weitere",
        "whole": {"migrated": "Das Gedächtnis wurde aktualisiert ({things})",
                  "seeded": "Das Gedächtnis wurde angelegt ({things})"},
        "diary_updated": "Das Tagebuch wurde aktualisiert ({entries})",
        "entries": ("1 Eintrag", "{n} Einträge"),
        "types": {"note": ("eine Notiz", "Notizen", "Notiz"),
                  "bookmark": ("ein Lesezeichen", "Lesezeichen", "Lesezeichen"),
                  "document": ("ein Dokument", "Dokumente", "Dokument"),
                  "diary": ("einen Tagebucheintrag", "Tagebucheinträge", "Tagebucheintrag"),
                  "list": ("eine Liste", "Listen", "Liste"), "person": ("eine Person", "Personen", "Person"),
                  "topic": ("ein Thema", "Themen", "Thema"),
                  "correspondent": ("einen Korrespondenten", "Korrespondenten", "Korrespondent"),
                  "email": ("eine E-Mail", "E-Mails", "E-Mail")},
        "card": ("eine Karte", "Karten", "Karte"),
    },
}


def words(language: str) -> dict:
    return WORDS["de" if language == "de" else "en"]


# ── The view: what the widget shows, at each size ────────────────────────

def view(history: dict | None, *, now: datetime, language: str, href: Callable[[dict], str],
         base: str, limit: int = LIMIT, audience: str = "", size: str = "",
         no_memory: bool = False) -> dict:
    """What the widget shows for the cards API's `history` (None: it did
    not answer), read at `now` (aware, in the instance's time zone).
    `no_memory`: the memory was never set up, so nothing answering is no
    error; the widget is quiet and says what will fill it.

    `history` holds up to `limit` + 1 changes: one more than is shown
    says there is more. `href` is the link of a card for the person
    looking. `base` is the dashboard's path, for its own links. `size`
    is the size the page fixed the widget at, if it did: a large widget
    needs no link to the large size.
    """
    w = words(language)
    shown = (history or {}).get("changes", [])[:limit]
    state = ("empty" if no_memory else "error") if history is None else "content" if shown else "empty"
    entries = [_entry(change, w, now, href) for change in _runs(shown, now)]
    days = _days(entries, w, now)
    today = days[0]["entries"] if days and days[0]["day"] == now.date().isoformat() else []
    hidden_in_medium = len(days) > 1 or (days and len(days[0]["entries"]) > MEDIUM_ENTRIES)
    has_more = history is not None and len(history.get("changes", [])) > limit

    def page(**query) -> str:
        query = {k: v for k, v in {**query, "audience": audience}.items() if v}
        return f"{base}?{urlencode(query)}"

    more = None
    if has_more or (hidden_in_medium and size != "large"):
        bigger = min(limit + STEP, MOST) if has_more else limit
        more = {"label": w["more"], "limit": bigger, "show": MEDIUM_UP if has_more else MEDIUM,
                "href": page(size="large", limit=bigger)}
    count = sum(e["changes"] for e in today)  # changes, not lines: a run counts each
    return {
        "state": state, "language": language, "title": w["title"],
        "empty": w["quiet"] if no_memory else w["empty"], "error": w["error"], "retry": w["retry"],
        "now": w["just_now"],
        "src": f"{base}/api/activity?" + urlencode({k: v for k, v in
                                                    {"limit": limit, "audience": audience}.items() if v}),
        "retry_href": page(limit=limit if limit != LIMIT else ""),
        "see_all": {"label": w["see_all"], "href": page(size="large"), "show": SMALL},
        "glance": {"text": w["glance"][min(count, 2)].format(n=count), "show": SMALL},
        "days": days, "more": more,
    }


def _runs(changes: list[dict], now: datetime) -> list[dict]:
    """The changes with each run of the same thing folded into one.

    Consecutive changes on one day, by the same person, of the same kind,
    to cards of one type, are one line: six edits to six people's cards
    read "Marge edited 6 people". A card in the run twice counts once.
    `changes` says how many changes a line stands for.
    """
    runs: list[dict] = []
    for change in changes:
        key = _run_key(change, now)
        if runs and key is not None and runs[-1]["key"] == key:
            run = runs[-1]
            known = {c["id"] for c in run["cards"]}
            fresh = [c for c in change["cards"] if c["id"] not in known]
            unlisted = max(int(change.get("count", 0)) - len(change["cards"]), 0)
            run["cards"] += fresh
            run["count"] += len(fresh) + unlisted
            run["types"] = {key[3]: run["count"]}
            run["changes"] += 1
        else:
            runs.append({**change, "key": key, "cards": list(change["cards"]), "changes": 1,
                         "count": int(change.get("count", 0))})
    return runs


def _run_key(change: dict, now: datetime) -> tuple | None:
    """What a change shares with the next one in its run; None for a change
    over cards of several types, which stands alone."""
    types = change.get("types") or {c["type"]: 1 for c in change["cards"]}
    if len(types) != 1:
        return None
    day = datetime.fromisoformat(change["at"]).astimezone(now.tzinfo).date()
    # A change that names no one joins its run whoever's cards it touched:
    # the diary compile commits each sender's entries apart, one line here.
    who = "" if _names_no_one(change) else change.get("who")
    return (day, who, change.get("kind"), next(iter(types)), change.get("asked_by", ""))


def _names_no_one(change: dict) -> bool:
    """A change to the whole memory, or one the stack made again on its
    own (`machine`, decided by the memory from its author and committer):
    it says what happened to the cards, with nobody as its subject."""
    kind = change.get("kind", "changed")
    return kind in _WHOLE or (kind in _MADE_FOR and bool(change.get("machine")))


def _entry(change: dict, w: dict, now: datetime, href: Callable[[dict], str]) -> dict:
    """One change (or one run of them) as a sentence: who, what they did,
    which cards.

    Work the memory did on its own has no one to name: a migration is
    about the whole memory, an update the stack made again is about the
    diary or the memory ("The diary was updated"), a refile is about the
    card ("Note refreshed"), with the person who asked for it when that
    was a person.
    """
    at = datetime.fromisoformat(change["at"]).astimezone(now.tzinfo)
    kind, count = change.get("kind", "changed"), int(change.get("count", 0))
    named, rest = _named(change, count, w, href)
    if kind in _WHOLE:
        who, named, rest = None, [], ""
        text = w["whole"][kind].format(things=_things(change, count, w))
    elif _names_no_one(change):
        who, named, rest = None, [], ""
        types = change.get("types") or {c["type"]: 1 for c in change["cards"]}
        text = (w["diary_updated"].format(entries=w["entries"][count != 1].format(n=count))
                if set(types) == {"diary"} else
                w["whole"]["migrated"].format(things=_things(change, count, w)))
    elif kind == "refiled" and change.get("machine"):
        who = None
        one, several, subject = _type_words(change, w)
        text = w["refreshed"].format(things=subject if count == 1 else f"{count} {several}")
        if _a_person(asked := change.get("asked_by", "")):
            text = w["requested"].format(text=text, name=asked.capitalize())
    else:
        who = _who(change)
        text = w["kinds"].get(kind, w["kinds"]["changed"]).format(thing=_thing(change, count, w))
    return {"at": at.isoformat(), "day": at.date().isoformat(), "who": who, "text": text,
            "cards": named, "rest": rest, "changes": change.get("changes", 1),
            **_when(at, now, w)}


def _named(change: dict, count: int, w: dict, href: Callable[[dict], str]) -> tuple[list[dict], str]:
    """The cards a sentence names, each with what goes before it, and what
    it says of the rest: `Maggie, Lisa and 4 more`. Up to three are named;
    beyond that the first two. People go by their first name in a list."""
    shown = CARDS_NAMED if count <= CARDS_NAMED else CARDS_NAMED - 1
    cards = change["cards"][:shown]
    rest = count - len(cards)
    named = []
    for i, c in enumerate(cards):
        last = i == len(cards) - 1 and i > 0 and rest == 0
        title = c["title"].split()[0] if c["type"] == "person" and count > 1 and c["title"].strip() \
            else c["title"]
        named.append({"title": title, "type": c["type"], "href": href(c),
                      "sep": "" if i == 0 else w["and"] if last else ", "})
    return named, w["rest"].format(n=rest) if rest > 0 else ""


def _a_person(slug: str) -> bool:
    """Whether a `Requested-By:` names someone to thank rather than the
    admin at the terminal or a bot."""
    return bool(slug) and slug not in ("admin", "stackadmin") and not slug.endswith("-bot")


def _who(change: dict) -> dict:
    """The person (or bot) a sentence starts with, as their chip shows them."""
    slug, machine = change.get("who", ""), bool(change.get("machine"))
    if machine:
        name = "Admin" if slug == "stackadmin" else slug.removesuffix("-bot").replace("-", " ").capitalize()
    else:
        name = change.get("who_name") or slug.capitalize()
    tone = "machine" if machine else str(int(sha1(slug.encode()).hexdigest(), 16) % _TONES)
    return {"name": name, "initial": name[:1].upper(), "tone": tone, "machine": machine}


def _thing(change: dict, count: int, w: dict) -> str:
    """`a note`, `3 notes`, `4 cards`: what a sentence says was touched."""
    one, several, _ = _type_words(change, w)
    return one if count == 1 else f"{count} {several}"


def _things(change: dict, count: int, w: dict) -> str:
    """`20 notes`, `1 card`: how much a change to the whole memory touched."""
    one, several, _ = _type_words(change, w)
    return f"1 {one.split(' ', 1)[-1]}" if count == 1 else f"{count} {several}"


def _type_words(change: dict, w: dict) -> tuple[str, str, str]:
    """The words for the cards a change touched: their type's when they
    are all of one (`types`, counted by the memory), else "card"."""
    types = change.get("types") or {c["type"]: 1 for c in change["cards"]}
    return w["types"].get(next(iter(types)), w["card"]) if len(types) == 1 else w["card"]


def _when(at: datetime, now: datetime, w: dict) -> dict:
    """Today's changes by how long ago (the script keeps them current),
    older ones by the time of day, under their day's heading."""
    if at.date() != now.date():
        return {"when": f"{at:%H:%M}", "relative": False}
    ago = max(now - at, timedelta(0))
    minutes = int(ago.total_seconds() // 60)
    if minutes < 1:
        text = w["just_now"]
    elif minutes < 60:
        text = w["minutes"][minutes != 1].format(n=minutes)
    else:
        text = w["hours"][minutes // 60 != 1].format(n=minutes // 60)
    return {"when": text, "relative": True}


def _days(entries: list[dict], w: dict, now: datetime) -> list[dict]:
    """The entries by day, newest first, each day with its heading and
    the sizes that show it and each of its entries."""
    days: list[dict] = []
    for entry in entries:
        if not days or days[-1]["day"] != entry["day"]:
            days.append({"day": entry["day"], "label": _day_label(entry["day"], w, now),
                         "show": ALL if not days else LARGE, "entries": []})
        days[-1]["entries"].append(entry)
    for i, day in enumerate(days):
        for j, entry in enumerate(day["entries"]):
            entry["show"] = LARGE if i else ALL if j == 0 else MEDIUM_UP if j < MEDIUM_ENTRIES else LARGE
    return days


def _day_label(day: str, w: dict, now: datetime) -> str:
    on = datetime.fromisoformat(day).date()
    if on == now.date():
        return w["today"]
    if on == now.date() - timedelta(days=1):
        return w["yesterday"]
    form = w["date"] if on.year == now.year else w["date_year"]
    return form.format(weekday=w["weekdays"][on.weekday()], day=on.day,
                       month=w["months"][on.month - 1], year=on.year)


# ── The view as HTML ─────────────────────────────────────────────────────
# The same markup `fs-activity.js` builds from the view: the README
# names the classes, and the widget's CSS styles nothing else.

_WHOLE_ICON = ('<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
               'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
               '<path d="m12 3 8 4.5-8 4.5-8-4.5z"/><path d="m4 12 8 4.5 8-4.5"/>'
               '<path d="m4 16.5 8 4.5 8-4.5"/></svg>')


def html(v: dict) -> str:
    """The widget's content as the page serves it: the large size's
    content, which the smaller sizes show less of."""
    parts = [f'<fs-activity data-now="{_e(v["now"])}">',
             f'<p class="glance" data-show="{v["glance"]["show"]}">{_e(v["glance"]["text"])}</p>']
    for day in v["days"]:
        parts.append(f'<section class="day" data-show="{day["show"]}">'
                     f'<h3 class="day-label" data-show="{MEDIUM_UP}">{_e(day["label"])}</h3>'
                     '<ol class="entries">')
        parts += [_entry_html(e) for e in day["entries"]]
        parts.append("</ol></section>")
    if more := v["more"]:
        parts.append(f'<a class="fs-more" data-show="{more["show"]}" data-limit="{more["limit"]}" '
                     f'href="{_e(more["href"])}">{_e(more["label"])}</a>')
    parts.append("</fs-activity>")
    return "".join(parts)


def _entry_html(e: dict) -> str:
    who = e["who"]
    if who:
        avatar = f'<span class="avatar" data-tone="{who["tone"]}" aria-hidden="true">{_e(who["initial"])}</span>'
        start = f'<b class="name">{_e(who["name"])}</b> '
    else:
        avatar = f'<span class="avatar" data-tone="machine" aria-hidden="true">{_WHOLE_ICON}</span>'
        start = ""
    cards = "".join(
        _e(c["sep"]) + (
            f'<a class="card" data-type="{_e(c["type"])}" href="{_e(c["href"])}">{_e(c["title"])}</a>'
            if c["href"] else f'<span class="card" data-type="{_e(c["type"])}">{_e(c["title"])}</span>')
        for c in e["cards"])
    relative = " data-relative" if e["relative"] else ""
    return (f'<li class="entry" data-show="{e["show"]}">{avatar}'
            f'<p class="sentence">{start}{_e(e["text"])}{": " + cards if cards else ""}{_e(e["rest"])}</p>'
            f'<time class="when" datetime="{e["at"]}"{relative}>{_e(e["when"])}</time></li>')


def _e(text: str) -> str:
    return escape(str(text), quote=True)
