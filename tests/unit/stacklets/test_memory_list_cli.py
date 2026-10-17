"""`stack memory list`: the family's lists, changed one item at a time.

The caller names an item, never a line. The command finds it, changes
that line and nothing else, and answers with what happened, because a
whole-page rewrite was measured to drop ticks (2026-09-15). Whatever
the caller cannot know is refused with the choices it has: an item
that matches several, a section the store list does not have, a person
the family never declared, a list that does not exist.

The `update_memory` seam is substituted, because the real one talks to
Forgejo. The stand-in keeps what the command depends on: the transform
gets the current file, a `ValueError` becomes an error envelope, an
unchanged file commits nothing, a `None` answer deletes the file, and a
commit reaches the vault on disk the way the real write-through pull
does.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


list_cli = _load("memory_cli_list", _REPO_ROOT / "stacklets" / "memory" / "cli" / "list.py")

TODAY = date.today().isoformat()
GROCERIES = "family/lists/einkaufsliste.md"
STORE = ["--section", "Gemüse & Obst", "--section", "Milchprodukte & Eier"]
PEOPLE = {"homer": "Homer Simpson", "marge": "Marge Simpson"}


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Forgejo as the command sees it: files, commits, and the vault on disk."""
    vault = tmp_path / "memory" / "vault"
    for pid, name in PEOPLE.items():
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
            if after == before or (after is None and not before):
                return {"ok": True, "committed": False}
            if after is None:
                (vault / repo_path).unlink()
            else:
                (vault / repo_path).parent.mkdir(parents=True, exist_ok=True)
                (vault / repo_path).write_text(after, encoding="utf-8")
            self.commits.append((repo_path, actor, message(before, after or "")))
            return {"ok": True, "committed": True, "path": repo_path, "mirrored": True}

    fake = _Store()
    fake.vault = vault
    monkeypatch.setattr(list_cli, "update_memory", fake.update_memory)
    return fake


@pytest.fixture
def run(store, tmp_path):
    def _go(*argv):
        return list_cli.run(list(argv), None, {"data_dir": str(tmp_path)})
    return _go


@pytest.fixture
def groceries(run):
    run("create", "einkaufsliste", "--title", "Einkaufsliste",
        "--description", "Was wir im Supermarkt brauchen", *STORE, "--as", "marge")
    return GROCERIES


class TestStartingAList:
    def test_a_new_list_is_a_file_in_the_lists_folder(self, run, store, groceries):
        assert store.text(groceries).startswith("---\ntype: list\ntitle: Einkaufsliste\n")
        assert store.commits[-1][:2] == (groceries, "marge")

    def test_show_names_every_list_with_what_is_open(self, run, groceries):
        run("add", "einkaufsliste", "Äpfel", "--as", "marge")
        result = run("show")
        assert result["lists"] == [{"slug": "einkaufsliste", "title": "Einkaufsliste",
                                    "description": "Was wir im Supermarkt brauchen",
                                    "kind": "todo", "topics": [], "open": 1}]

    def test_a_list_that_exists_is_not_started_again(self, run, groceries):
        result = run("create", "einkaufsliste", "--title", "Noch eine", "--as", "homer")
        assert "already exists" in result["error"]

    def test_a_list_named_by_its_title_alone_gets_a_slug_from_it(self, run, store):
        result = run("create", "--title", "Garage aufräumen", "--as", "homer")
        assert result["path"] == "family/lists/garage-aufraumen.md"

    def test_a_packing_list_is_started_as_a_checklist(self, run, store):
        """Its ticks stay for the next trip; a todo list would archive them overnight."""
        result = run("create", "packliste", "--title", "Packliste", "--kind", "checklist",
                     "--as", "marge")
        assert "kind: checklist\n" in store.text(result["path"])

    def test_a_list_turns_into_a_checklist(self, run, store, groceries):
        run("set", "einkaufsliste", "--kind", "checklist", "--as", "marge")
        assert "kind: checklist\n" in store.text("family/lists/einkaufsliste.md")

    def test_an_appointment_needs_its_date(self, run, store):
        """A calendar is ordered by date and cleared by it; an entry without one would stay forever."""
        run("create", "termine", "--title", "Termine", "--kind", "calendar", "--as", "marge")
        refused = run("add", "termine", "Elternabend", "--as", "marge")
        assert "needs its date" in refused["error"]
        run("add", "termine", "Elternabend", "--due", "2026-11-17", "--as", "marge")
        run("add", "termine", "Theater", "--due", "2026-11-03", "--as", "marge")
        assert store.text("family/lists/termine.md").index("Theater") < \
            store.text("family/lists/termine.md").index("Elternabend")

    def test_a_slug_is_lowercase_words_and_dashes(self, run):
        assert "not a slug" in run("create", "Einkauf Liste", "--title", "x", "--as", "marge")["error"]

    def test_a_change_without_a_person_is_refused(self, run, store, groceries):
        commits = len(store.commits)
        result = run("add", "einkaufsliste", "Milch")
        assert result["error"] == "say who adds: add --as <person>, the family member it is for"
        assert len(store.commits) == commits


