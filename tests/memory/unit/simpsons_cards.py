"""A fabricated memory repo of the Simpsons: one card per type and kind.

Each card's frontmatter is what its writer produces today, field for field
and in the writer's order (the archivist's `document_frontmatter` and
`capture_frontmatter`, a note's without the fields its sidecar holds, the
diary's `_frontmatter`, `stack.lists`, `stack.people.render_person`,
`stack.cards.links.correspondent_card` and `topic_card`), so a schema that
accepts these accepts the memory a rig holds.
"""

from __future__ import annotations

import struct
import zlib

ONTOLOGY = """\
[topic.medical]
names = { de = "Gesundheit", en = "Medical" }

[topic.itchy-scratchy-land]
names = { de = "Itchy & Scratchy Land", en = "Itchy & Scratchy Land" }
"""

PEOPLE = {
    "family/people/homer.md": "---\ntype: person\ncard_id: person-homer\ntitle: Homer Simpson\n"
                              "account: homer\n---\n\n# Homer Simpson\n",
    "family/people/marge.md": "---\ntype: person\ncard_id: person-marge\ntitle: Marge Simpson\n"
                              "account: marge\n---\n\n# Marge Simpson\n",
    "family/people/bart.md": "---\ntype: person\ncard_id: person-bart\ntitle: Bart Simpson\n"
                             "aliases:\n  - El Barto\naccount: bart\n---\n\n# Bart Simpson\n",
    "family/people/maggie.md": "---\ntype: person\ncard_id: person-maggie\ntitle: Maggie Simpson\n"
                               "---\n\n# Maggie Simpson\n",
}

CARDS = {
    "family/documents/2026/10/duff-insurance-policy-p72.md": (
        "---\ntype: document\ncard_id: document-3f9a0c1b2d\ntitle: Duff Insurance policy\n"
        "date: \"2026-10-02\"\ncorrespondent: Duff Insurance\ndocument_type: Policy\ncategory: Insurance\n"
        "persons:\n  - homer\ntags:\n  - medical\n  - car-insurance\npaperless_id: 72\n"
        "paperless_url: \"http://localhost:42020\"\nresource: \"http://localhost:42020/documents/72/details\"\n"
        "processing: ai_formatted\nmodel: qwen3\nsource: paperless\ntimestamp: \"2026-10-02T09:14:00Z\"\n---\n\n"
        "# Duff Insurance policy\n\n"
        "> **From:** [Duff Insurance](../../../correspondents/duff-insurance.md) · "
        "**About:** [Homer](../../../people/homer.md)\n\nThe policy covers the car.\n"),
    "marge/notes/2026/10/art-supplies.md": (
        "---\ntype: note\ncard_id: note-7f3a9c21e4\ntitle: Art supplies for next time\n"
        "date: \"2026-10-13\"\npersons:\n  - marge\ntags:\n  - itchy-scratchy-land\n"
        "  - watercolour\n---\n\n# Art supplies for next time\n\n"
        "> **About** [Marge](../../../../family/people/marge.md)\n\nBrushes and paper.\n\n"
        "## Sources\n\n> brushes + paper for next time\n"),
    "homer/notes/grill.md": (
        "---\ntype: note\ncard_id: note-0b6e2d91aa\ntitle: Grill\n---\n# Grill\nBuy charcoal.\n"),
    "family/bookmarks/2026/10/krusty-burger.md": (
        "---\ntype: bookmark\ncard_id: bookmark-5c4d3e2f1a\ntitle: Krusty Burger menu\n"
        "date: \"2026-10-11\"\npersons:\n  - bart\nfiled_by: bart\ntags:\n  - burgers\n"
        "resource: \"https://krusty.example/menu\"\ncapture_id: $Bk2LmNoPqRsTuVwXyZ0123456789\n"
        "timestamp: \"2026-10-11T18:00:00Z\"\n---\n\n# Krusty Burger menu\n\n"
        "> **About** [Bart](../../../people/bart.md)\n\nWhy it was kept: the new shake.\n"),
    "family/emails/2026/10/school-trip.md": (
        "---\ntype: email\ncard_id: email-9e8d7c6b5a\ntitle: School trip to the plant\n"
        "date: \"2026-10-09\"\npersons:\n  - bart\ntags:\n  - sender-skinner\n"
        "resource: \"mid:<trip-42@springfield-elementary.example>\"\nmodel: qwen3\n"
        "timestamp: \"2026-10-09T07:30:00Z\"\n---\n\n# School trip to the plant\n\n"
        "> **About** [Bart](../../../people/bart.md)\n> **Started** 2026-10-09 · **Kind** email\n"),
    "family/lists/einkaufsliste.md": (
        "---\ntype: list\ncard_id: list-einkaufsliste\ntitle: Einkaufsliste\n---\n\n- [ ] Duff\n"),
    "family/lists/packliste.md": (
        "---\ntype: list\ncard_id: list-packliste\ntitle: Packliste\ntopics:\n  - camping\n"
        "kind: checklist\n---\n\n- [ ] Zelt\n"),
    "family/lists/termine.md": (
        "---\ntype: list\ncard_id: list-termine\ntitle: Termine\nkind: calendar\n---\n\n- [ ] Zahnarzt\n"),
    "family/lists/archive/einkaufsliste/2026-09.md": (
        "---\ntype: list\ncard_id: list-einkaufsliste-2026-09\ntitle: \"Einkaufsliste, 2026-09\"\n"
        "kind: archive\n---\n\n- [x] Duff\n"),
    "family/correspondents/duff-insurance.md": (
        "---\ntype: correspondent\ncard_id: correspondent-duff-insurance\ntitle: Duff Insurance\n"
        "aliases:\n  - Duff Insurance Springfield\n---\n\n# Duff Insurance\n"),
    "family/itchy-scratchy-land/topic.md": (
        "---\ntype: topic\ncard_id: topic-itchy-scratchy-land\ntitle: Itchy Scratchy Land\n---\n\n"
        "# Itchy Scratchy Land\n"),
}


