"""`stack memory find`, `card` and `links`: the family memory as linked cards.

The agent reads the family memory by card: it finds cards by words, opens
one by its id, and follows its links. Each card says who changed it last
and in which commit, so a card that changed since the agent read it can be
told apart from one that did not.

The family memory here is a real git repository with commits made as
the people who made them, the way every change reaches the vault
(ADR-015). Every file in it is a card. The brain holds what the wiki
compiles from the cards (a person's page, a diary month): views, read with
the card they are about and never cards of their own (ADR-016 §3).
"""

from __future__ import annotations

import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _load(name: str):
    path = _REPO_ROOT / "stacklets" / "memory" / "cli" / f"{name}.py"
    from stack.stacklet_code import load
    return load(_REPO_ROOT / "stacklets" / "memory", path)


find_cli, card_cli, links_cli = _load("find"), _load("card"), _load("links")

RECENT = (date.today() - timedelta(days=2)).isoformat()
LONG_AGO = (date.today() - timedelta(days=900)).isoformat()


class Memory:
    """A family memory on disk: files committed as the people who wrote them."""

    def __init__(self, root: Path):
        self.data_dir = root
        self.vault = root / "memory" / "vault"
        self.brain = root / "memory" / "brain"
        self.vault.mkdir(parents=True)
        self._git("init", "-q")

    def compile(self, path: str, text: str) -> None:
        """A page the wiki compiles into the brain: a view, never a card."""
        file = self.brain / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def write(self, path: str, text: str, *, by: str, message: str) -> str:
        file = self.vault / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
        self._git("add", path)
        self._git("-c", f"user.name={by}", "-c", f"user.email={by}@famstack",
                  "commit", "-q", "-m", message)
        return self._git("rev-parse", "--short=7", "HEAD").strip()

    def file_many(self, count: int, *, by: str) -> None:
        """`count` filings, one commit each, as years of a family's use leave them."""
        branch = self._git("symbolic-ref", "HEAD").strip()
        lines = []
        for i in range(count):
            path, body = f"{by}/notes/many-{i}.md", _note(f"Many{i:09d}", f"Notiz {i}", RECENT).encode()
            lines += [f"commit {branch}".encode(),
                      f"committer {by} <{by}@famstack> {1700000000 + i} +0000".encode(),
                      b"data 6", b"filing"]
            if i == 0:
                lines.append(f"from {self._git('rev-parse', 'HEAD').strip()}".encode())
            lines += [f"M 100644 inline {path}".encode(), f"data {len(body)}".encode(), body, b""]
        subprocess.run(["git", "-C", str(self.vault), "fast-import", "--quiet", "--force"],
                       input=b"\n".join(lines), check=True)
        self._git("reset", "-q", "--hard")

    def _git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.vault), *args], check=True,
                              capture_output=True, text=True).stdout


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path)
    m.write("family/people/lisa.md", "---\ntype: person\ntitle: Lisa Simpson\ncard_id: person-lisa\n"
            "aliases:\n  - Lis\n---\nLisa plays the saxophone.\n",
            by="marge", message="chore(people): marge described Lisa")
    m.write("family/camping/about.md", "---\ntype: topic\ntitle: Camping\nslug: camping\n---\n"
            "The family's camping trips.\n", by="homer", message="feat(topics): homer added camping")
    return m


def _run(cli, memory, *args):
    return cli.run(list(args), None, {"data_dir": str(memory.data_dir)})


def _note(capture_id: str, title: str, day: str, body: str = "", persons=()) -> str:
    people = "".join(f"\n  - {p}" for p in persons)
    return (f"---\ntype: note\ntitle: {title}\ndate: \"{day}\"\ncapture_id: \"${capture_id}\"\n"
            + (f"persons:{people}\n" if persons else "") + f"---\n{body}\n")