class TestAddingItems:
    def test_items_land_in_their_section_in_walking_order(self, run, store, groceries):
        run("add", "einkaufsliste", "Joghurt", "--section", "milchprodukte & eier", "--as", "marge")
        run("add", "einkaufsliste", "Äpfel", "--section", "Gemüse & Obst", "--as", "marge")
        body = store.text(groceries).split("---\n", 2)[2]
        assert body == "\n## Gemüse & Obst\n- [ ] Äpfel\n\n## Milchprodukte & Eier\n- [ ] Joghurt\n"

    def test_a_section_the_list_does_not_have_is_refused_with_its_sections(self, run, store,
                                                                        groceries):
        result = run("add", "einkaufsliste", "Joghurt", "--section", "Dairy", "--as", "marge")
        assert result["error"] == ("Einkaufsliste has no section 'Dairy'. "
                                   "Sections: Gemüse & Obst; Milchprodukte & Eier")
        assert "Joghurt" not in store.text(groceries)

    def test_several_items_are_one_commit(self, run, store, groceries):
        commits = len(store.commits)
        result = run("add", "einkaufsliste", "Milch", "Eier", "--as", "marge")
        assert result["summary"] == "added Milch; Eier to Einkaufsliste"
        assert len(store.commits) == commits + 1

    def test_an_item_already_open_is_not_added_twice(self, run, store, groceries):
        run("add", "einkaufsliste", "Milch", "--as", "marge")
        commits = len(store.commits)
        result = run("add", "einkaufsliste", "milch", "--as", "homer")
        assert result["committed"] is False
        assert result["summary"] == "'Milch' is already on Einkaufsliste"
        assert len(store.commits) == commits

    def test_people_and_a_due_date_are_written_as_fields(self, run, store, groceries):
        run("add", "einkaufsliste", "Kuchen für die Schule", "--for", "Marge", "--for", "homer",
            "--due", "2026-10-20", "--as", "marge")
        assert ("- [ ] Kuchen für die Schule [Marge](../people/marge.md) "
                "[Homer](../people/homer.md) 📅 2026-10-20") in store.text(groceries)

    def test_someone_the_family_never_declared_is_refused(self, run, groceries):
        result = run("add", "einkaufsliste", "Bier", "--for", "Barney", "--as", "homer")
        assert result["error"] == "nobody called 'Barney' in the family. People: Homer, Marge"

    def test_a_due_date_is_a_day(self, run, groceries):
        result = run("add", "einkaufsliste", "Milch", "--due", "Friday", "--as", "marge")
        assert "not a date" in result["error"]