def diary(kind: str, *, tags: tuple[str, ...] = ("medical",)) -> str:
    """A diary card of `kind` as the compile writes it: its tags are topic
    ids, as on every other card (ADR-017 §4)."""
    tag_lines = "".join(f"  - {t}\n" for t in tags)
    duration = "duration_ms: 41000\n" if kind == "voice" else ""
    return (f"---\ntype: diary\ncard_id: diary-{kind[:2]}0c1b2d3e\ntitle: Abe's birthday lunch\n"
            "date: \"2026-03-15\"\ndate_basis: spoken\ndescription: Lunch at the retirement castle.\n"
            f"persons:\n  - homer\ntags:\n{tag_lines}filed_by: homer\nkind: {kind}\nmode: monologue\n"
            f"{duration}resource: \"https://matrix.to/#/!room:simpson/$ev1\"\nentry_id: 3f9a0c1b2d\n"
            "event_ids:\n  - $Ev1AbCdEf\nmedia:\n  - /media/2026/03/Ev1AbCdEf.m4a\nmodel: qwen3\n"
            "timestamp: \"2026-03-15T12:00:00.123Z\"\ndigest: 9f86d081884c7d65\n---\n\n"
            "# Abe's birthday lunch\n\nWe took Grampa to lunch.\n")


for _kind in ("text", "voice", "image", "video", "file"):
    CARDS[f"homer/diary/entries/2026/03/lunch-{_kind}.md"] = diary(_kind)

# A note's sidecar: where it came from and how it was made (ADR-018). It
# is no card, so it sits beside its note in the repo and in no card list.
SIDECARS = {
    "marge/notes/2026/10/art-supplies.json": (
        '{\n  "card_id": "note-7f3a9c21e4",\n  "filed_by": "marge",\n  "sources": [\n    {\n'
        '      "role": "original",\n      "person": "marge",\n      "time": "2026-10-13T21:12:08Z",\n'
        '      "channel": "chat",\n      "kind": "text",\n'
        '      "id": "$r-PlkCYBsNEjgZDfzCpcAoDaY66uCHNc7tJjDWrY8P4"\n    }\n  ],\n'
        '  "made": {\n    "at": "2026-10-13T21:12:08Z",\n    "model": "qwen3",\n'
        '    "requested_by": "marge"\n  }\n}\n'),
}

