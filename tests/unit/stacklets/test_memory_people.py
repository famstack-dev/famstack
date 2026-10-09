"""The memory stacklet and the family's person files.

Every person the family knows has a file, `family/people/<id>.md`
(`stack.people`). This file pins how the memory stacklet treats them:

- `stack up memory` adds a file for every account that has none, on
  every start, and never rewrites one: the restart of an existing
  install is the upgrade, and the family's edits survive every restart.
- The wiki gives every declared person a page under the names their
  file gives, and its home page lists the members (people with an
  account). Before any file exists the wiki keeps its old roster, so
  nothing changes until the restart.
- Generated person pages nobody declares (`media/about.md`, from the
  old roster) are retired, but never while a person file is broken.
- A change to a person file rebuilds every person page.

Forgejo is stubbed with `pytest-httpserver`; the wiki works on real
files in a temporary brain and vault.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

from lib import REPO_NAME, REPO_OWNER, ensure_people  # noqa: E402
from stack.forgejo import ForgejoClient  # noqa: E402
from stack.people import Person, read_person  # noqa: E402

import wiki  # noqa: E402
from curator import member_selection  # noqa: E402

CONTENTS = f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents"
PEOPLE = f"{CONTENTS}/family/people"

SIMPSONS = [
    {"name": "Homer Simpson", "role": "admin"},
    {"name": "Marge Simpson"},
    {"name": "Bart Simpson"},
    {"name": "Lisa Simpson"},
]

HOMER = "---\ntype: person\ntitle: Homer Simpson\naccount: homer\n---\n"
MARGE = "---\ntype: person\ntitle: Marge Simpson\naccount: marge\n---\n"
MAGGIE = "---\ntype: person\ntitle: Maggie Simpson\naliases:\n  - Margaret\n---\n"


def _client(httpserver) -> ForgejoClient:
    return ForgejoClient(url=httpserver.url_for(""), token="bottoken")


def _listing(*names: str) -> list[dict]:
    return [{"type": "file", "path": f"family/people/{n}", "sha": "abc"} for n in names]


def _committed(httpserver) -> dict[str, str]:
    """The files the one commit created, path to content."""
    post = next(r for r, _ in httpserver.log if r.method == "POST")
    return {f["path"]: base64.b64decode(f["content"]).decode()
            for f in json.loads(post.data)["files"]}


# ── Writing the files on start ───────────────────────────────────────────

class TestTheStartAddsAFilePerAccount:
    def test_an_install_without_files_gets_one_per_account_in_one_commit(self, httpserver):
        httpserver.expect_request(PEOPLE, method="GET").respond_with_data("", status=404)
        httpserver.expect_request(CONTENTS, method="POST").respond_with_json({}, status=201)

        added = ensure_people(_client(httpserver), SIMPSONS)

        assert added == ["homer", "marge", "bart", "lisa"]
        files = _committed(httpserver)
        assert sorted(files) == [f"family/people/{i}.md" for i in ("bart", "homer", "lisa", "marge")]
        homer = read_person("homer", files["family/people/homer.md"])
        assert homer == Person("homer", "Homer Simpson", account="homer")
        assert homer.member

    def test_an_existing_file_is_never_rewritten(self, httpserver):
        """The family gave Homer an alias; a restart adds only who is missing."""
        httpserver.expect_request(PEOPLE, method="GET").respond_with_json(
            _listing("homer.md", "marge.md", "bart.md", "maggie.md"))
        httpserver.expect_request(CONTENTS, method="POST").respond_with_json({}, status=201)

        assert ensure_people(_client(httpserver), SIMPSONS) == ["lisa"]
        assert list(_committed(httpserver)) == ["family/people/lisa.md"]

    def test_nothing_missing_commits_nothing(self, httpserver):
        httpserver.expect_request(PEOPLE, method="GET").respond_with_json(
            _listing("homer.md", "marge.md", "bart.md", "lisa.md"))

        assert ensure_people(_client(httpserver), SIMPSONS) == []
        assert not [r for r, _ in httpserver.log if r.method == "POST"]


# ── The wiki's people ────────────────────────────────────────────────────

@pytest.fixture
def instance(tmp_path, monkeypatch):
    """A vault and brain holding what the old roster got wrong.

    A `media/` folder (diary recordings), a bot's bucket, documents that
    call the baby "Margaret" and name a surname, and a generated person
    page for `media/` left over from the old roster.
    """
    vault, brain = tmp_path / "vault", tmp_path / "brain"
    for root in (vault, brain):
        for folder in ("homer", "marge", "media", "mail-bot", "family/documents"):
            (root / folder).mkdir(parents=True, exist_ok=True)
    (brain / "media" / "about.md").write_text(
        "---\ntitle: Media\nslug: media\ntype: person\ngenerated: true\n---\n\n"
        "<!-- begin: generated -->\n\nMaggie's page, under the wrong folder.\n\n"
        "<!-- end: generated -->\n")
    monkeypatch.setenv("MEMORY_VAULT_DIR", str(vault))
    monkeypatch.setenv("BRAIN_REPO_DIR", str(brain))
    index = [
        {"rel": "family/documents/a.md", "persons": ["margaret"],
         "person_names": ["Margaret"], "title": "Birth certificate"},
        {"rel": "family/documents/b.md", "persons": ["simpson", "homer"],
         "person_names": ["Simpson", "Homer"], "title": "Car insurance"},
    ]
    return vault, brain, index


def _declare(root: Path, **files: str) -> None:
    folder = root / "family" / "people"
    folder.mkdir(parents=True, exist_ok=True)
    for person_id, text in files.items():
        (folder / f"{person_id}.md").write_text(text)


def _people(problems: list[str] | None = None):
    return wiki._people("family", problems if problems is not None else [])


class TestTheWikisPeople:
    def test_before_any_file_exists_the_old_roster_stands(self, instance):
        """Nothing changes until `stack up memory` has written the files."""
        vault, brain, index = instance
        people = _people()
        assert people is None
        assert wiki._roster(brain, index, "family", people) == wiki._member_slugs(
            brain, index, "family")

    def test_every_declared_person_has_a_page_members_first(self, instance):
        vault, brain, index = instance
        _declare(vault, homer=HOMER, marge=MARGE, maggie=MAGGIE)
        assert wiki._roster(brain, index, "family", _people()) == ["homer", "marge", "maggie"]

    def test_names_resolve_through_the_familys_aliases(self, instance):
        """The birth certificate says "Margaret"; it belongs on Maggie's page."""
        vault, brain, index = instance
        _declare(vault, homer=HOMER, maggie=MAGGIE)
        wiki._resolve_persons(index, _people())
        assert index[0]["persons"] == ["maggie"]
        assert wiki._member_entries(index, "maggie") == [index[0]]

    def test_a_name_nobody_answers_to_gets_no_page(self, instance):
        vault, brain, index = instance
        people = [Person("homer", "Homer Simpson", account="homer")]
        wiki._resolve_persons(index, people)
        assert "simpson" not in wiki._roster(brain, index, "family", people)

    def test_the_files_are_read_from_the_vault_before_the_brain(self, instance):
        """An edit is on disk in the vault before the curator mirrors it."""
        vault, brain, index = instance
        _declare(brain, homer=HOMER)
        _declare(vault, homer=HOMER, maggie=MAGGIE)
        assert [p.id for p in _people()] == ["homer", "maggie"]

    def test_a_broken_file_is_reported_and_left_out(self, instance):
        vault, brain, index = instance
        _declare(vault, homer=HOMER, maggie="---\ntype: person\naliases: Margaret\n---\n")
        problems: list[str] = []
        assert [p.id for p in _people(problems)] == ["homer"]
        assert problems[0].startswith("family/people/maggie.md: ")

    def test_member_option_takes_any_name_the_person_answers_to(self):
        people = [Person("maggie", "Maggie Simpson", ("Margaret",))]
        assert wiki._member_id("Margaret", people) == "maggie"
        assert wiki._member_id("Maggie Simpson", people) == "maggie"


