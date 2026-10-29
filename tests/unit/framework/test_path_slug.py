"""Every copy of the name-to-path rule spells a name the same way.

A topic room's folder, a correspondent's file and a card id are spelled by
`stack.slug.path_slug`. The agent container has the same file mounted, and
two JavaScript copies follow the rule: the wiki's `fold`, which matches a
folder to its topic, and the dashboard's `username`. Each is held to one
table of examples (`tests/fixtures/path-slugs.json`), so a copy that drifts
fails here rather than as a topic the wiki or the agent no longer finds.

The JavaScript functions run in Node as they are written in their files:
the function's source is cut out of the file and called on every example.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from stack.slug import path_slug

REPO = Path(__file__).resolve().parents[3]
TABLE = json.loads((REPO / "tests/fixtures/path-slugs.json").read_text(encoding="utf-8"))["examples"]
WIKI_MODEL = REPO / "stacklets/memory/quartz/components/familyModel.ts"
DASHBOARD = REPO / "stacklets/core/tools-server/dashboard/first-run.js"


@pytest.mark.parametrize(("name", "slug"), TABLE)
def test_the_framework_spells_each_name_as_the_table_does(name, slug):
    assert path_slug(name) == slug


def test_a_long_name_is_cut_at_a_word_boundary():
    assert path_slug("Camping im Sommer am großen See", limit=22) == "camping-im-sommer-am"


def test_one_word_longer_than_the_limit_is_cut_where_the_limit_is():
    assert path_slug("A" * 100) == "a" * 60


# ── The JavaScript copies ────────────────────────────────────────────────

def _function_source(path: Path, name: str) -> str:
    """The JavaScript function `name` in `path`, without type annotations.

    The TypeScript file's one annotation style (`s: string`, `): string`)
    is all that is stripped; the body is plain JavaScript.
    """
    text = path.read_text(encoding="utf-8")
    start = re.search(rf"function {name}\(", text)
    assert start, f"no function {name} in {path}"
    depth, i = 0, text.index("{", start.end())
    for i in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            break
    source = text[start.start():i + 1]
    return re.sub(r"(\w+|\)): string", r"\1", source)


def _run_in_node(source: str, call: str, names: list[str]) -> list[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = f"{source}\nconsole.log(JSON.stringify({json.dumps(names)}.map((n) => {call})))"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


def test_the_wiki_folds_each_name_to_its_folder():
    """The wiki finds a topic's folder by folding its name: the folded name,
    words joined by hyphens, is the folder."""
    names = [name for name, _ in TABLE]
    folded = _run_in_node(_function_source(WIKI_MODEL, "fold"), 'fold(n).replace(/ /g, "-")', names)
    assert dict(zip(names, folded)) == dict(TABLE)


def test_the_dashboard_spells_a_first_name_as_its_username():
    """A username is a first name in a to z; the table's one-word names are those."""
    words = [(name, slug) for name, slug in TABLE if re.fullmatch(r"[^\W\d_]+", name)]
    spelled = _run_in_node(_function_source(DASHBOARD, "username"), "username(n)", [n for n, _ in words])
    assert dict(zip([n for n, _ in words], spelled)) == dict(words)
