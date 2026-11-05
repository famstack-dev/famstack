"""The wiki's sidebar reaches every person and every topic room.

The sidebar is drawn from the model `familyModel` computes over the pages
of a build. The pages are the brain: the records at their own paths, plus
the views the curator compiles over the cards. A person's view is
`<id>/about` and says `view: person`; a topic room's view is `about` in
the room's folder and says `view: topic`. The person and topic cards
themselves are not rendered. A room sits in the shared bucket
(everyone's) or in a person's own bucket (`marge/duff-beer`).

The model is TypeScript and runs inside the wiki's Quartz build; it is
called here through Node, which runs TypeScript by stripping its types,
with the pages a build would pass it. Without Node the test is skipped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODEL = (Path(__file__).resolve().parents[3]
         / "stacklets" / "memory" / "quartz" / "components" / "familyModel.ts")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


def _page(slug: str, **frontmatter) -> dict:
    """One page as the build hands it to the model."""
    return {"slug": slug, "relativePath": slug + ".md", "frontmatter": frontmatter,
            "dates": {"modified": "2026-11-04T10:00:00Z"}}


# The pages a build passes for a family whose members filed into their own
# buckets and into one shared room. Lisa filed nothing yet.
BRAIN = [
    _page("homer/about", view="person", about="person-homer", title="Homer Simpson"),
    _page("lisa/about", view="person", about="person-lisa", title="Lisa Simpson"),
    _page("marge/about", view="person", about="person-marge", title="Marge Simpson"),
    _page("marge/duff-beer/about", view="topic", about="topic-duff-beer", title="Duff Beer"),
    _page("marge/duff-beer/notes/2026/11/duff-beer-opinion-7c0067",
          type="note", title="Duff beer opinion", timestamp="2026-11-02T09:00:00Z"),
    _page("marge/notes/2026/10/art-supplies-4c1d2e",
          type="note", title="Art supplies", timestamp="2026-10-20T09:00:00Z"),
    _page("homer/bookmarks/2026/10/krusty-burger-menu-5c4d3e",
          type="bookmark", title="Krusty Burger menu", timestamp="2026-10-21T09:00:00Z"),
    _page("family/itchy-scratchy-land/about", view="topic", about="topic-itchy-scratchy-land",
          title="Itchy Scratchy Land"),
    _page("family/itchy-scratchy-land/notes/2026/10/tickets-7903f2",
          type="note", title="Tickets", timestamp="2026-10-12T09:00:00Z"),
    _page("family/diary/about", view="diary-front", title="Diary"),
]


# The wiki's modules import each other without an extension
# (`./bookmarkLink`), as Quartz's esbuild resolves them. Node's own
# resolver does not add one, so the test resolves such an import to its
# `.ts` file first and then loads the model.
RESOLVE_TS = """import { registerHooks } from "node:module";
registerHooks({ resolve(spec, context, next) {
  try { return next(spec, context) }
  catch (e) {
    if (e.code !== "ERR_MODULE_NOT_FOUND" || !spec.startsWith(".")) throw e;
    return next(spec + ".ts", context);
  }
} });
"""


def _model(tmp_path: Path, pages: list[dict]) -> dict:
    """The sidebar and the list pages `familyModel` builds from `pages`."""
    script = (RESOLVE_TS
              + f"const {{ familyModel }} = await import({json.dumps(MODEL.as_uri())});\n"
              "const pages = JSON.parse(await new Response(process.stdin).text());\n"
              "for (const p of pages) p.dates.modified = new Date(p.dates.modified);\n"
              f"const ctx = {{ argv: {{ directory: {json.dumps(str(tmp_path))} }} }};\n"
              "const model = familyModel(ctx, pages);\n"
              "console.log(JSON.stringify({ nav: model.nav, lists: Object.fromEntries(model.lists) }));\n")
    done = subprocess.run(["node", "--input-type=module", "--no-warnings", "-e", script],
                          input=json.dumps(pages), capture_output=True, text=True,
                          timeout=30, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _section(model: dict, key: str) -> dict:
    return next(n for n in model["nav"] if n.get("key") == key)


def _reachable(node: dict) -> set[str]:
    """Every page a click from this node opens, its own included."""
    slugs = {node["slug"]} if node.get("slug") else set()
    for child in node.get("children", []):
        slugs |= _reachable(child)
    return slugs


def _listed(model: dict, node: dict) -> set[str]:
    """The pages a node reaches, and the pages its list pages list."""
    out = set()
    for slug in _reachable(node):
        out.add(slug)
        for section in model["lists"].get(slug, {}).get("sections", []):
            out |= set(section.get("slugs") or [])
    return out


def test_every_member_is_in_the_people_section_and_opens_their_page(tmp_path):
    people = _section(_model(tmp_path, BRAIN), "people")

    assert [(p["label"], p["slug"]) for p in people["children"]] == [
        ("Homer Simpson", "homer/about"),
        ("Lisa Simpson", "lisa/about"),
        ("Marge Simpson", "marge/about"),
    ]


def test_a_persons_own_room_notes_and_bookmarks_are_reached_from_their_entry(tmp_path):
    model = _model(tmp_path, BRAIN)
    by_name = {p["label"]: p for p in _section(model, "people")["children"]}

    marge = _listed(model, by_name["Marge Simpson"])
    assert "marge/duff-beer/about" in marge
    assert "marge/duff-beer/notes/2026/11/duff-beer-opinion-7c0067" in marge
    assert "marge/notes/2026/10/art-supplies-4c1d2e" in marge
    assert "homer/bookmarks/2026/10/krusty-burger-menu-5c4d3e" in _listed(model, by_name["Homer Simpson"])
    assert not _listed(model, by_name["Lisa Simpson"]) - {"lisa/about"}


def test_a_shared_room_is_reached_from_topics_and_a_private_one_is_not(tmp_path):
    topics = _reachable(_section(_model(tmp_path, BRAIN), "topics"))

    assert "family/itchy-scratchy-land/about" in topics
    assert "marge/duff-beer/about" not in topics


def test_the_other_sections_stay_in_their_order(tmp_path):
    keys = [n.get("key") for n in _model(tmp_path, BRAIN)["nav"]]

    assert keys == ["start", "recent", "diary", "people", "topics", "notes"]
