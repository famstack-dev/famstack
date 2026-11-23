"""The memory pages' words, in English and German.

Every label the memory's pages show ("My memory", the family's page, a
person, a topic, a sender, the documents) comes from here, so a
page and its German twin never drift, and the page modules hold layout
only. The words the card pages share with these pages (the way back to the
dashboard, the type of a card, the line about the chat on a list, the
memory that does not answer, dates in plain words) stay in
`memory_ui.words`, which this module reads, so both say them the same way.

    w = words("de")
    w["sections"]["up_next"]                 # 'Demnächst'
    counted(3, w["cards"])                   # '3 Karten'

The German follows the card pages' rules: orders and the reader in the du
form, "Gedächtnis" for the memory.

Pure: no I/O. `language` is "de" or anything else for English.
"""

from __future__ import annotations

from memory_ui.words import words as card_words

__all__ = ["words", "counted"]


def words(language: str) -> dict:
    """The labels for `language`: German for "de", else English, with the
    card pages' shared ones under `card` (`w["card"]["dashboard"]`).

        >>> words("de")["sections"]["lists"]
        'Listen'
        >>> words("fr")["hello"].format(name="Marge")
        'Hello, Marge'
    """
    code = "de" if language == "de" else "en"
    return {**WORDS[code], "card": card_words(code)}


def counted(n: int, forms: tuple[str, str]) -> str:
    """`n` with the singular or the plural of `forms` ("1 card", "{n} cards").

        >>> counted(1, ("1 card", "{n} cards")), counted(4, ("1 card", "{n} cards"))
        ('1 card', '4 cards')
    """
    return forms[0] if n == 1 else forms[1].format(n=n)


# ── The words ──────────────────────────────────────────────────────────────

