"""The words a family reads in the diary, in the household language.

    configure_language("en")          # once per run, from LANGUAGE
    phrase("diary_title")             # "Family Diary"
    day_label(date(2026, 9, 20))      # "Sunday, 20 September 2026"
    kind_label(entry)                 # "Voice note, 2:05"

Every string a family member reads on a page comes from the table here.
The compiler selects the household language once, at startup, with
`configure_language`; English is the default and the test baseline. The
selection is process state: the other modules ask for a phrase when they
render (`phrase`), never keep a copy of the table, so a language selected
later still reaches them.

`configure_language` also selects the language of the dating sentences
(`dating.configure_language`), which keep their own table.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

from . import dating

if TYPE_CHECKING:
    from .entries import Entry


# ── Language ──────────────────────────────────────────────────────────
#
# Every string a family member reads on a page comes from this table.
# The compiler selects the household language once, at startup, via
# configure_language(). English is the default and the test baseline.
# Other modules read it through `phrase` when they render, because
# `configure_language` replaces the table a copy would still point at.

_STRINGS = {
    "en": {
        "and": "and",
        "months": ["January", "February", "March", "April", "May",
                   "June", "July", "August", "September", "October",
                   "November", "December"],
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"],
        "day_heading": "{day}, {dom} {month}",
        "vocab_people": "The people in this family are {names}.",
        "vocab_topics": "They often talk about {topics}.",
        "kind_image": "Photo", "kind_video": "Video",
        "kind_file": "File", "kind_text": "Written note",
        "kind_attachment": "Attachment",
        "kind_voice": "Voice note", "kind_dialogue": "Conversation",
        "for": "for",
        "when_unknown": "When this happened is not recoverable",
        "untranscribable": "This recording could not be transcribed.",
        "full_transcript": "Full transcript",
        "replied": "{who} replied",
        "link_voice": "Listen in the room",
        "link_image": "See it in the room",
        "link_video": "Watch it in the room",
        "link_other": "Open in the room",
        "kept_file": "The file itself",
        "no_entries": "No entries yet.",
        "entry_one": "entry", "entry_many": "entries",
        "month_one": "month", "month_many": "months",
        "year_opening": "{count} this year",
        "recorded_by": "recorded by",
        "months_h": "Months", "years_h": "Years",
        "diary_title": "Family Diary",
        "home_teaser": "Recordings and notes, read back month by month.",
        "index_intro": (
            "Your memories, kept in a chronicle to read back. Voice "
            "notes, photos, conversations you recorded. Every entry "
            "leads back to the original recording, there to be "
            "listened to, today or in twenty years."),
        "getting_started": """## Nothing recorded yet

This diary is written from **Memories**, the room in your family chat.
Whatever you send there becomes an entry here: a voice message, a photo,
a video, or a few lines of writing.

- Record a voice message. It is written out here in full, and the
  recording stays one tap away.
- Open with the date ("Today is the third of March") and the entry is
  filed on that day. Without one, the day a photo was taken counts,
  else the day you sent it.
- Reply to a message to add to that memory later.
- Each morning a card appears in the thread under your message. Reply to
  it to fix a name, a date or a fact.

There is no wrong way to use it. Press record.""",
        "across": "across",
    },
    "de": {
        "and": "und",
        "months": ["Januar", "Februar", "M\u00e4rz", "April", "Mai",
                   "Juni", "Juli", "August", "September", "Oktober",
                   "November", "Dezember"],
        "days": ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                 "Freitag", "Samstag", "Sonntag"],
        "day_heading": "{day}, {dom}. {month}",
        "vocab_people": "Die Personen in dieser Familie sind {names}.",
        "vocab_topics": "Sie sprechen oft \u00fcber {topics}.",
        "kind_image": "Foto", "kind_video": "Video",
        "kind_file": "Datei", "kind_text": "Notiz",
        "kind_attachment": "Anhang",
        "kind_voice": "Sprachnotiz", "kind_dialogue": "Gespr\u00e4ch",
        "for": "f\u00fcr",
        "when_unknown": ("Wann dies geschah, l\u00e4sst sich nicht "
                         "mehr feststellen"),
        "untranscribable": ("Diese Aufnahme konnte nicht "
                            "transkribiert werden."),
        "full_transcript": "Vollst\u00e4ndiges Transkript",
        "replied": "{who} antwortete",
        "link_voice": "Im Chat anh\u00f6ren",
        "link_image": "Im Chat ansehen",
        "link_video": "Im Chat ansehen",
        "link_other": "Im Chat \u00f6ffnen",
        "kept_file": "Die Datei selbst",
        "no_entries": "Noch keine Eintr\u00e4ge.",
        "entry_one": "Eintrag", "entry_many": "Eintr\u00e4ge",
        "month_one": "Monat", "month_many": "Monaten",
        "year_opening": "{count} in diesem Jahr",
        "recorded_by": "aufgenommen von",
        "months_h": "Monate", "years_h": "Jahre",
        "diary_title": "Familientagebuch",
        "home_teaser": ("Aufnahmen und Notizen, Monat f\u00fcr Monat "
                        "zum Nachlesen."),
        "index_intro": (
            "Eure Erinnerungen, festgehalten in einer Chronik zum "
            "Nachlesen. Sprachnotizen, Fotos, Gespr\u00e4che, die ihr "
            "aufgenommen habt. Jeder Eintrag f\u00fchrt zur\u00fcck "
            "zur Originalaufnahme, zum Nachh\u00f6ren, heute "
            "oder in zwanzig Jahren."),
        "getting_started": """## Noch nichts aufgenommen