class TestFindingCards:

    def test_a_card_is_named_by_its_id_with_its_date_and_version(self, memory, capsys):
        sha = memory.write("homer/notes/zelt.md", _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT),
                           by="homer", message="feat(notes): homer filed Zelt ist kaputt")

        _run(find_cli, memory, "zelt")

        line = capsys.readouterr().out.splitlines()[0]
        assert line.startswith("capture/Abc123Def4  note  " + RECENT + "  Zelt ist kaputt")
        assert f"@{sha}" in line

    def test_a_recent_card_ranks_above_an_old_one_that_matches_a_little_better(self, memory,
                                                                                capsys):
        memory.write("homer/notes/old.md", _note("Old0000000001", "Camping 2023", LONG_AGO,
                                                 body="Zelt, Zelt, Zelt aufgebaut."),
                     by="homer", message="feat(notes): old")
        memory.write("homer/notes/new.md", _note("New0000000001", "Camping jetzt", RECENT,
                                                 body="Das Zelt ist gerissen."),
                     by="homer", message="feat(notes): new")

        _run(find_cli, memory, "zelt")

        out = capsys.readouterr().out
        assert out.index("Camping jetzt") < out.index("Camping 2023")

    def test_a_card_many_others_point_to_ranks_above_an_isolated_one(self, memory, capsys):
        """Of two cards that match alike, the one the family keeps linking
        to is the one that matters (a trip's overview, not a stray note)."""
        memory.write("homer/notes/a.md", _note("Lonely0000001", "Zelt allein", RECENT),
                     by="homer", message="feat(notes): a")
        memory.write("homer/notes/b.md", _note("Linked0000001", "Zelt geteilt", RECENT),
                     by="homer", message="feat(notes): b")
        for i in range(5):
            memory.write(f"homer/notes/ref{i}.md",
                         _note(f"Ref{i:010d}", f"Ausflug {i}", RECENT, body="Siehe [b](b.md)."),
                         by="homer", message=f"feat(notes): ref {i}")

        _run(find_cli, memory, "zelt")

        out = capsys.readouterr().out
        assert out.index("Zelt geteilt") < out.index("Zelt allein")

    def test_a_search_finds_the_cards_never_the_page_compiled_from_them(self, memory, capsys):
        """A hit is something a reader can link and a person can correct: a
        card. The wiki's page on Lisa says what the note says, and is not one."""
        memory.write("homer/notes/zelt.md", _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT),
                     by="homer", message="feat(notes): zelt")
        memory.compile("lisa/about.md", "---\nview: person\ntitle: Lisa Simpson\nabout: person-lisa\nderived_from:\n  - person-lisa\n"
                       "---\nLisa found the torn tent (Zelt ist kaputt).\n")

        _run(find_cli, memory, "zelt")

        found = capsys.readouterr().out
        assert "capture/Abc123Def4" in found and "Lisa found the torn tent" not in found
        assert len(found.splitlines()) == 1

    def test_a_word_inside_a_longer_one_is_found(self, memory, capsys):
        """German writes its words together: "zelt" has to find "Zeltplatz"."""
        memory.write("homer/notes/platz.md", _note("Platz00000001", "Zeltplatz am See", RECENT),
                     by="homer", message="feat(notes): platz")

        _run(find_cli, memory, "zelt")

        assert "Zeltplatz am See" in capsys.readouterr().out

    def test_since_keeps_only_what_changed_from_that_day_on(self, memory, capsys):
        memory.write("homer/notes/old.md", _note("Old0000000001", "Zelt geflickt", LONG_AGO),
                     by="homer", message="feat(notes): old")
        memory.write("homer/notes/new.md", _note("New0000000001", "Zelt gerissen", RECENT),
                     by="homer", message="feat(notes): new")

        _run(find_cli, memory, "zelt", "--since", (date.today() - timedelta(days=30)).isoformat())

        out = capsys.readouterr().out
        assert "Zelt gerissen" in out and "Zelt geflickt" not in out

    def test_nothing_found_says_so_and_exits_1(self, memory, capsys):
        with pytest.raises(SystemExit) as exit_:
            _run(find_cli, memory, "einhorn")

        assert exit_.value.code == 1
        assert "No card matches" in capsys.readouterr().out