WORDS = {
    "en": {
        "my_memory": "My memory", "your_memory": "Your memory", "hello": "Hello, {name}",
        "mine": ("1 card is yours or names you", "{n} cards are yours or name you"),
        "whole_family": "the whole family's memory",
        "the_family": "The family", "family_of": "The {family}",
        "cards": ("1 card", "{n} cards"),
        "sections": {"up_next": "Up next", "lists": "Lists", "topics": "Your topics",
                     "changed": "What changed", "bookmarks": "Your bookmarks",
                     "documents": "Documents", "links": "Links",
                     "main_bookmarks": "Bookmarks", "main_documents": "Documents",
                     "their_topics": "Topics", "cards": "Cards", "sent": "Documents",
                     "newest": "Newest"},
        "empty": {"up_next": "Nothing dated ahead.", "lists": "No open list.",
                  "topics": "No topic of yours yet.",
                  "changed": "Nothing new in the last two weeks.",
                  "bookmarks": "No bookmark yet.", "documents": "No document yet.",
                  "their_topics": "No topic yet.", "cards": "No card yet.", "sent": "No document yet.",
                  "newest": "No document yet.", "senders": "No sender yet."},
        "types": {"document": "Documents", "note": "Notes", "bookmark": "Bookmarks", "diary": "Diary",
                  "list": "Lists", "email": "Emails"},
        "show_only": "Your cards by type",
        "open_items": ("1 item open", "{n} items open"),
        "about_you": "{n} about you", "only_you": "only you", "latest": "Latest: {title}",
        "by_you": "by you", "by": "by {name}",
        "all_documents": "All documents",
        "newer": "‹ Newer", "older": "Older ›", "part_of": "{part} of {parts}",
        "parts_label": "Older and newer cards",
        "often_with": "Often with", "people": "People", "topics": "Topics", "senders": "Senders",
        # The frame: the header, the menu, the phone's menu button.
        "brand": "famstack memory, your page", "memory": "memory",
        "search": "Search the memory", "search_hint": "Search letters, notes, bookmarks, people …",
        "clear_search": "Clear the search", "searching": "Searching …",
        "this_device": "this device",
        "menu": "Menu", "menu_label": "The memory", "your_topics": "Your topics",
        "skip": "Skip to the page",
        # The graph pages: a person, a topic, a sender, the documents, a kind, the senders.
        "person": "Person", "topic_family": "The family's topic", "topic_own": "Own topic",
        "topic_of": "{name}'s topic",
        "topic_key": "✦ beside a date: famstack's AI summarised the entry. Everything else is taken from the cards as they are.",
        "chip_bookmarks": ("1 bookmark", "{n} bookmarks"), "chip_documents": ("1 document", "{n} documents"),
        "in_topic": "In this topic", "all_n": "All {n} ›",
        "profile_own": "Your profile", "profile_of": "{name}'s profile",
        "sender": "Sender", "everything": "Everything", "documents": "Documents",
        "document_count": ("1 document", "{n} documents"),
        "sender_count": ("1 sender", "{n} senders"),
        "about_name": "{n} about {name}", "only_name": "only {name}",
        "kinds_label": "Kinds of document", "newest_on": "Newest {day}",
        "not_found": "Not in the memory", "not_found_line": "The memory has no page at this address.",
        # The search page.
        "search_title": "Search", "found": ("1 match", "{n} matches"),
        "found_first": "The first {shown} of {n} matches",
        "no_match": "Nothing in the memory matches \"{q}\".",
        "ask_words": "Type a word to search the memory.",
        "groups": {"person": "People", "topic": "Topics", "correspondent": "Senders"},
    },
    "de": {
        "my_memory": "Mein Gedächtnis", "your_memory": "Dein Gedächtnis", "hello": "Hallo, {name}",
        "mine": ("1 Karte gehört dir oder nennt dich", "{n} Karten gehören dir oder nennen dich"),
        "whole_family": "das Gedächtnis der ganzen Familie",
        "the_family": "Die Familie", "family_of": "{family}",
        "cards": ("1 Karte", "{n} Karten"),
        "sections": {"up_next": "Demnächst", "lists": "Listen", "topics": "Deine Themen",
                     "changed": "Was sich geändert hat", "bookmarks": "Deine Lesezeichen",
                     "documents": "Dokumente", "links": "Verweise",
                     "main_bookmarks": "Lesezeichen", "main_documents": "Dokumente",
                     "their_topics": "Themen", "cards": "Karten", "sent": "Dokumente",
                     "newest": "Neueste"},
        "empty": {"up_next": "Nichts mit Datum in Sicht.", "lists": "Keine offene Liste.",
                  "topics": "Noch kein Thema von dir.",
                  "changed": "Nichts Neues in den letzten zwei Wochen.",
                  "bookmarks": "Noch kein Lesezeichen.", "documents": "Noch kein Dokument.",
                  "their_topics": "Noch kein Thema.", "cards": "Noch keine Karte.",
                  "sent": "Noch kein Dokument.", "newest": "Noch kein Dokument.",
                  "senders": "Noch kein Absender."},
        "types": {"document": "Dokumente", "note": "Notizen", "bookmark": "Lesezeichen",
                  "diary": "Tagebuch", "list": "Listen", "email": "E-Mails"},
        "show_only": "Deine Karten nach Art",
        "open_items": ("1 Punkt offen", "{n} Punkte offen"),
        "about_you": "{n} über dich", "only_you": "nur du", "latest": "Zuletzt: {title}",
        "by_you": "von dir", "by": "von {name}",
        "all_documents": "Alle Dokumente",
        "newer": "‹ Neuere", "older": "Ältere ›", "part_of": "{part} von {parts}",
        "parts_label": "Ältere und neuere Karten",
        "often_with": "Oft mit", "people": "Personen", "topics": "Themen", "senders": "Absender",
        "brand": "famstack Gedächtnis, deine Seite", "memory": "Gedächtnis",
        "search": "Das Gedächtnis durchsuchen",
        "search_hint": "Briefe, Notizen, Lesezeichen, Personen suchen …",
        "clear_search": "Suche leeren", "searching": "Suche läuft …",
        "this_device": "dieses Gerät",
        "menu": "Menü", "menu_label": "Das Gedächtnis", "your_topics": "Deine Themen",
        "skip": "Zur Seite springen",
        "person": "Person", "topic_family": "Thema der Familie", "topic_own": "Eigenes Thema",
        "topic_of": "Thema von {name}",
        "topic_key": "✦ neben einem Datum: Die KI von famstack hat den Eintrag zusammengefasst. Alles andere steht so da, wie es auf den Karten steht.",
        "chip_bookmarks": ("1 Lesezeichen", "{n} Lesezeichen"), "chip_documents": ("1 Dokument", "{n} Dokumente"),
        "in_topic": "In diesem Thema", "all_n": "Alle {n} ›",
        "profile_own": "Dein Profil", "profile_of": "Profil von {name}",
        "sender": "Absender", "everything": "Alles", "documents": "Dokumente",
        "document_count": ("1 Dokument", "{n} Dokumente"),
        "sender_count": ("1 Absender", "{n} Absender"),
        "about_name": "{n} über {name}", "only_name": "nur {name}",
        "kinds_label": "Arten von Dokumenten", "newest_on": "Neu {day}",
        "not_found": "Nicht im Gedächtnis",
        "not_found_line": "Das Gedächtnis hat keine Seite unter dieser Adresse.",
        "search_title": "Suche", "found": ("1 Treffer", "{n} Treffer"),
        "found_first": "Die ersten {shown} von {n} Treffern",
        "no_match": "Nichts im Gedächtnis passt zu „{q}“.",
        "ask_words": "Gib ein Wort ein, um das Gedächtnis zu durchsuchen.",
        "groups": {"person": "Personen", "topic": "Themen", "correspondent": "Absender"},
    },
}
