"""The nightly archive: yesterday's ticks leave every list, into one commit.

What a family sees the morning after a shop: the list holds only what
they did not get, and what they got is in the month's archive. The
command is driven against a real HTTP server standing in for Forgejo
(`pytest-httpserver`), answering the calls it makes.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))

from stack.forgejo import ForgejoClient  # noqa: E402
from stack.lists import read_list  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


nightly = _load("memory_bot_cli_lists",
                _REPO_ROOT / "stacklets" / "memory" / "bot" / "cli" / "lists.py")

CONTENTS = f"/api/v1/repos/{nightly.REPO_OWNER}/{nightly.REPO_NAME}/contents"
LIST = (
    "---\ntype: list\ntitle: Einkaufsliste\nsections:\n  - Obst\n  - Milch\n---\n\n"
    "## Obst\n- [x] Äpfel ✅ 2026-10-11\n- [ ] Birnen\n\n"
    "## Milch\n- [x] Joghurt\n"
)


def _file(text: str, sha: str) -> dict:
    return {"type": "file", "content": base64.b64encode(text.encode()).decode(), "sha": sha}


def test_the_morning_after_the_list_holds_what_is_open(httpserver):
    httpserver.expect_request(f"{CONTENTS}/family/lists", method="GET").respond_with_json([
        {"type": "file", "path": "family/lists/einkaufsliste.md"},
        {"type": "dir", "path": "family/lists/archive"},
    ])
    httpserver.expect_request(f"{CONTENTS}/family/lists/einkaufsliste.md",
                              method="GET").respond_with_json(_file(LIST, "list-sha"))
    httpserver.expect_request(f"{CONTENTS}/family/lists/archive/einkaufsliste/2026-10.md",
                              method="GET").respond_with_data("", status=404)
    client = ForgejoClient(url=httpserver.url_for(""), token="bottoken")

    changes, moved = nightly.archive_lists(client, bucket="family", today="2026-10-12")

    assert moved == 1, "Joghurt was ticked by hand today and stays until tomorrow"
    by_path = {c.path: c for c in changes}
    kept = read_list(by_path["family/lists/einkaufsliste.md"].content,
                     path="family/lists/einkaufsliste.md")
    assert [(i.text, i.done, i.done_on) for i in kept.items] == \
        [("Birnen", False, ""), ("Joghurt", True, "2026-10-12")]
    assert by_path["family/lists/einkaufsliste.md"].sha == "list-sha"
    archive = by_path["family/lists/archive/einkaufsliste/2026-10.md"]
    assert archive.operation == "create"
    assert "## Obst\n- [x] Äpfel ✅ 2026-10-11" in archive.content


def test_a_quiet_night_changes_nothing(httpserver):
    httpserver.expect_request(f"{CONTENTS}/family/lists", method="GET").respond_with_json(
        [{"type": "file", "path": "family/lists/packliste.md"}])
    httpserver.expect_request(f"{CONTENTS}/family/lists/packliste.md", method="GET") \
        .respond_with_json(_file("---\ntype: list\ntitle: Packliste\n---\n\n- [ ] Zelt\n", "s"))
    client = ForgejoClient(url=httpserver.url_for(""), token="bottoken")

    assert nightly.archive_lists(client, bucket="family", today="2026-10-12") == ([], 0)


def test_the_morning_after_an_appointment_it_is_off_the_calendar(httpserver):
    calendar = ("---\ntype: list\ntitle: Termine\nkind: calendar\n---\n\n"
                "- [ ] Theater 📅 2026-11-03\n- [ ] Elternabend 📅 2026-11-17\n")
    httpserver.expect_request(f"{CONTENTS}/family/lists", method="GET").respond_with_json(
        [{"type": "file", "path": "family/lists/termine.md"}])
    httpserver.expect_request(f"{CONTENTS}/family/lists/termine.md", method="GET") \
        .respond_with_json(_file(calendar, "cal-sha"))
    httpserver.expect_request(f"{CONTENTS}/family/lists/archive/termine/2026-11.md",
                              method="GET").respond_with_data("", status=404)
    client = ForgejoClient(url=httpserver.url_for(""), token="bottoken")

    changes, moved = nightly.archive_lists(client, bucket="family", today="2026-11-04")

    assert moved == 1
    by_path = {c.path: c for c in changes}
    left = read_list(by_path["family/lists/termine.md"].content, path="family/lists/termine.md")
    assert [i.text for i in left.items] == ["Elternabend"]
    assert by_path["family/lists/archive/termine/2026-11.md"].operation == "create"