class TestOpeningACard:

    def test_a_card_says_who_changed_it_when_and_how(self, memory, capsys):
        sha = memory.write("homer/notes/zelt.md", _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT),
                           by="marge", message="fix(notes): marge corrected the tent note")

        _run(card_cli, memory, "capture/Abc123Def4")

        out = capsys.readouterr().out
        assert f"by marge @{sha}: fix(notes): marge corrected the tent note" in out

    def test_a_card_shows_the_cards_that_point_to_it(self, memory, capsys):
        memory.write("family/camping/zelt.md",
                     _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT, persons=["Lisa"]),
                     by="homer", message="feat(notes): zelt")

        _run(card_cli, memory, "person/lisa")

        out = capsys.readouterr().out
        assert "linked from: capture/Abc123Def4 (Zelt ist kaputt)" in out

    def test_a_link_relative_to_the_page_reaches_the_card(self, memory, capsys):
        """Pages link each other the way markdown does, relative to where
        they live; such a link is an edge like a site path."""
        memory.write("family/camping/zelt.md",
                     _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT,
                           body="Lisa hat es gemerkt ([Lisa](../people/lisa.md))."),
                     by="homer", message="feat(notes): zelt")

        _run(card_cli, memory, "person/lisa")

        assert "linked from: capture/Abc123Def4 (Zelt ist kaputt)" in capsys.readouterr().out

    def test_a_card_in_a_topic_folder_links_to_its_topic(self, memory, capsys):
        memory.write("family/camping/zelt.md", _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT),
                     by="homer", message="feat(notes): zelt")

        _run(card_cli, memory, "capture/Abc123Def4")

        assert "links to: topic/camping (Camping)" in capsys.readouterr().out

    def test_the_version_moves_when_the_card_changes(self, memory, capsys):
        first = memory.write("homer/notes/zelt.md", _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT),
                             by="homer", message="feat(notes): zelt")
        second = memory.write("homer/notes/zelt.md",
                              _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT, body="Geflickt."),
                              by="marge", message="fix(notes): marge mended the tent")

        _run(card_cli, memory, "capture/Abc123Def4")

        line = capsys.readouterr().out.splitlines()[0]
        assert f"@{second}" in line and f"@{first}" not in line

    def test_several_cards_open_in_one_call(self, memory, capsys):
        """Each model call costs seconds; opening the cards a search found
        one by one cost a call each (8 calls for one question on the rig)."""
        memory.write("homer/notes/zelt.md", _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT),
                     by="homer", message="feat(notes): zelt")

        _run(card_cli, memory, "capture/Abc123Def4", "person/lisa", "topic/camping")

        out = capsys.readouterr().out
        assert "Zelt ist kaputt" in out and "Lisa plays the saxophone" in out
        assert "The family's camping trips" in out

    def test_cards_opened_together_share_one_budget_and_say_how_to_see_the_rest(self, memory,
                                                                               capsys):
        """A tool result is read again on every later call of a turn, so
        several cards together get the text of one; a card cut short says
        how to see more with the commands the agent has."""
        memory.write("homer/notes/long.md",
                     _note("Long000000001", "Langer Bericht", RECENT, body="Zelt. " * 2000),
                     by="homer", message="feat(notes): long")

        _run(card_cli, memory, "capture/Long00000", "person/lisa")
        together = capsys.readouterr().out
        _run(card_cli, memory, "capture/Long00000")
        alone = capsys.readouterr().out

        assert len(together) < 4000 and len(alone) < 7000
        assert "`stack memory card capture/Long000000` alone shows more" in together
        assert alone.count("Zelt.") > 1.5 * together.count("Zelt.")

    def test_a_card_keeps_its_history_however_much_was_filed_after_it(self, memory, capsys):
        """Years of filings later, Lisa's profile still says who wrote it."""
        memory.file_many(2100, by="homer")

        _run(card_cli, memory, "person/lisa")

        assert "by marge" in capsys.readouterr().out

    def test_a_persons_card_is_their_file_and_names_the_page_compiled_about_them(self, memory,
                                                                                 capsys):
        """The page the wiki compiles on Lisa is what a question about her
        reads first; it is reached from her card, as a view, not opened as one."""
        memory.compile("lisa/about.md", "---\nview: person\ntitle: Lisa Simpson\nabout: person-lisa\nderived_from:\n  - person-lisa\n"
                       "---\nLisa, 8, plays the saxophone and wants to be president.\n")

        _run(card_cli, memory, "person/lisa")
        card = capsys.readouterr().out

        assert card.splitlines()[1] == "file: family/people/lisa.md"
        assert "view: lisa/about.md (person, compiled by the wiki)" in card
        with pytest.raises(SystemExit):
            _run(card_cli, memory, "lisa/about.md")

    def test_a_path_outside_the_family_memory_is_no_card(self, memory, tmp_path, capsys):
        """The agent opens pages with `card`; only the memory's cards exist for it."""
        (tmp_path / "secret.txt").write_text("not the family's memory")

        with pytest.raises(SystemExit) as exit_:
            _run(card_cli, memory, "../../secret.txt")

        assert exit_.value.code == 1
        assert "not the family's memory" not in capsys.readouterr().out

    def test_an_unknown_card_says_how_to_find_one(self, memory, capsys):
        with pytest.raises(SystemExit) as exit_:
            _run(card_cli, memory, "capture/nothere")

        assert exit_.value.code == 1
        assert "stack memory find" in capsys.readouterr().out


