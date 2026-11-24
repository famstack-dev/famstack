"""A link into a part the wiki does not hold shows as plain text.

The wiki renders only the family's shared part of the memory. A page there
can still link into a person's bucket: the home page names each member
with a link to their page (`homer/about`), and a person card's link maps to
that member page. VaultLinks turns such a link into its text, because the
page it names is not in the build. `vaultPaths.ts` decides it: where a link
points (`vaultPathOf`), which page shows a card (`viewOf`), and whether the
wiki holds that page (`shown`, by the first folder of its path).

The module is TypeScript and runs inside the wiki's Quartz build; its
functions are called here through Node, which runs TypeScript by stripping
its types. Without Node the test is skipped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = (Path(__file__).resolve().parents[3]
          / "stacklets" / "memory" / "quartz" / "components" / "vaultPaths.ts")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")

# The top folders of the wiki's input: the shared bucket and the media.
HELD = ["family", "media"]


def _shown(links: list[tuple[str, str]]) -> list[bool]:
    """For each (url, folder of the page), whether VaultLinks keeps it a link."""
    script = (f"import {{ shown, vaultPathOf, viewOf }} from {json.dumps(MODULE.as_uri())};\n"
              "const {links, held} = JSON.parse(await new Response(process.stdin).text());\n"
              "console.log(JSON.stringify(links.map(([url, dir]) => {\n"
              "  const target = vaultPathOf(url, dir);\n"
              "  return target === undefined || shown(viewOf(target), (top) => held.includes(top));\n"
              "})));\n")
    done = subprocess.run(["node", "--input-type=module", "-e", script],
                          input=json.dumps({"links": links, "held": HELD}),
                          capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


def test_the_home_pages_link_to_a_member_page_is_plain_text():
    """Given the home page's member line `[Homer](homer/about)`, when the wiki
    builds it, then Homer's name is not a link: his page is not in the wiki."""
    assert _shown([("homer/about", "")]) == [False]


def test_a_link_to_a_person_card_is_plain_text():
    """Given a family note linking Homer's person card, which maps to his member
    page, then the link is plain text."""
    assert _shown([("../../people/homer.md", "family/notes/2026"),
                   ("/family/people/homer.md", "family/notes/2026")]) == [False, False]


def test_a_link_into_a_persons_own_notes_is_plain_text():
    assert _shown([("/marge/notes/2026/11/planung.md", "family/notes"),
                   ("../../../marge/geschenke/about.md", "family/notes/2026")]) == [False, False]


def test_links_into_the_shared_part_stay_links():
    """Resting state: a family topic, a correspondent card, a shared note, a
    photo and the home page stay links."""
    assert _shown([("../../itchy-scratchy-land/topic.md", "family/notes/2026"),
                   ("/family/correspondents/kwik-e-mart.md", "family/notes"),
                   ("family/lists/dates", ""),
                   ("/media/2026/11/lake.jpg", "family/notes"),
                   ("/index", "family/notes")]) == [True] * 5


def test_links_out_of_the_vault_stay_links():
    assert _shown([("https://example.org/homer/about", ""), ("#sources", "family"),
                   ("mailto:homer@example.org", "family")]) == [True] * 3


def test_a_relative_link_to_a_person_card_maps_to_the_member_page():
    """The mapping a link to a card is read through stays as it was."""
    script = (f"import {{ viewOf, vaultPathOf }} from {json.dumps(MODULE.as_uri())};\n"
              "console.log(JSON.stringify([viewOf(vaultPathOf('../people/homer.md', 'family/notes')),"
              " viewOf('family/garden/topic.md'), viewOf('family/correspondents/moe.md')]));\n")
    done = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(done.stdout) == ["homer/about.md", "family/garden/about.md",
                                       "family/correspondents/moe/about.md"]
