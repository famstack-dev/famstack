"""The memory pages' words, in English and German, and dates in plain words.

Every label the memory's pages show comes from here, so a page and its
German twin never drift, and a page module holds layout only. The
English labels are the source; the German ones follow the
pages' rules: orders in the du form, the family's things are "euer",
"Gedächtnis" for the memory and "Erinnerungen" for its items.

    w = words("de")
    w["history"]                                   # 'Verlauf'
    plain_day(date(2026, 11, 3), today=date(2026, 11, 10), language="en")
                                                   # 'Tuesday'

Dates follow one rule everywhere on the pages, because a family reads
"yesterday" faster than "2026-11-09": today, yesterday, the weekday within
the past week, else the day and month, with the year only when it is not
this year. `created` adds the full date and time for a hover and a screen
reader, since the plain words drop them.

Pure: no I/O. `language` is "de" or anything else for English.
"""

from __future__ import annotations

from datetime import date, datetime

__all__ = ["words", "plain_day", "long_day", "created", "day_of"]


def words(language: str) -> dict:
    """The labels for `language`: German for "de", else English.

        >>> words("de")["open"]["paperless"]
        'In Paperless öffnen'
        >>> words("fr")["history"]
        'History'
    """
    return WORDS["de" if language == "de" else "en"]


def plain_day(day: date, *, today: date, language: str) -> str:
    """`day` as a family says it, seen from `today`.

        >>> t = date(2026, 11, 10)
        >>> [plain_day(date(2026, 11, d), today=t, language="en") for d in (10, 9, 4)]
        ['today', 'yesterday', 'Wednesday']
        >>> plain_day(date(2026, 11, 3), today=t, language="en")
        '3 November'
        >>> plain_day(date(2025, 11, 3), today=t, language="de")
        '3. November 2025'

    A day in the future (a calendar's next date) is never "yesterday": it
    reads as its date.
    """
    w = words(language)
    days = (today - day).days
    if days == 0:
        return w["today"]
    if days == 1:
        return w["yesterday"]
    if 1 < days < 7:
        return w["weekdays"][day.weekday()]
    return long_day(day, today=today, language=language)


def long_day(day: date, *, today: date, language: str) -> str:
    """`day` as its date, the year only when it is not this year.

        >>> long_day(date(2026, 9, 2), today=date(2026, 11, 10), language="en")
        '2 September'
        >>> long_day(date(2025, 9, 2), today=date(2026, 11, 10), language="de")
        '2. September 2025'
    """
    w = words(language)
    text = w["date"].format(day=day.day, month=w["months"][day.month - 1])
    return text if day.year == today.year else f"{text} {day.year}"


def created(at: datetime, *, now: datetime, language: str) -> tuple[str, str]:
    """When a card was made: the plain words, and the full date and time
    for its hover and its spoken label. `at` is read in `now`'s time zone.

        >>> from datetime import timezone
        >>> now = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
        >>> created(datetime(2026, 11, 9, 14, 32, tzinfo=timezone.utc), now=now, language="en")
        ('yesterday', 'Created 9 November 2026, 14:32')
    """
    w = words(language)
    local = at.astimezone(now.tzinfo) if at.tzinfo else at
    full = w["date"].format(day=local.day, month=w["months"][local.month - 1])
    return (plain_day(local.date(), today=now.date(), language=language),
            w["created"].format(day=f"{full} {local.year}", time=f"{local:%H:%M}"))


def day_of(value: object) -> date | None:
    """The day an ISO date or timestamp names, None for anything else.

        >>> day_of("2026-10-02T09:14:00Z")
        datetime.date(2026, 10, 2)
        >>> day_of("soon") is None
        True
    """
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


# ── The words ──────────────────────────────────────────────────────────────