class TestACardReadsAsItsTypeRendersIt:
    """The agent reads a card as the chat and the card browser show it: the
    markdown of its type's renderer (ADR-016 §4), not the file with the
    links and quotes it carries for the editors."""

    NOTE = ("---\ntype: note\ncard_id: note-7f3a9c21e4\ntitle: Zelt ist kaputt\n"
            f"date: \"{RECENT}\"\npersons:\n  - lisa\n---\n\n# Zelt ist kaputt\n\n"
            "> **About** [Lisa](../../people/lisa.md)\n> **Captured** today · **Kind** note\n\n"
            "> [!summary]\n> The tent tore on the last trip.\n\n## Caption\n\nDas Zelt ist gerissen.\n")

    def test_a_card_shows_its_title_once_its_people_by_name_and_their_words_last(self, memory,
                                                                                capsys):
        memory.write("family/camping/zelt.md", self.NOTE, by="homer", message="feat(notes): zelt")

        _run(card_cli, memory, "note-7f3a9c21e4")
        out = capsys.readouterr().out

        text = out[out.index("# Zelt ist kaputt"):]
        assert text.count("Zelt ist kaputt") == 1
        assert f"{RECENT} · Lisa" in text
        assert "**About**" not in text and "**Captured**" not in text
        assert text.rstrip().endswith("> Das Zelt ist gerissen.")

    def test_with_json_each_card_comes_with_its_rendered_markdown(self, memory, capsys):
        memory.write("family/camping/zelt.md", self.NOTE, by="homer", message="feat(notes): zelt")

        result = _run(card_cli, memory, "note-7f3a9c21e4", "person-lisa")

        assert result["cards"] == ["note-7f3a9c21e4", "person-lisa"]
        note, lisa = result["rendered"]
        assert note.startswith("# Zelt ist kaputt\n\n" + f"{RECENT} · Lisa\n")
        assert lisa == "# Lisa Simpson\n\nLis\n\nLisa plays the saxophone.\n"


class TestFollowingLinks:

    def test_the_cards_around_a_card_come_nearest_first(self, memory, capsys):
        memory.write("family/camping/zelt.md",
                     _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT, persons=["Lisa"]),
                     by="homer", message="feat(notes): zelt")

        _run(links_cli, memory, "person/lisa", "--depth", "2")

        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith("1  linked from  capture/Abc123Def4")
        assert any(line.startswith("2  links to  topic/camping") for line in lines)

    def test_links_reach_only_as_far_as_asked(self, memory, capsys):
        memory.write("family/camping/zelt.md",
                     _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT, persons=["Lisa"]),
                     by="homer", message="feat(notes): zelt")

        _run(links_cli, memory, "person/lisa")

        out = capsys.readouterr().out
        assert "capture/Abc123Def4" in out and "topic/camping" not in out