class TestKnownItems:
    """An item the household had before goes where it was last bought."""

    @pytest.fixture
    def shops(self, run, store, groceries):
        run("create", "markt", "--title", "Wochenmarkt", "--description", "Samstags: Gemüse, Käse",
            "--section", "Käse", "--as", "marge")
        archive = "family/lists/archive/einkaufsliste/2026-10.md"
        (store.vault / archive).parent.mkdir(parents=True)
        (store.vault / archive).write_text(
            "---\ntype: list\ntitle: Einkaufsliste, 2026-10\n---\n\n"
            "## Milchprodukte & Eier\n- [x] Joghurt ✅ 2026-10-03\n- [x] Eier ✅ 2026-10-03\n")
        markt = "family/lists/archive/markt/2026-10.md"
        (store.vault / markt).parent.mkdir(parents=True)
        (store.vault / markt).write_text(
            "---\ntype: list\ntitle: Wochenmarkt, 2026-10\n---\n\n- [x] Eier ✅ 2026-10-10\n")

    def test_a_known_item_lands_on_its_list_in_its_section_and_spelling(self, run, store, shops):
        result = run("add", "joghurt", "--as", "marge")
        assert result["summary"] == "added Joghurt to Einkaufsliste"
        assert "## Milchprodukte & Eier\n- [ ] Joghurt\n" in store.text(GROCERIES)

    def test_the_latest_purchase_decides_the_list(self, run, store, shops):
        run("add", "Eier", "--as", "marge")
        assert "- [ ] Eier" in store.text("family/lists/markt.md")

    def test_items_known_on_different_lists_go_to_each(self, run, store, shops):
        result = run("add", "Joghurt", "Eier", "--as", "marge")
        assert [r["summary"] for r in result["lists"]] == \
            ["added Joghurt to Einkaufsliste", "added Eier to Wochenmarkt"]

    def test_a_named_list_wins_over_where_it_was_bought(self, run, store, shops):
        run("add", "einkaufsliste", "Eier", "--as", "marge")
        assert "## Milchprodukte & Eier\n- [ ] Eier" in store.text(GROCERIES)

    def test_an_unknown_item_without_a_list_names_every_list(self, run, shops):
        result = run("add", "Kerzen", "--as", "marge")
        assert result["error"] == (
            "no list has had 'Kerzen' before; name the list. Lists: einkaufsliste "
            "(Was wir im Supermarkt brauchen); markt (Samstags: Gemüse, Käse)")

    def test_a_topic_narrows_the_lists(self, run, shops):
        result = run("add", "Kerzen", "--topic", "garten", "--as", "marge")
        assert "no list names the topic 'garten'" in result["error"]