VAULT = {**PEOPLE, **CARDS, **SIDECARS}

# A note the whole family reads, for a test that needs the home page asked:
# the home page reads no card of a person's bucket, and no other card of
# `VAULT` has a summary or a clean version.
FAMILY_NOTE = {
    "family/notes/2026/10/barbecue.md": (
        "---\ntype: note\ncard_id: note-1a2b3c4d5e\ntitle: Barbecue on Saturday\n"
        "date: \"2026-10-14\"\npersons:\n  - homer\n---\n\n# Barbecue on Saturday\n\n"
        "The Flanders are coming.\n\n## Sources\n\n> barbecue saturday, flanders too\n"),
}


# ── Notes made from a file ────────────────────────────────────────────────
#
# A PDF, a text file and a photo saved in topic rooms, as the capture files
# them: the card holds famstack's summary (or its description of the photo)
# and `## Sources`, whose quote opens with the file (a link, or an embed for
# the photo) followed by the file's words or the caption; the sidecar sits
# beside it. `MEDIA` is what the media store keeps for them: each file and
# the record the capture writes beside it (`RunningMemory.keeps_file`).

UNO_RULES, GARDEN_BENCH, PANCAKES = "note-4e1a9b7c2d", "note-0c6f3e9a1b", "note-5d2b8e4f1c"
UNO_PDF = "2026/11/Q7unoRulesEvent.pdf"
BENCH_PNG = "2026/11/B3nchPhotoEvent.png"
PANCAKES_MD = "2026/11/R3cipeTextEvent.md"
UNO_FULL_TEXT = "Each player gets seven cards."


def _file_note(path: str, card_id: str, title: str, *, by: str, day: str, tags: list[str],
               summary: str, quote: str, kind: str) -> dict[str, str]:
    tag_block = "tags:\n" + "".join(f"  - {t}\n" for t in tags) if tags else ""
    quoted = "\n".join(f"> {ln}" if ln else ">" for ln in quote.split("\n"))
    card = (f"---\ntype: note\ncard_id: {card_id}\ntitle: {title}\ndate: \"{day}\"\n"
            f"persons:\n  - {by}\n{tag_block}---\n\n# {title}\n\n"
            f"{summary}\n\n## Sources\n\n{quoted}\n")
    sidecar = ('{"card_id": "%s", "filed_by": "%s", "sources": [{"role": "original", '
               '"person": "%s", "time": "%sT16:05:00Z", "channel": "chat", "kind": "%s", '
               '"id": "$Ev%s"}], "made": {"at": "%sT16:05:09Z", "model": "qwen3"}}\n'
               % (card_id, by, by, day, kind, card_id[-6:], day))
    return {path: card, path.removesuffix(".md") + ".json": sidecar}


FILE_NOTES = {
    "family/games/topic.md": "---\ntype: topic\ncard_id: topic-games\ntitle: Games\n---\n\n# Games\n",
    "family/garden/topic.md": "---\ntype: topic\ncard_id: topic-garden\ntitle: Garden\n---\n\n# Garden\n",
    **_file_note("family/games/notes/2026/11/uno-rules-4e1a9b.md", UNO_RULES, "UNO rules",
                 by="marge", day="2026-11-07", tags=["games"],
                 summary="The official UNO rules on two pages: how to deal and what the action "
                         "cards do.",
                 quote=f"[uno-rules.pdf](/media/{UNO_PDF})\n\n{UNO_FULL_TEXT}\n\n"
                       "Say UNO when you have one card left.", kind="file"),
    **_file_note("family/garden/notes/2026/11/garden-bench-idea-0c6f3e.md", GARDEN_BENCH,
                 "Garden bench idea", by="homer", day="2026-11-05", tags=["garden"],
                 summary="A wooden bench built in a ring around the trunk of an old apple tree.",
                 quote=f"![[/media/{BENCH_PNG}]]\n\nThis is the one I mean for under the apple tree.",
                 kind="image"),
    **_file_note("family/notes/2026/11/pancakes-5d2b8e.md", PANCAKES, "Pancakes", by="marge",
                 day="2026-11-06", tags=[], summary="Marge's pancake recipe for four.",
                 quote=f"[pancakes.md](/media/{PANCAKES_MD})\n\nThree eggs, flour, milk.",
                 kind="file"),
}