class TestWhoIsListening:
    """What one person keeps in their own bucket stays theirs: a room with
    others in it reads the family's memory, not one person's (the same rule
    that files a note into a bucket by who is in the room). `--audience`
    names who will see the answer; without it, the admin sees everything."""

    @pytest.fixture
    def private(self, memory):
        memory.write("marge/notes/aquarell.md",
                     _note("Marge000000001", "Barts Aquarell", RECENT,
                           body="Bart malt nass in nass. Siehe [Lisa](../../family/people/lisa.md)."),
                     by="marge", message="feat(notes): marge filed Barts Aquarell")
        memory.compile("lisa/about.md", "---\nview: person\ntitle: Lisa Simpson\nabout: person-lisa\nderived_from:\n  - person-lisa\n"
                       "---\nLisa, 8, the family's saxophonist.\n")
        return memory

    def test_in_a_room_with_others_a_persons_own_note_is_not_found(self, private, capsys):
        with pytest.raises(SystemExit):
            _run(find_cli, private, "aquarell", "--audience", "bart,homer")

        assert "Barts Aquarell" not in capsys.readouterr().out

    def test_nor_opened_by_its_id(self, private, capsys):
        with pytest.raises(SystemExit) as exit_:
            _run(card_cli, private, "capture/Marge00000", "--audience", "bart,homer")

        assert exit_.value.code == 1 and "nass in nass" not in capsys.readouterr().out

    def test_nor_seen_as_a_link_of_a_card_that_is_read(self, private, capsys):
        with pytest.raises(SystemExit):  # Lisa's card has no links left: exit 1
            _run(links_cli, private, "person/lisa", "--audience", "bart")

        assert "Marge00000" not in capsys.readouterr().out

    def test_in_their_own_chat_it_is_theirs_to_read(self, private, capsys):
        _run(find_cli, private, "aquarell", "--audience", "marge")

        assert "Barts Aquarell" in capsys.readouterr().out

    def test_a_person_with_others_still_reads_it_as_one_of_them_only_alone(self, private, capsys):
        with pytest.raises(SystemExit):
            _run(find_cli, private, "aquarell", "--audience", "marge,homer")

        assert "Barts Aquarell" not in capsys.readouterr().out

    def test_the_familys_memory_is_read_by_everyone(self, private, capsys):
        _run(find_cli, private, "camping", "--audience", "bart,homer")

        assert "topic/camping" in capsys.readouterr().out

    def test_the_page_compiled_in_a_persons_bucket_is_left_out_for_others(self, private, capsys):
        """Lisa's compiled page lives in her bucket; others read her card without it."""
        _run(card_cli, private, "person/lisa", "--audience", "bart,homer")
        others = capsys.readouterr().out
        _run(card_cli, private, "person/lisa", "--audience", "lisa")
        hers = capsys.readouterr().out

        assert "Lisa plays the saxophone" in others and "lisa/about.md" not in others
        assert "view: lisa/about.md" in hers

    def test_the_admin_without_an_audience_sees_everything(self, private, capsys):
        _run(find_cli, private, "aquarell")

        assert "Barts Aquarell" in capsys.readouterr().out


class TestNarrowingWhatIsFound:
    """What an agent asks most: about one person, in a stretch of days,
    newest first ("what did Lisa do last week?")."""

    @pytest.fixture
    def days(self, memory):
        memory.write("family/camping/zelt.md",
                     _note("Zelt00000001", "Zelt gekauft", "2026-09-01", persons=["Lisa"]),
                     by="homer", message="feat(notes): zelt")
        memory.write("family/camping/see.md",
                     _note("See000000001", "Am See", "2026-09-10", persons=["Lisa"]),
                     by="marge", message="feat(notes): see")
        memory.write("family/camping/grill.md", _note("Grill0000001", "Grillabend", "2026-09-12"),
                     by="homer", message="feat(notes): grill")
        return memory

    def test_a_person_narrows_it_to_the_cards_about_them(self, days, capsys):
        _run(find_cli, days, "--person", "lisa")

        out = capsys.readouterr().out
        assert "Zelt gekauft" in out and "Am See" in out and "Grillabend" not in out

    def test_a_person_is_found_by_any_of_their_names(self, days, capsys):
        _run(find_cli, days, "--person", "Lisa Simpson")

        assert "Am See" in capsys.readouterr().out

    def test_a_stretch_of_days_keeps_what_falls_in_it(self, days, capsys):
        _run(find_cli, days, "--since", "2026-09-05", "--until", "2026-09-11")

        out = capsys.readouterr().out
        assert "Am See" in out and "Zelt gekauft" not in out and "Grillabend" not in out

    def test_newest_first_orders_by_day(self, days, capsys):
        _run(find_cli, days, "--type", "note", "--sort", "newest")

        lines = capsys.readouterr().out.splitlines()
        assert [ln.split("  ")[3] for ln in lines] == ["Grillabend", "Am See", "Zelt gekauft"]