class TestEveryDeclaredPersonHasAPage:
    def test_a_person_nothing_names_yet_gets_a_page_without_the_model(self, instance):
        """The home page and the People menu link every person's page."""
        vault, brain, index = instance
        maggie = Person("maggie", "Maggie Simpson", ("Margaret",))
        llm = None  # never called: there is nothing for it to write about

        rc = asyncio.run(wiki._generate_member(
            llm, "maggie", [], brain, person=maggie,
            shared_bucket="family", lang="en", write=True))

        page = (brain / "maggie" / "about.md").read_text()
        assert rc == 0
        assert yaml.safe_load(page.split("---\n")[1])["canonical"] == "Maggie Simpson"
        assert "Nothing filed about Maggie yet" in page

    def test_without_person_files_an_empty_bucket_is_still_skipped(self, instance):
        vault, brain, index = instance
        rc = asyncio.run(wiki._generate_member(
            None, "media", [], brain, shared_bucket="family", lang="en", write=True))
        assert rc == 1


class TestUndeclaredPersonPages:
    def test_a_generated_page_nobody_declares_is_retired(self, instance):
        vault, brain, index = instance
        retired = wiki._retire_undeclared(["homer", "maggie"], "family", write=True)
        assert retired == ["media/about.md"]
        assert not (brain / "media" / "about.md").exists()

    def test_a_page_a_person_wrote_is_left_alone(self, instance):
        vault, brain, index = instance
        (brain / "media" / "about.md").write_text("---\ntitle: Media\n---\n\nOur photos.\n")
        assert wiki._retire_undeclared(["homer"], "family", write=True) == []
        assert (brain / "media" / "about.md").exists()

    def test_a_dry_run_names_the_page_and_keeps_it(self, instance):
        vault, brain, index = instance
        assert wiki._retire_undeclared(["homer"], "family", write=False) == ["media/about.md"]
        assert (brain / "media" / "about.md").exists()

    def test_no_page_is_retired_while_a_person_file_is_broken(self, instance, monkeypatch):
        """Maggie's file has a typo: her page must survive until it is fixed."""
        vault, brain, index = instance
        _declare(vault, homer=HOMER, maggie="---\ntype: person\naliases: Margaret\n---\n")
        (brain / "maggie").mkdir()
        (brain / "maggie" / "about.md").write_text(
            "---\ntitle: Maggie Simpson\nslug: maggie\ntype: person\ngenerated: true\n---\n")

        async def _no_model(*args, **kwargs):
            return 0
        monkeypatch.setattr(wiki, "_generate_home", _no_model)
        monkeypatch.setattr(wiki, "_generate_member", _no_model)
        monkeypatch.setattr(wiki, "_index_vault", lambda vault: index)
        monkeypatch.setattr(wiki, "_brain_dir", lambda: brain)

        asyncio.run(wiki.run(None, ["--members"]))

        assert (brain / "maggie" / "about.md").exists()
        assert (brain / "media" / "about.md").exists()


