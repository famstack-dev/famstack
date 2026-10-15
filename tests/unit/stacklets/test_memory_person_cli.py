"""`stack memory person`: show a person, or add one the family knows.

Every person the family knows has a file, `family/people/<id>.md`
(`stack.people`). Account holders get theirs from `stack up memory`;
everyone else, a baby, a grandparent, the family doctor, is added with
`stack memory person add`, so an admin never writes frontmatter by hand.

A name is how every reader finds a person: the classifier, the
transcription vocabulary, the wiki. So a name that already belongs to
someone is refused with whose it is, rather than splitting one person
into two files.

The `update_memory` seam is substituted, because the real one talks to
Forgejo. The stand-in keeps what the command depends on: the transform
gets the current file, a `ValueError` becomes an error envelope, and a
commit reaches the vault on disk the way the real write-through pull
does.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))

from stack.people import load_people, resolve  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


person_cli = _load("memory_cli_person", _REPO_ROOT / "stacklets" / "memory" / "cli" / "person.py")

MAGGIE = "family/people/maggie.md"


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Forgejo as the command sees it: the family's files and its commits."""
    vault = tmp_path / "memory" / "vault"
    for pid, name in {"homer": "Homer Simpson", "marge": "Marge Simpson"}.items():
        person = vault / "family" / "people" / f"{pid}.md"
        person.parent.mkdir(parents=True, exist_ok=True)
        person.write_text(f"---\ntype: person\ntitle: {name}\naccount: {pid}\n---\n")

    class _Store:
        def __init__(self):
            self.commits: list[tuple[str, str, str]] = []

        def text(self, path: str) -> str:
            file = vault / path
            return file.read_text(encoding="utf-8") if file.exists() else ""

        def update_memory(self, config, repo_path, transform, *, actor, message):
            before = self.text(repo_path)
            try:
                after = transform(before)
            except ValueError as e:
                return {"error": str(e)}
            (vault / repo_path).write_text(after, encoding="utf-8")
            self.commits.append((repo_path, actor, message(before, after)))
            return {"ok": True, "committed": True, "path": repo_path, "mirrored": True}

    fake = _Store()
    fake.vault = vault
    monkeypatch.setattr(person_cli, "update_memory", fake.update_memory)
    monkeypatch.setattr(person_cli, "refresh_vault_if_stale", lambda *a, **k: None)
    return fake


@pytest.fixture
def run(store, tmp_path):
    def _go(*argv):
        return person_cli.run(list(argv), None, {"data_dir": str(tmp_path)})
    return _go


class TestAddingAPerson:
    def test_a_person_without_an_account_gets_a_file_under_their_first_name(self, run, store):
        result = run("add", "Maggie Simpson", "--alias", "Margaret", "--as", "marge")

        assert result["path"] == MAGGIE
        assert store.commits == [(MAGGIE, "marge", "people: marge added Maggie Simpson")]

    def test_every_reader_then_knows_them_by_each_name(self, run, store):
        """The file is in the format the classifier, the diary and the wiki read."""
        run("add", "Maggie Simpson", "--alias", "Margaret", "--alias", "Mags", "--as", "marge")

        people = load_people(store.vault)
        assert resolve("Margaret", people).id == "maggie"
        assert resolve("Mags", people).id == "maggie"
        assert resolve("Maggie Simpson", people).member is False

    def test_a_name_that_belongs_to_someone_is_refused_with_whose_it_is(self, run, store):
        """A second file for one person would split what is filed about them."""
        result = run("add", "Abraham Simpson", "--alias", "Homer Simpson", "--as", "marge")

        assert result["error"] == "'Homer Simpson' is already a name of Homer Simpson (homer)"
        assert store.commits == []

    def test_a_taken_id_is_refused_until_another_is_given(self, run, store):
        """Two people can share a first name; their files cannot share a name."""
        refused = run("add", "Homer Thompson", "--as", "marge")
        assert refused["error"] == ("'homer' is taken by Homer Simpson; "
                                    "give this person another with --id")

        added = run("add", "Homer Thompson", "--id", "homer-thompson", "--as", "marge")
        assert added["path"] == "family/people/homer-thompson.md"

    def test_an_id_is_lowercase_letters_digits_and_dashes(self, run, store):
        result = run("add", "Grampa", "--id", "Grampa Simpson", "--as", "marge")
        assert "not an id" in result["error"]
        assert store.commits == []

    def test_an_addition_without_a_person_is_refused(self, run, store):
        """Every change is a commit with a person's name on it."""
        result = run("add", "Maggie Simpson")
        assert result["error"] == "say who adds them: add --as <person>, the family member it is for"
        assert store.commits == []


class TestTheVerbs:
    def test_show_reads_a_persons_page(self, run, store):
        page = store.vault / "homer" / "about.md"
        page.parent.mkdir(parents=True)
        page.write_text("---\ntitle: Homer\nslug: homer\ncanonical: Homer\n---\n\n# Homer\n")

        assert run("show", "homer")["person"] == "homer"

    def test_a_bare_person_lists_its_verbs(self, run):
        assert run()["verbs"] == ["show", "add"]

    def test_a_name_without_a_verb_points_to_show(self, run):
        """`stack memory person <name>` was the read before `add` existed."""
        result = run("homer")
        assert result["error"] == "to read a person: stack memory person show homer"