class TestEveryThingIsOneCard:
    """One card per thing, with an id of its kind: the graph is only right
    when a person, a list or a correspondent is one node, whatever file
    a link points at."""

    def test_a_person_is_one_card_however_much_the_wiki_compiles_about_them(self, memory, capsys):
        """Records link the family's file on a person; the wiki compiles a
        page from it and every record about them. The person is one card."""
        memory.compile("lisa/about.md", "---\nview: person\ntitle: Lisa Simpson\nabout: person-lisa\nderived_from:\n  - person-lisa\n"
                       "---\nLisa plays the saxophone.\n")
        memory.write("family/camping/zelt.md",
                     _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT,
                           body="Siehe [Lisa](../people/lisa.md)."),
                     by="homer", message="feat(notes): zelt")

        _run(card_cli, memory, "person/lisa")

        assert "linked from: capture/Abc123Def4" in capsys.readouterr().out
        _run(find_cli, memory, "lisa", "--type", "person")
        assert capsys.readouterr().out.count("Lisa Simpson") == 1

    def test_a_lists_monthly_archive_belongs_to_its_list(self, memory, capsys):
        memory.write("family/lists/markt.md", "---\ntype: list\ntitle: Wochenmarkt\n---\n- [ ] Kohlrabi\n",
                     by="marge", message="feat(lists): markt")
        memory.write("family/lists/archive/markt/2026-10.md",
                     "---\ntype: list\ntitle: Wochenmarkt, 2026-10\n---\n- [x] Eier\n",
                     by="marge", message="chore(lists): archive markt")

        _run(card_cli, memory, "list/markt/2026-10")

        out = capsys.readouterr().out
        assert out.startswith("list/markt/2026-10  list-archive")
        assert "links to: list/markt (Wochenmarkt)" in out

    def test_a_correspondent_and_a_hand_written_note_have_ids_of_their_kind(self, memory, capsys):
        memory.write("family/correspondents/springfield-elementary.md",
                     "---\ntype: correspondent\ntitle: Springfield Elementary\n"
                     "slug: springfield-elementary\ngenerated: true\n---\n",
                     by="curator", message="chore(wiki): correspondent")
        memory.write("homer/notes/reifen-wechseln.md", "---\ntype: note\ntitle: Reifen wechseln\n---\n",
                     by="homer", message="feat(notes): homer wrote it down")

        _run(find_cli, memory, "springfield reifen")

        out = capsys.readouterr().out
        assert "correspondent/springfield-elementary  correspondent" in out
        assert "note/reifen-wechseln  note" in out

    def test_a_list_belongs_to_the_topics_it_names(self, memory, capsys):
        memory.write("family/lists/zeltliste.md",
                     "---\ntype: list\ntitle: Zeltliste\ntopics:\n  - camping\n---\n- [ ] Heringe\n",
                     by="marge", message="feat(lists): zeltliste")

        _run(card_cli, memory, "list/zeltliste")

        assert "links to: topic/camping (Camping)" in capsys.readouterr().out
class TestWhatFindLeavesOut:

    @pytest.fixture
    def pages(self, memory):
        memory.compile("family/diary/2026/10.md", "---\nview: diary-month\ntitle: October 2026\n---\n"
                       "The family's month: camping, the tent.\n")
        memory.write("family/camping/zelt.md", _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT),
                     by="homer", message="feat(notes): zelt")
        return memory

    def test_a_readme_or_an_index_in_the_memory_repo_is_no_card(self, memory, capsys):
        """They are how Forgejo and the wiki find the way (ADR-017 §6): never
        found, never opened, wherever they are."""
        memory.write("README.md", "# Unser Gedächtnis\nZelt, Grill und Garten.\n",
                     by="marge", message="docs: readme")
        memory.write("family/camping/index.md", "# Camping\nDas Zelt.\n", by="marge", message="docs: index")

        for ref in ("README.md", "family/camping/index.md", "page/README"):
            with pytest.raises(SystemExit):
                _run(card_cli, memory, ref)
        with pytest.raises(SystemExit):
            _run(find_cli, memory, "zelt")

    def test_navigation_pages_are_left_out_of_a_search(self, pages, capsys):
        _run(find_cli, pages, "camping")

        assert "October 2026" not in capsys.readouterr().out

    def test_nor_found_when_asked_for_by_kind(self, pages, capsys):
        """The wiki compiles them to find the way; they are no card of their own."""
        with pytest.raises(SystemExit):
            _run(find_cli, pages, "camping", "--type", "index")

        assert "October 2026" not in capsys.readouterr().out

    def test_without_words_the_newest_come_first(self, pages, capsys):
        memory = pages
        memory.write("family/camping/alt.md", _note("Alt000000001", "Alter Eintrag", LONG_AGO),
                     by="homer", message="feat(notes): alt")

        _run(find_cli, memory)

        lines = capsys.readouterr().out.splitlines()
        assert lines[-1].split("  ")[3] == "Alter Eintrag"