def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A real PNG of one colour, so a browser draws the photo note's panel."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


MEDIA = {
    UNO_PDF: (b"%PDF-1.4 uno", {"filename": "uno-rules.pdf", "mime": "application/pdf",
                                "size": 240_000, "kind": "file"}),
    BENCH_PNG: (_png(320, 200, (122, 158, 96)), {"filename": "IMG_0412.png", "mime": "image/png",
                                                 "size": 1_843_201, "kind": "image"}),
    PANCAKES_MD: (b"Three eggs, flour, milk.", {"filename": "pancakes.md", "mime": "text/markdown",
                                                 "size": 24, "kind": "file"}),
}


# ── Diary entries made of sections ────────────────────────────────────────
#
# Diary cards as the diary's compile writes them (`diary_card._unsigned`):
# the meta line, famstack's summary as a callout, every medium in the order
# it was sent, the quotes, the replies under their writer, and the words
# under one heading (`Transcript` for a voice message, `Text`, else
# `Caption`). `ENTRY_MEDIA` is what the media store keeps for them; one
# photo of the lake is missing from it, as in a copy of the memory that
# came without all its files.

GRANDMA, SAX, LAKE, PLAY = "diary-2f8c6d0e7b", "diary-5c3e8a1f02", "diary-3d7e1f5a08", "diary-6a0b3c9d45"
GRANDMA_VOICE = "2026/11/GrandmaVoiceEvent.m4a"
GRANDMA_PHOTOS = [f"2026/11/GrandmaPhoto{n}Event.png" for n in range(1, 8)]
LAKE_PHOTOS = ["2026/08/LakeSwimEvent.png", "2026/08/LakeCanoeEvent.png", "2026/08/LakeFireEvent.png"]
PLAY_VIDEO, PLAY_PROGRAMME = "2026/04/PlayVideoEvent.mp4", "2026/04/PlayProgrammeEvent.pdf"
GRANDMA_WORDS = ("Grandma is here! She brought the lemon cake and she wants to hear Lisa play. "
                 "We will take lots of photos.")


def _entry(card_id: str, title: str, *, day: str, basis: str, by: str, addressee: str, kind: str,
           media: list[str], summary: str, words: str, replies: tuple[tuple[str, str], ...] = (),
           duration_ms: int = 0, quotes: tuple[str, ...] = ()) -> str:
    events = [p.rsplit("/", 1)[-1].rsplit(".", 1)[0] for p in media] or [f"{card_id[-6:]}Text"]
    meta = (f"---\ntype: diary\ncard_id: {card_id}\ntitle: {title}\ndate: \"{day}\"\n"
            f"date_basis: {basis}\ndescription: {title}.\npersons:\n  - {by}\ntags: []\n"
            f"filed_by: {by}\naddressee: {addressee}\nkind: {kind}\nmode: monologue\n"
            + (f"duration_ms: {duration_ms}\n" if duration_ms else "")
            + f"resource: \"https://matrix.to/#/!memories:simpson/${events[0]}\"\n"
            f"entry_id: {card_id[-10:]}\nevent_ids:\n" + "".join(f"  - ${e}\n" for e in events)
            + "media:\n" + "".join(f"  - /media/{p}\n" for p in media)
            + f"model: qwen3\ntimestamp: \"{day}T18:00:00.000Z\"\ndigest: 9f86d081884c7d65\n---\n\n")
    embeds = "".join(f"![[/media/{p}]]\n\n" if p.rsplit(".", 1)[-1] != "pdf"
                     else f"[{p.rsplit('/', 1)[-1]}](/media/{p})\n\n" for p in media)
    body = (f"# {title}\n\n> **From** {by} · **For** {addressee} · **Date** {day} ({basis})\n\n"
            f"> [!summary]\n> {summary}\n\n{embeds}")
    if quotes:
        body += "## Quotes\n\n" + "".join(f"- {q}\n" for q in quotes) + "\n"
    if replies:
        body += "## Replies\n\n" + "".join(f"**{who}**\n> {text}\n\n" for who, text in replies)
    heading = {"voice": "Transcript", "text": "Text"}.get(kind, "Caption")
    return meta + body + (f"## {heading}\n\n{words}\n" if words else "")