class TestPersonPageNames:
    def test_the_declared_names_replace_what_the_page_had(self):
        page = ("---\ntitle: Margaret\nslug: maggie\ntype: person\ncanonical: Margaret\n"
                "synonyms:\n  - Maggie\nbirthday: \"2025-01-12\"\n---\n\nbody\n")
        updated = wiki._with_member_names(
            page, canonical="Maggie Simpson", synonyms=["Margaret", "Maggie"])
        frontmatter = yaml.safe_load(updated.split("---\n")[1])
        assert frontmatter == {
            "title": "Maggie Simpson", "canonical": "Maggie Simpson",
            "synonyms": ["Margaret", "Maggie"],
            "slug": "maggie", "type": "person", "birthday": "2025-01-12",
        }
        assert updated.endswith("---\n\nbody\n")

    def test_the_home_page_is_told_the_full_names(self):
        prompt = wiki._build_home_prompt(
            [], roster=["homer", "marge"], lang="en",
            names={"homer": "Homer Simpson", "marge": "Marge Simpson"})
        assert "- **[Marge Simpson](marge/about)**" in prompt
        assert "<full name of" not in prompt


# ── Rebuilding on a change ───────────────────────────────────────────────

class TestTheCuratorRebuildsOnAChange:
    def test_a_changed_person_file_rebuilds_every_person_page(self):
        argv = member_selection(["family/people/maggie.md"], lambda path: {},
                                shared_bucket="family")
        assert argv == ["--home", "--members"]

    def test_other_changes_select_as_before(self):
        argv = member_selection(["homer/notes/2026/10/x.md"], lambda path: {},
                                shared_bucket="family")
        assert argv == ["--home", "--member", "homer"]
