"""A fabricated memory repo of the Simpsons: one card per type and kind.

Each card's frontmatter is what its writer produces today, field for field
and in the writer's order (the archivist's `document_frontmatter` and
`capture_frontmatter`, a note's without the fields its sidecar holds, the
diary's `_frontmatter`, `stack.lists`, `stack.people.render_person`,
`stack.card_links.correspondent_card` and `topic_card`), so a schema that
accepts these accepts the memory a rig holds.
"""

from __future__ import annotations

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


def diary(kind: str, *, tags: tuple[str, ...] = ("Gesundheit",)) -> str:
    """A diary card of `kind` as the compile writes it: its tags are the
    topics' labels in the instance's language (`diary_card.canonicalize_topic`)."""
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
