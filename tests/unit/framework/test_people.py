"""The family's people: one declared file each, read strictly.

`<shared_bucket>/people/<id>.md` is the only place the family says who
it knows (docs/design/brain/vault-format.md §5, `person` (declared)).
These tests pin what the readers depend on: the format, that a member
is a person with an account, that a broken file is reported by path
and never guessed at, and that "no people" is a different answer from
"these people".
"""

from __future__ import annotations

from stack.people import (
    Person,
    find_people,
    load_people,
    members,
    people_dir,
    people_from_accounts,
    read_person,
    render_person,
    resolve,
)


def _write(root, name: str, text: str, bucket: str = "family") -> None:
    folder = people_dir(root, bucket)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


HOMER = "---\ntype: person\ntitle: Homer Simpson\naccount: homer\n---\n\n# Homer Simpson\n"
MAGGIE = ("---\ntype: person\ntitle: Maggie Simpson\naliases:\n  - Margaret\n"
          "  - Mags\n---\n\nSays her first word in season four.\n")


class TestReadingAPersonFile:
    def test_the_file_name_is_the_id_and_the_frontmatter_the_names(self):
        assert read_person("maggie", MAGGIE) == Person(
            "maggie", "Maggie Simpson", ("Margaret", "Mags"))

    def test_a_person_with_an_account_is_a_member(self):
        """The first iteration's guess at membership: an account."""
        assert read_person("homer", HOMER).member

    def test_a_person_without_an_account_is_known_but_not_a_member(self):
        assert not read_person("maggie", MAGGIE).member

    def test_the_body_is_the_familys_and_never_read(self):
        assert read_person("maggie", MAGGIE).aliases == ("Margaret", "Mags")

    def test_every_name_a_person_answers_to(self):
        """Full name, first name, then aliases: what the classifier and
        transcription listen for."""
        assert read_person("maggie", MAGGIE).all_names() == [
            "Maggie Simpson", "Maggie", "Margaret", "Mags"]

    def test_a_file_named_in_obsidian_still_names_a_person(self):
        text = "---\ntype: person\ntitle: Abe Simpson\n---\n"
        assert read_person("Grampa Simpson", text).id == "grampa-simpson"


class TestABrokenFileIsReportedNotGuessed:
    """A family edits these by hand. A mistake must say where it is."""

    def _problems(self, tmp_path, text: str) -> list[str]:
        _write(tmp_path, "homer.md", HOMER)
        _write(tmp_path, "maggie.md", text)
        problems: list[str] = []
        people = load_people(tmp_path, report=problems.append)
        assert [p.id for p in people or []] == ["homer"]
        return problems

    def test_frontmatter_outside_the_format(self, tmp_path):
        problems = self._problems(
            tmp_path, "---\ntype: person\ntitle: Maggie\naliases: |\n  Margaret\n---\n")
        assert problems and problems[0].startswith("family/people/maggie.md: ")

    def test_one_alias_where_a_list_belongs(self, tmp_path):
        problems = self._problems(
            tmp_path, "---\ntype: person\ntitle: Maggie Simpson\naliases: Margaret\n---\n")
        assert "`aliases` must be a list" in problems[0]

    def test_a_file_without_a_name(self, tmp_path):
        problems = self._problems(tmp_path, "---\ntype: person\naliases:\n  - Margaret\n---\n")
        assert "`title`" in problems[0]

    def test_a_note_that_is_not_a_person(self, tmp_path):
        problems = self._problems(tmp_path, "Ideas for Maggie's birthday\n")
        assert "type: person" in problems[0]

    def test_bytes_that_are_not_text(self, tmp_path):
        _write(tmp_path, "homer.md", HOMER)
        (people_dir(tmp_path) / "maggie.md").write_bytes(b"---\ntype: \xff\xfe\n---\n")
        problems: list[str] = []
        assert [p.id for p in load_people(tmp_path, report=problems.append)] == ["homer"]
        assert "cannot be read" in problems[0]

    def test_an_id_declared_twice_counts_once(self, tmp_path):
        _write(tmp_path, "homer.md", HOMER)
        _write(tmp_path, "Homer!.md", HOMER)
        problems: list[str] = []
        assert len(load_people(tmp_path, report=problems.append)) == 1
        assert "declared twice" in problems[0]


class TestNoPeopleIsNotAnEmptyHousehold:
    def test_no_folder_reads_as_none(self, tmp_path):
        """Readers keep their previous behaviour until the files exist."""
        assert load_people(tmp_path) is None

    def test_a_folder_of_broken_files_reads_as_none(self, tmp_path):
        """Nobody readable is never acted on as "nobody"."""
        _write(tmp_path, "maggie.md", "not a person\n")
        assert load_people(tmp_path) is None

    def test_the_files_live_in_the_shared_bucket(self, tmp_path):
        _write(tmp_path, "homer.md", HOMER, bucket="familie")
        assert [p.id for p in load_people(tmp_path, "familie")] == ["homer"]
        assert load_people(tmp_path) is None

    def test_the_first_root_with_people_wins(self, tmp_path):
        """The working copy is fresher than the brain mirror of it."""
        vault, brain = tmp_path / "vault", tmp_path / "brain"
        _write(brain, "homer.md", HOMER)
        assert [p.id for p in find_people((None, vault, brain))] == ["homer"]
        _write(vault, "maggie.md", MAGGIE)
        assert [p.id for p in find_people((vault, brain))] == ["maggie"]


class TestWhoIsWho:
    def test_members_come_first_then_everyone_else_by_name(self, tmp_path):
        _write(tmp_path, "maggie.md", MAGGIE)
        _write(tmp_path, "marge.md", "---\ntype: person\ntitle: Marge Simpson\naccount: marge\n---\n")
        _write(tmp_path, "homer.md", HOMER)
        people = load_people(tmp_path)
        assert [p.id for p in people] == ["homer", "marge", "maggie"]
        assert [p.id for p in members(people)] == ["homer", "marge"]

    def test_a_name_resolves_by_any_name_the_person_goes_by(self):
        maggie = read_person("maggie", MAGGIE)
        assert resolve("margaret", [maggie]) is maggie
        assert resolve("Maggie Simpson", [maggie]) is maggie
        assert resolve("Simpson", [maggie]) is None


class TestTheFilesFamstackWrites:
    """`stack up memory` adds one for every account that has none."""

    def test_every_account_holder_becomes_a_member(self):
        users = [
            {"name": "Homer Simpson", "role": "admin"},
            {"name": "Marge Simpson"},
            {"name": "Bart", "id": "bart"},
        ]
        assert people_from_accounts(users) == [
            Person("homer", "Homer Simpson", account="homer"),
            Person("marge", "Marge Simpson", account="marge"),
            Person("bart", "Bart", account="bart"),
        ]

    def test_the_id_is_the_chat_user_name(self):
        """An explicit `id` in users.toml is the Matrix localpart; so is the person id."""
        assert people_from_accounts([{"name": "Homer Simpson", "id": "hjs"}])[0].id == "hjs"

    def test_an_entry_without_a_name_is_skipped(self):
        assert people_from_accounts([{"id": "ghost"}]) == []

    def test_a_written_file_reads_back_as_the_same_person(self):
        homer = Person("homer", "Homer Simpson", ("Homie",), account="homer")
        assert read_person("homer", render_person(homer)) == homer

    def test_a_written_file_follows_the_vault_format(self):
        page = render_person(Person("homer", "Homer Simpson", account="homer"))
        assert page.startswith("---\ntype: person\ntitle: Homer Simpson\naccount: homer\n---\n")