DIARY_ENTRIES = {
    "marge/diary/entries/2026/11/2026-11-03-2f8c6d0e7b.md": _entry(
        GRANDMA, "Grandma visited", day="2026-11-03", basis="spoken", by="marge", addressee="Lisa",
        kind="voice", media=[GRANDMA_VOICE, *GRANDMA_PHOTOS], duration_ms=72_000,
        summary="Grandma Bouvier came for the day: a cake, Lisa's saxophone and tea in the garden.",
        words=GRANDMA_WORDS, quotes=("She brought the lemon cake",)),
    "lisa/diary/entries/2026/10/2026-10-28-5c3e8a1f02.md": _entry(
        SAX, "Lisa's saxophone", day="2026-10-28", basis="sent", by="lisa", addressee="Lisa",
        kind="text", media=[], summary="Lisa practised the new piece for an hour.",
        words="I played the whole piece without a mistake today!"),
    "marge/diary/entries/2026/08/2026-08-15-3d7e1f5a08.md": _entry(
        LAKE, "The lake in August", day="2026-08-15", basis="sent", by="marge", addressee="Marge",
        kind="image", media=LAKE_PHOTOS, summary="A day at the lake: swimming, the canoe and a fire.",
        words="The lake was warm enough to swim."),
    "bart/diary/entries/2026/04/2026-04-01-6a0b3c9d45.md": _entry(
        PLAY, "The school play", day="2026-04-01", basis="sent", by="bart", addressee="Bart",
        kind="video", media=[PLAY_VIDEO, PLAY_PROGRAMME], duration_ms=12_000,
        summary="Bart played the tree in the school play.", words="",
        replies=(("marge", "You were the best tree."),)),
}

_COLOURS = [(196, 120, 90), (122, 158, 96), (90, 130, 190), (220, 190, 90), (150, 110, 170),
            (100, 170, 170), (200, 140, 160)]
ENTRY_MEDIA = {
    GRANDMA_VOICE: (b"m4a voice", {"filename": "Voice message.m4a", "mime": "audio/mp4",
                                    "size": 410_000, "kind": "voice"}),
    **{p: (_png(480, 320, _COLOURS[i]), {"filename": f"IMG_10{i}.png", "mime": "image/png",
                                          "size": 1_200_000, "kind": "image"})
       for i, p in enumerate(GRANDMA_PHOTOS)},
    **{p: (_png(480, 320, _COLOURS[i]), {"filename": f"IMG_20{i}.png", "mime": "image/png",
                                          "size": 1_100_000, "kind": "image"})
       for i, p in enumerate(LAKE_PHOTOS[:2])},
    PLAY_VIDEO: (b"mp4 video", {"filename": "play.mp4", "mime": "video/mp4", "size": 9_000_000,
                                "kind": "video"}),
    PLAY_PROGRAMME: (b"%PDF-1.4 programme", {"filename": "programme.pdf",
                                             "mime": "application/pdf", "size": 120_000,
                                             "kind": "file"}),
}