class TestTheFamilyCalendar:
    """`calendar` names the family calendar, whatever its file is called.

    "Trag es in den Kalender ein" names no list the family has. The calendar's
    file is called after the household language, or whatever the family named
    it, and on an instance where no letter with a date was ever filed there was
    none at all. The agent, with nowhere to put the appointment, wrote it into
    the diary. One name that always works, and a calendar that is there when
    the first date comes, leave nothing to look up or guess.
    """

    @pytest.fixture
    def run_in(self, store, tmp_path):
        def _go(language, *argv):
            config = {"data_dir": str(tmp_path), "stack": {"core": {"language": language}}}
            return list_cli.run(list(argv), None, config)
        return _go

    @pytest.fixture
    def kalender(self, run):
        run("create", "kalender", "--title", "Kalender", "--kind", "calendar", "--as", "marge")
        return "family/lists/kalender.md"

    def test_an_entry_lands_on_the_calendar_the_family_has(self, run, store, kalender):
        result = run("add", "calendar", "10:30 Treff am Bahnhof", "--due", "2026-10-18",
                     "--as", "marge")
        assert result["summary"] == "added 10:30 Treff am Bahnhof to Kalender"
        assert "- [ ] 10:30 Treff am Bahnhof 📅 2026-10-18" in store.text(kalender)

    def test_the_first_entry_starts_the_calendar_in_the_household_language(self, run_in, store):
        result = run_in("de", "add", "calendar", "Elternabend", "--due", "2026-11-17",
                        "--as", "marge")
        page = store.text("family/lists/termine.md")
        assert page.startswith("---\ntype: list\ntitle: Termine\n")
        assert "kind: calendar\n" in page and "- [ ] Elternabend" in page
        assert result["summary"] == "added Elternabend to Termine"
        assert len(store.commits) == 1

    def test_an_english_household_gets_dates(self, run_in, store):
        run_in("en", "add", "calendar", "Parent evening", "--due", "2026-11-17", "--as", "marge")
        assert "title: Dates\n" in store.text("family/lists/dates.md")

    def test_an_entry_known_from_another_list_still_goes_on_the_calendar(
            self, run, store, groceries, kalender):
        """The name decides, not where the words were last seen."""
        run("add", "einkaufsliste", "Zahnarzt", "--as", "marge")
        run("add", "calendar", "Zahnarzt", "--for", "homer", "--due", "2026-11-05", "--as", "marge")
        assert "Zahnarzt" in store.text(kalender)

    def test_an_entry_still_needs_its_date(self, run, store):
        refused = run("add", "calendar", "Elternabend", "--as", "marge")
        assert "needs its date" in refused["error"]
        assert store.commits == []

    def test_the_name_works_for_showing_and_ticking(self, run, store, kalender):
        run("add", "calendar", "Elternabend", "--due", "2026-11-17", "--as", "marge")
        assert run("show", "calendar")["title"] == "Kalender"
        run("tick", "calendar", "Elternabend", "--as", "marge")
        assert "- [x] Elternabend" in store.text(kalender)

    def test_ticking_on_a_calendar_that_does_not_exist_starts_nothing(self, run, store):
        refused = run("tick", "calendar", "Elternabend", "--as", "marge")
        assert "no list 'calendar'" in refused["error"]
        assert store.commits == []

    def test_a_list_the_family_called_calendar_is_itself(self, run, store, kalender):
        run("create", "calendar", "--title", "Calendar", "--as", "bart")
        run("add", "calendar", "Comics", "--as", "bart")
        assert "Comics" in store.text("family/lists/calendar.md")

    def test_a_date_without_a_list_is_not_put_on_the_calendar(self, run, store, groceries):
        """Without the name nothing is guessed: the caller is asked which list."""
        result = run("add", "Kerzen", "--due", "2026-10-20", "--as", "marge")
        assert "name the list" in result["error"]
        assert not (store.vault / "family/lists/dates.md").exists()


class TestTickingOff:
    @pytest.fixture
    def stocked(self, run, groceries):
        run("add", "einkaufsliste", "Hafermilch", "Milch", "Haferflocken", "--as", "marge")
        return groceries

    def test_a_tick_records_the_day(self, run, store, stocked):
        result = run("tick", "einkaufsliste", "Milch", "--as", "@homer:example.org")
        assert f"- [x] Milch {chr(0x2705)} {TODAY}" in store.text(stocked)
        assert result["summary"] == "ticked off Milch on Einkaufsliste"
        assert store.commits[-1][1:] == ("homer", "chore(lists): homer ticked off Milch on Einkaufsliste")

    def test_a_reopened_item_loses_its_tick_day(self, run, store, stocked):
        run("tick", "einkaufsliste", "Milch", "--as", "homer")
        run("untick", "einkaufsliste", "Milch", "--as", "homer")
        assert "- [ ] Milch\n" in store.text(stocked)

    def test_ticking_twice_changes_nothing_and_says_so(self, run, store, stocked):
        run("tick", "einkaufsliste", "Milch", "--as", "homer")
        commits = len(store.commits)
        result = run("tick", "einkaufsliste", "Milch", "--as", "homer")
        assert result["summary"] == "'Milch' is already ticked" and len(store.commits) == commits

    def test_a_name_that_fits_several_items_names_them_all(self, run, store, stocked):
        before = store.text(stocked)
        result = run("tick", "einkaufsliste", "hafer", "--as", "homer")
        assert result["error"] == ("'hafer' matches several items: Hafermilch; Haferflocken. "
                                   "Name the item more exactly.")
        assert store.text(stocked) == before

    def test_a_name_that_fits_nothing_lists_what_is_open(self, run, stocked):
        result = run("tick", "einkaufsliste", "Kaffee", "--as", "homer")
        assert result["error"] == ("no item matching 'Kaffee' on Einkaufsliste. "
                                   "Open items: Hafermilch; Milch; Haferflocken")

    def test_an_item_bought_and_needed_again_is_still_named_by_its_name(self, run, store,
                                                                        stocked):
        run("tick", "einkaufsliste", "Milch", "--as", "homer")
        run("add", "einkaufsliste", "Milch", "--as", "marge")
        assert run("tick", "einkaufsliste", "Milch", "--as", "homer")["committed"] is True
        assert store.text(stocked).count("- [x] Milch") == 2
        assert run("untick", "einkaufsliste", "Milch", "--as", "homer")["committed"] is True

    def test_reset_reopens_everything_ticked(self, run, store, stocked):
        run("tick", "einkaufsliste", "Milch", "Hafermilch", "--as", "homer")
        result = run("reset", "einkaufsliste", "--as", "marge")
        assert result["summary"] == "reopened Hafermilch; Milch on Einkaufsliste"
        assert "- [x]" not in store.text(stocked)