WORDS = {
    "en": {
        "dashboard": "‹ Dashboard",
        "types": {"document": "Document", "bookmark": "Bookmark", "note": "Note", "email": "Email",
                  "diary": "Diary", "person": "Person", "topic": "Topic", "list": "List",
                  "correspondent": "Correspondent", "list-archive": "List"},
        "open": {"paperless": "Open in Paperless", "page": "Open the page",
                 "play": "Play the recording", "web": "Open the website",
                 # A note made from a file opens its file (`file_notes.py`).
                 "pdf": "Open the PDF", "image": "Open the image", "file": "Open the file"},
        "file_kinds": {"pdf": "PDF", "image": "Image", "markdown": "Markdown", "text": "Text",
                       "file": "File"},
        "full_text": "Full text", "loading_text": "Loading the full text…",
        "no_text": "The full text could not be read.",
        "people": "People", "tags": "Tags", "wiki": "Read it in the wiki",
        "history": "History", "loading": "Loading the history…", "show_all": "Show all",
        "details": "Details", "card_id": "Card", "lives": "Where it lives", "sources": "Sources",
        "nothing": "Nothing written here yet.",
        "unreachable": "The family memory does not answer. Is the memory stacklet running? "
                       "(stack up memory)",
        "missing": "No card has this address.",
        "no_history": "The history could not be read.",
        # A removed card and a memory that is not set up (`notices.py`).
        "gone_title": "This card is gone", "gone_line": "{who} removed it on {day}.",
        "off_title": "The family memory is not set up yet",
        "off_line": "Once it runs, every link in the chat opens its card here.",
        "off_button": "Set it up",
        "today": "today", "yesterday": "yesterday",
        "weekdays": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
        "months": ("January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"),
        "date": "{day} {month}", "created": "Created {day}, {time}",
        # The facts line and its quiet extra, per type (`card_types.py`).
        "saved_by": "saved by {name}", "to": "To {names}",
        "from": "From {sender}", "also": "also {names}",
        "open_items": "{n} open", "next": "Next: {item}, {day}", "in_chat": "in the chat",
        "cards": ("1 card", "{n} cards"), "changed_on": "changed {day}",
        "said_date": "Date as said in the recording",
        "medium": {"voice": "Voice", "image": "Photo", "video": "Video", "text": "Text",
                   "file": "File"},
        # A diary entry's sections (`entry.py`).
        "for": "For {name}", "and": "and",
        "holds": {"text": ("text", "{n} texts"), "voice": ("a voice message", "{n} voice messages"),
                  "photo": ("a photo", "{n} photos"), "video": ("a video", "{n} videos"),
                  "file": ("a file", "{n} files"), "reply": ("a reply", "{n} replies")},
        "added_by": "Added by {name}", "of": "{n} of {count}", "all_photos": "All photos",
        "previous_photo": "Previous photo", "next_photo": "Next photo",
        "play_video": "Play the video", "open_file": "Open",
        "not_kept": "Not in the memory's files",
        "list_kinds": {"todo": "To-do list", "checklist": "Checklist", "calendar": "Calendar list",
                       "archive": "Archive"},
        # A list's page (`lists.py`).
        "ticked_by": "ticked by {name}",
        "ticked": "ticked", "not_ticked": "not ticked",
        "earlier": "Earlier months", "items": ("1 item", "{n} items"),
        "chat_line": "Add or tick items in the chat: 'Stacky, we need milk.'",
        # History: who did what to this one card (`history.py`).
        "famstack": "famstack",
        "did": {"filed": "filed it", "updated": "added to it", "replied": "replied to it",
                "refiled": "read it again", "corrected": "corrected it", "edited": "changed it",
                "changed": "changed it", "removed": "removed it",
                "transcribed": "wrote down what was said", "declared": "wrote it",
                "moved": "moved it", "listed": "put its items on a list",
                "migrated": "brought it up to date", "seeded": "set it up"},
    },
    "de": {
        "dashboard": "‹ Übersicht",
        "types": {"document": "Dokument", "bookmark": "Lesezeichen", "note": "Notiz", "email": "E-Mail",
                  "diary": "Tagebuch", "person": "Person", "topic": "Thema", "list": "Liste",
                  "correspondent": "Absender", "list-archive": "Liste"},
        "open": {"paperless": "In Paperless öffnen", "page": "Seite öffnen",
                 "play": "Aufnahme abspielen", "web": "Website öffnen",
                 "pdf": "PDF öffnen", "image": "Bild öffnen", "file": "Datei öffnen"},
        "file_kinds": {"pdf": "PDF", "image": "Bild", "markdown": "Markdown", "text": "Text",
                       "file": "Datei"},
        "full_text": "Volltext", "loading_text": "Volltext wird geladen…",
        "no_text": "Der Volltext ließ sich nicht lesen.",
        "people": "Personen", "tags": "Schlagwörter", "wiki": "Im Wiki lesen",
        "history": "Verlauf", "loading": "Verlauf wird geladen…", "show_all": "Alle anzeigen",
        "details": "Details", "card_id": "Karte", "lives": "Wo sie liegt", "sources": "Quellen",
        "nothing": "Hier steht noch nichts.",
        "unreachable": "Das Familiengedächtnis antwortet nicht. Läuft das memory-Stacklet? "
                       "(stack up memory)",
        "missing": "Unter dieser Adresse gibt es keine Erinnerung.",
        "no_history": "Der Verlauf ließ sich nicht lesen.",
        "gone_title": "Diese Erinnerung gibt es nicht mehr", "gone_line": "{who} hat sie am {day} entfernt.",
        "off_title": "Euer Familiengedächtnis ist noch nicht eingerichtet",
        "off_line": "Sobald es läuft, öffnet hier jeder Link aus dem Chat seine Erinnerung.",
        "off_button": "Richte es ein",
        "today": "heute", "yesterday": "gestern",
        "weekdays": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
        "months": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                   "September", "Oktober", "November", "Dezember"),
        "date": "{day}. {month}", "created": "Erstellt am {day}, {time}",
        "saved_by": "gespeichert von {name}", "to": "An {names}",
        "from": "Von {sender}", "also": "auch {names}",
        "open_items": "{n} offen", "next": "Als Nächstes: {item}, {day}", "in_chat": "im Chat",
        "cards": ("1 Erinnerung", "{n} Erinnerungen"), "changed_on": "geändert {day}",
        "said_date": "Datum wie in der Aufnahme gesagt",
        "medium": {"voice": "Sprachnachricht", "image": "Foto", "video": "Video", "text": "Text",
                   "file": "Datei"},
        "for": "Für {name}", "and": "und",
        "holds": {"text": ("Text", "{n} Texte"),
                  "voice": ("eine Sprachnachricht", "{n} Sprachnachrichten"),
                  "photo": ("ein Foto", "{n} Fotos"), "video": ("ein Video", "{n} Videos"),
                  "file": ("eine Datei", "{n} Dateien"), "reply": ("eine Antwort", "{n} Antworten")},
        "added_by": "Ergänzt von {name}", "of": "{n} von {count}", "all_photos": "Alle Fotos",
        "previous_photo": "Vorheriges Foto", "next_photo": "Nächstes Foto",
        "play_video": "Video abspielen", "open_file": "Öffnen",
        "not_kept": "Nicht in den Dateien des Gedächtnisses",
        "list_kinds": {"todo": "To-do-Liste", "checklist": "Checkliste", "calendar": "Terminliste",
                       "archive": "Archiv"},
        "ticked_by": "abgehakt von {name}",
        "ticked": "abgehakt", "not_ticked": "nicht abgehakt",
        "earlier": "Frühere Monate", "items": ("1 Punkt", "{n} Punkte"),
        "chat_line": "Setz im Chat etwas auf die Liste oder hak es ab: „Stacky, wir brauchen Milch.“",
        "famstack": "famstack",
        "did": {"filed": "hat es abgelegt", "updated": "hat etwas ergänzt",
                "replied": "hat darauf geantwortet", "refiled": "hat es neu gelesen",
                "corrected": "hat es korrigiert", "edited": "hat es geändert",
                "changed": "hat es geändert", "removed": "hat es entfernt",
                "transcribed": "hat aufgeschrieben, was gesagt wurde", "declared": "hat es angelegt",
                "moved": "hat es verschoben", "listed": "hat die Punkte auf eine Liste gesetzt",
                "migrated": "hat es auf den neuen Stand gebracht", "seeded": "hat es angelegt"},
    },
}
