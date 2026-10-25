"""`stack memory person`: show a person, or add one the family knows.

Every person the family knows has a file, `family/people/<id>.md`
(`stack.people`). Account holders get theirs from `stack up memory`;
everyone else, a baby, a grandparent, the family doctor, is added with
`stack memory person add`, so an admin never writes frontmatter by hand.

A name is how every reader finds a person: the classifier, the
transcription vocabulary, the wiki. So a name that already belongs to
someone is refused with whose it is, rather than splitting one person
into two files.

Each command runs the whole way an instance runs it, in this process:
through core's card door to the curator's API, whose card store commits
to the curator's clone and pushes to Forgejo, a bare repository here
(`running_memory`, memory's test instance). A page a test needs first is edited in
Forgejo by a person and pulled by the curator, never written into the
clone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from tests.memory.unit.instance import running_memory  # noqa: E402
from stack.people import load_people, resolve  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


from stack.stacklet_code import load  # noqa: E402

person_cli = load(_REPO_ROOT / "stacklets" / "memory", _REPO_ROOT / "stacklets" / "memory" / "cli" / "person.py")

MAGGIE = "family/people/maggie.md"


@pytest.fixture
def store(tmp_path):
    """The family's memory as a person command reaches it, holding Homer and Marge."""
    people = {f"family/people/{pid}.md": f"---\ntype: person\ntitle: {name}\naccount: {pid}\n---\n"
              for pid, name in {"homer": "Homer Simpson", "marge": "Marge Simpson"}.items()}
    with running_memory(tmp_path, people) as memory:
        yield memory


@pytest.fixture
def run(store):
    def _go(*argv):
        return person_cli.run(list(argv), None, store.config)
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
        store.edited_in_forgejo(
            {"homer/about.md": "---\ntitle: Homer\nslug: homer\ncanonical: Homer\n---\n\n# Homer\n"})

        assert run("show", "homer")["person"] == "homer"

    def test_a_bare_person_lists_its_verbs(self, run):
        assert run()["verbs"] == ["show", "add"]

    def test_a_name_without_a_verb_points_to_show(self, run):
        """`stack memory person <name>` was the read before `add` existed."""
        result = run("homer")
        assert result["error"] == "to read a person: stack memory person show homer"