class TestTickingWithoutNamingTheList:
    """"I signed the permission slip" ticks it on whichever list the archivist put it."""

    @pytest.fixture
    def school(self, run, groceries):
        run("create", "education", "--title", "Education", "--as", "marge")
        run("add", "education", "Sign the permission slip", "Pay the trip fee", "--as", "marge")
        run("add", "einkaufsliste", "Milch", "--as", "marge")

    def test_an_item_is_found_on_its_list(self, run, store, school):
        result = run("tick", "permission slip", "--as", "marge")
        assert result["summary"] == "ticked off Sign the permission slip on Education"
        assert "- [x] Sign the permission slip" in store.text("family/lists/education.md")

    def test_items_on_different_lists_are_each_ticked_there(self, run, school):
        result = run("tick", "trip fee", "Milch", "--as", "homer")
        assert [r["summary"] for r in result["lists"]] == [
            "ticked off Pay the trip fee on Education", "ticked off Milch on Einkaufsliste"]

    def test_the_open_one_is_ticked_when_another_list_has_it_ticked(self, run, store, school):
        run("add", "education", "Milch", "--as", "marge")
        run("tick", "education", "Milch", "--as", "marge")
        run("tick", "Milch", "--as", "homer")
        assert "- [x] Milch" in store.text(GROCERIES)

    def test_an_item_open_on_two_lists_is_refused_with_both(self, run, school):
        run("add", "education", "Milch", "--as", "marge")
        result = run("tick", "Milch", "--as", "homer")
        assert result["error"] == ("'Milch' is on several lists (education: Milch; "
                                   "einkaufsliste: Milch). Name the list.")

    def test_an_item_on_no_list_is_refused(self, run, school):
        assert run("tick", "Kaffee", "--as", "homer")["error"] == "no item matching 'Kaffee' on any list"