Dieses Tagebuch entsteht aus **Memories**, dem Raum in eurem Familienchat.
Alles, was ihr dort sendet, wird hier zu einem Eintrag: eine Sprachnachricht,
ein Foto, ein Video oder ein paar Zeilen Text.

- Nehmt eine Sprachnachricht auf. Sie wird hier vollst\u00e4ndig
  ausgeschrieben, und die Aufnahme bleibt einen Fingertipp entfernt.
- Beginnt mit dem Datum ("Heute ist der dritte M\u00e4rz"), dann wird der
  Eintrag auf diesen Tag datiert. Ohne Datum z\u00e4hlt der Aufnahmetag
  eines Fotos, sonst der Tag, an dem ihr gesendet habt.
- Antwortet auf eine Nachricht, um sp\u00e4ter etwas zu erg\u00e4nzen.
- Jeden Morgen erscheint im Thread unter eurer Nachricht eine Karte.
  Antwortet darauf, um einen Namen, ein Datum oder eine Angabe zu korrigieren.

Es gibt kein falsches Vorgehen. Dr\u00fcckt auf Aufnahme.""",
        "across": "in",
    },
}

_L = _STRINGS["en"]


def configure_language(code: str) -> None:
    """Select the render language. Unknown codes keep English."""
    global _L
    _L = _STRINGS.get((code or "").strip().lower()[:2], _STRINGS["en"])
    dating.configure_language(code)


def phrase(key: str, *default):
    """The household language's text for `key`; `default` when given and the
    language has none, else KeyError.

        phrase("diary_title")                        # "Family Diary"
        phrase("kind_sticker", phrase("kind_attachment"))
    """
    return _L.get(key, default[0]) if default else _L[key]


def month_name(on: date) -> str:
    return _L["months"][on.month - 1]


def month_year(on: date) -> str:
    return f"{month_name(on)} {on.year}"


def day_heading(on: date) -> str:
    return _L["day_heading"].format(
        day=_L["days"][on.weekday()], dom=on.day, month=month_name(on))


def day_label(on: date) -> str:
    """A day as a family reads it, with the year: "Sunday, 20 September 2026"."""
    return f"{day_heading(on)} {on.year}"


def counted(n: int, one: str, many: str) -> str:
    return f"{n} {_L[one] if n == 1 else _L[many]}"


def and_list(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" {_L['and']} {names[-1]}"


def duration(ms: int | None) -> str:
    if not ms:
        return ""
    total = round(ms / 1000)
    return f"{total // 60}:{total % 60:02d}"


# What each kind of entry is called on the page. Every kind the
# compiler accepts needs a name here: falling through to the internal
# word prints "video" in a line of otherwise written English.
def kind_label(entry: "Entry") -> str:
    if entry.kind == "voice":
        noun = (_L["kind_dialogue"] if entry.mode == "dialogue"
                else _L["kind_voice"])
    else:
        noun = _L.get(f"kind_{entry.kind}", _L["kind_attachment"])
    length = duration(entry.duration_ms)
    return f"{noun}, {length}" if length else noun


def plain_title(entry: "Entry") -> str:
    """A title that needs no model: what the entry is, and its day.

    For a record whose reading failed. It says nothing the room does not
    say, so a later run with a working model can replace it.
    """
    return f"{kind_label(replace(entry, duration_ms=None))}, {day_heading(entry.on)}"