class TestRemovingAndMoving:
    def test_removing_the_last_item_of_a_section_removes_its_heading(self, run, store, groceries):
        run("add", "einkaufsliste", "Äpfel", "--section", "Gemüse & Obst", "--as", "marge")
        run("add", "einkaufsliste", "Joghurt", "--section", "Milchprodukte & Eier", "--as", "marge")
        run("remove", "einkaufsliste", "Äpfel", "--as", "marge")
        assert store.text(groceries).split("---\n", 2)[2] == \
            "\n## Milchprodukte & Eier\n- [ ] Joghurt\n"

    def test_a_moved_item_keeps_its_people_and_leaves_the_old_list(self, run, store, groceries,
                                                                   capsys):
        run("create", "markt", "--title", "Wochenmarkt", "--as", "marge")
        run("add", "einkaufsliste", "Eier", "--section", "Milchprodukte & Eier", "--for", "homer",
            "--as", "marge")
        capsys.readouterr()
        result = run("move", "einkaufsliste", "Eier", "--to", "markt", "--as", "marge")
        assert "error" not in result
        assert capsys.readouterr().out.splitlines()[0] == \
            "moved Eier from Einkaufsliste to Wochenmarkt (by marge)", "one move, one line"
        assert "- [ ] Eier [Homer](../people/homer.md)" in store.text("family/lists/markt.md")
        assert store.text(groceries).endswith("  - Milchprodukte & Eier\n---\n"), \
            "the old list is empty again, with no heading left behind"

    def test_an_item_whose_name_is_part_of_another_still_arrives(self, run, store, groceries):
        """A move removes the item from the old list, so the new one must hold it."""
        run("create", "markt", "--title", "Wochenmarkt", "--as", "marge")
        run("add", "markt", "Bio-Eier", "--as", "marge")
        run("add", "einkaufsliste", "Eier", "--section", "Milchprodukte & Eier", "--as", "marge")
        run("move", "einkaufsliste", "Eier", "--to", "markt", "--as", "marge")
        assert "- [ ] Eier\n" in store.text("family/lists/markt.md")

    def test_an_item_ticked_on_the_other_list_arrives_open(self, run, store, groceries):
        run("create", "markt", "--title", "Wochenmarkt", "--as", "marge")
        run("add", "markt", "Eier", "--as", "marge")
        run("tick", "markt", "Eier", "--as", "marge")
        run("add", "einkaufsliste", "Eier", "--section", "Milchprodukte & Eier", "--as", "marge")
        run("move", "einkaufsliste", "Eier", "--to", "markt", "--as", "marge")
        assert "- [ ] Eier\n" in store.text("family/lists/markt.md")

    def test_an_item_open_on_both_lists_is_kept_once(self, run, store, groceries):
        run("create", "markt", "--title", "Wochenmarkt", "--as", "marge")
        run("add", "markt", "Eier", "--as", "marge")
        run("add", "einkaufsliste", "Eier", "--section", "Milchprodukte & Eier", "--as", "marge")
        run("move", "einkaufsliste", "Eier", "--to", "markt", "--as", "marge")
        assert store.text("family/lists/markt.md").count("Eier") == 1
        assert "Eier\n" not in store.text(groceries).split("---\n", 2)[2]

    def test_a_list_that_does_not_exist_is_refused_with_the_lists(self, run, groceries):
        result = run("move", "einkaufsliste", "Eier", "--to", "baumarkt", "--as", "marge")
        assert result["error"] == "no list 'baumarkt'. Lists: einkaufsliste"


class TestDeletingAList:
    """A list nobody needs any more is deleted, never emptied and left behind.

    Deleting a list that still has open items needs `--yes`: the refusal
    names them, so whoever asked can be asked whether those go too.
    """

    def test_a_list_with_nothing_open_is_deleted_in_one_commit(self, run, store, groceries):
        result = run("delete", "einkaufsliste", "--as", "marge")
        assert result["committed"] and not store.text(groceries)
        assert store.commits[-1][1:] == ("marge", "chore(lists): marge deleted the list Einkaufsliste")

    def test_the_answer_links_no_page_that_is_gone(self, run, groceries, capsys, monkeypatch):
        monkeypatch.setattr(list_cli, "public", lambda page, base: f"https://home.example/go/{page}")
        run("delete", "einkaufsliste", "--as", "marge")
        assert capsys.readouterr().out.splitlines()[-1] == \
            "deleted the list Einkaufsliste (by marge)"

    def test_open_items_are_named_and_kept_without_yes(self, run, store, groceries):
        run("add", "einkaufsliste", "Milch", "Äpfel", "--as", "marge")
        result = run("delete", "einkaufsliste", "--as", "marge")
        assert result["error"] == ("Einkaufsliste still has 2 open items: Milch; Äpfel. "
                                   "Delete it with them: add --yes")
        assert "Milch" in store.text(groceries)

    def test_yes_deletes_it_with_its_open_items(self, run, store, groceries):
        run("add", "einkaufsliste", "Milch", "--as", "marge")
        assert run("delete", "einkaufsliste", "--yes", "--as", "marge")["committed"]
        assert not store.text(groceries)
        assert run("show")["lists"] == []

    def test_its_archive_stays_as_the_history_of_what_was_bought(self, run, store, groceries):
        archive = store.vault / "family" / "lists" / "archive" / "einkaufsliste" / "2026-09.md"
        archive.parent.mkdir(parents=True)
        archive.write_text("---\ntype: list\ntitle: Einkaufsliste\n---\n\n- [x] Milch ✅ 2026-09-02\n")
        run("delete", "einkaufsliste", "--as", "marge")
        assert archive.exists()

    def test_a_list_that_does_not_exist_is_refused_with_the_lists(self, run, groceries):
        assert run("delete", "baumarkt", "--as", "marge")["error"] == \
            "no list 'baumarkt'. Lists: einkaufsliste"


class TestTheHistory:
    def test_a_long_change_counts_its_items_in_the_subject(self, run, store, groceries):
        names = ["Hafermilch ohne Zucker", "Vollkornbrot vom Bäcker", "Bio-Joghurt Natur"]
        run("add", "einkaufsliste", *names, "--as", "marge")
        subject, _, body = store.commits[-1][2].partition("\n\n")
        assert subject == "chore(lists): marge added 3 items to Einkaufsliste"
        assert body == f"added {'; '.join(names)} to Einkaufsliste"


class TestChangingAList:
    def test_a_list_joins_a_topic_room_after_the_fact(self, run, store, groceries):
        run("add", "einkaufsliste", "Milch", "--as", "marge")
        result = run("set", "einkaufsliste", "--topic", "einkauf", "--as", "marge")
        assert result["summary"] == "changed the topics of Einkaufsliste"
        assert "topics:\n  - einkauf\n" in store.text(groceries)
        assert "- [ ] Milch" in store.text(groceries)

    def test_an_empty_topic_leaves_it_in_none(self, run, store, groceries):
        run("set", "einkaufsliste", "--topic", "", "--as", "marge")
        assert "topics:" not in store.text(groceries)

    def test_a_new_walking_order_reorders_the_sections(self, run, store, groceries):
        run("add", "einkaufsliste", "Äpfel", "--section", "Gemüse & Obst", "--as", "marge")
        run("add", "einkaufsliste", "Milch", "--section", "Milchprodukte & Eier", "--as", "marge")
        run("set", "einkaufsliste", "--section", "Milchprodukte & Eier", "--section", "Gemüse & Obst",
            "--as", "marge")
        body = store.text(groceries).split("---\n", 2)[2]
        assert body.index("Milchprodukte") < body.index("Gemüse")

    def test_nothing_to_change_is_refused(self, run, groceries):
        assert run("set", "einkaufsliste", "--as", "marge")["error"].startswith("name what to change")


class TestShowingAList:
    def test_items_appear_under_their_sections_with_who_and_when(self, run, groceries, capsys):
        run("add", "einkaufsliste", "Joghurt", "--section", "Milchprodukte & Eier",
            "--for", "marge", "--due", "2026-10-14", "--as", "marge")
        capsys.readouterr()
        run("show", "einkaufsliste")
        assert capsys.readouterr().out.splitlines()[1:] == [
            "## Milchprodukte & Eier",
            "- [ ] Joghurt  (Marge, due 2026-10-14)",
        ]

    def test_without_a_verb_it_prints_the_verbs(self, run, capsys):
        assert run()["verbs"][0] == "show"
        assert "stack memory list add" in capsys.readouterr().out
