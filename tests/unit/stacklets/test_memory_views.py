"""Views follow their cards (ADR-016 §3 and §5, ADR-017 §7).

Everything the curator compiles into the brain is a view: a person's page,
a correspondent's, a topic's, the diary's front, year and month pages, the
home page, a folder listing. A view names the cards it was compiled from,
so when a card is pushed the curator compiles again exactly the views that
name it: at once without the model (which cards belong, dates, list items,
links), and with the model once the pushes have settled, so a burst costs
one model call per view.

The family memory here is a real git repository of Simpsons cards, the
brain is projected from it the way the curator mirrors it, and the
compiler runs in this process with the stub model the memory tests use.
The subprocess the curator starts for a compile is the one thing not run.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

import curator  # noqa: E402
import diary_card  # noqa: E402
import wiki  # noqa: E402
from graph import load_graph  # noqa: E402
from test_memory_diary_people import _diary_command  # noqa: E402

ROOM = "!memories:simpsons"


# ── The family memory ─────────────────────────────────────────────────────

def _person(pid: str, name: str) -> str:
    return f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\naccount: {pid}\n---\n"


def _record(kind: str, card_id: str, title: str, day: str, summary: str, *,
            persons: tuple[str, ...] = (), extra: str = "") -> str:
    people = "".join(f"\n  - {p}" for p in persons)
    return (f"---\ntype: {kind}\ncard_id: {card_id}\ntitle: {title}\ndate: \"{day}\"\n"
            f"timestamp: \"{day}T10:00:00Z\"\n" + (f"persons:{people}\n" if persons else "")
            + extra + f"---\n\n# {title}\n\n> [!summary]\n> {summary}\n")


def _diary(entry_id: str, on: date, title: str, body: str, *, persons: list[str],
           tags: list[str]) -> tuple[str, str]:
    """(path, text) of a diary card, as the diary compiler writes it."""
    card = diary_card.Card(
        entry_id=entry_id, on=on, confidence="sent", kind="text", sender="marge",
        at=1_790_000_000_000, event_ids=[f"${entry_id}"],
        resource=f"https://matrix.to/#/{ROOM}/${entry_id}", body=body, title=title,
        persons=persons, tags=tags, summary=f"{title}.", card_id=f"diary-{entry_id}")
    return diary_card.card_path(card, bucket="family"), diary_card.render(card, family_owned=True)


TENT_DAY = date(2026, 10, 4)
TENT, TENT_TEXT = _diary("0a0a0a0a0a", TENT_DAY, "The tent tore at Itchy & Scratchy Land",
                         "Homer sat on the tent.", persons=["Homer"], tags=["camping"])
SKATE, SKATE_TEXT = _diary("0b0b0b0b0b", date(2026, 9, 12), "Bart's new skateboard",
                           "Bart rode it into the garage.", persons=["Bart"], tags=[])

CARDS = {
    "family/people/homer.md": _person("homer", "Homer Simpson"),
    "family/people/marge.md": _person("marge", "Marge Simpson"),
    "family/people/bart.md": _person("bart", "Bart Simpson"),
    "family/camping/topic.md": "---\ntype: topic\ncard_id: topic-camping\ntitle: Camping\n---\n",
    "family/camping/notes/2026/10/tent-poles-aa11.md": _record(
        "note", "note-aa11aa11aa", "Tent poles", "2026-10-01",
        "Buy new tent poles before the next trip.", persons=("Homer",)),
    "family/correspondents/duff-insurance.md": (
        "---\ntype: correspondent\ncard_id: correspondent-duff-insurance\ntitle: Duff Insurance\n---\n"
        "Insures the family car.\n"),
    "family/documents/2026/09/duff-renewal-p12.md": _record(
        "document", "document-bb22bb22bb", "Duff Insurance renewal", "2026-09-15",
        "The car insurance renews in October.", persons=("Marge",),
        extra="correspondent: Duff Insurance\npaperless_id: 12\n"),
    "bart/notes/2026/10/skate-park-cc33.md": _record(
        "note", "note-cc33cc33cc", "Skate park", "2026-10-02",
        "Bart wants to go to the new skate park.", persons=("Bart",)),
    TENT: TENT_TEXT,
    SKATE: SKATE_TEXT,
}


class Model:
    """The stub model: one answer for every page it is asked for, each call recorded."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, role, prompt, **kwargs) -> str:
        self.calls.append(role)
        if role == "writer":  # a diary month's opening paragraph
            return "A month of camping, and a torn tent."
        return json.dumps({"tagline": "A Simpson.", "about": "As the cards say. [1]",
                           "summary": "As the cards say. [1]", "welcome": "Welcome home."})


class Family:
    """The Simpsons' memory repo, the brain projected from it, and the compiler."""

    def __init__(self, root: Path, monkeypatch):
        self.vault, self.brain = root / "vault", root / "brain"
        self.vault.mkdir()
        self.brain.mkdir()
        for name, value in (("MEMORY_VAULT_DIR", self.vault), ("BRAIN_REPO_DIR", self.brain),
                            ("DIARY_STATE_DIR", root / "diary"), ("SHARED_BUCKET", "family"),
                            ("LANGUAGE", "en")):
            monkeypatch.setenv(name, str(value))
        self._git("init", "-q", "-b", "main")
        self.repo = curator.Vault(self.vault)
        self.model = Model()
        self.diary = _diary_command()
        self.head = ""
        for path, text in CARDS.items():
            self.push(path, text, mirror=False)
        self._mirror("", self.head)

    def push(self, path: str, text: str, *, mirror: bool = True) -> tuple[str, str]:
        """Commit `text` at `path` as Marge and mirror it into the brain: (before, after)."""
        file = self.vault / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        self._git("add", path)
        self._git("-c", "user.name=marge", "-c", "user.email=marge@simpsons", "commit", "-qm", path)
        before, self.head = self.head, self._git("rev-parse", "HEAD").strip()
        if mirror:
            self._mirror(before, self.head)
        return before, self.head

    def _mirror(self, before: str, after: str) -> None:
        if before:
            lines = self._git("diff", "--name-status", "-M", f"{before}..{after}").splitlines()
            ops = curator.diff_to_fileops(lines, lambda p: self.repo.frontmatter_at(after, p))
        else:
            ops = [("copy", p, p) for p in CARDS]
        curator.Brain(self.brain, self.vault)._apply(ops)

    async def select(self, since: str, until: str) -> curator.Selection:
        return await curator.views_pushed(self.repo, self.brain, since, until,
                                          shared_bucket="family")

    async def compile(self, selection: curator.Selection, *, fast: bool) -> bool:
        """What `curator.compile_views` runs in a child process, run here."""
        flag, model = (["--fast"], None) if fast else ([], self.model)
        ok = True
        if selection.wiki:
            ok = await wiki.run(model, [*selection.wiki, *flag]) == 0
        if selection.months:
            months = [arg for month in selection.months for arg in ("--month", month)]
            ok = await self.diary.pages(model, [*flag, *months]) == 0 and ok
        return ok

    async def compile_everything(self) -> None:
        """As the nightly sweep does: the diary first, the home page links it."""
        assert await self.diary.pages(self.model, []) == 0
        assert await wiki.run(self.model, []) == 0

    def views(self) -> dict[str, str]:
        """Every view in the brain, by path, as written."""
        return {p.relative_to(self.brain).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted(self.brain.rglob("*.md"))
                if p.read_text(encoding="utf-8").startswith("---\nview:")}

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.vault), *args], check=True,
                              capture_output=True, text=True).stdout


@pytest.fixture
def family(tmp_path, monkeypatch):
    f = Family(tmp_path, monkeypatch)
    asyncio.run(f.compile_everything())
    f.model.calls.clear()
    return f


def _fields(text: str) -> dict:
    return yaml.safe_load(text.split("---\n", 2)[1])


def _edit_tent(family: Family, body: str) -> tuple[str, str]:
    """A hand edit of the tent card, as Marge would push it from Obsidian."""
    return family.push(TENT, TENT_TEXT.replace("Homer sat on the tent.", body))


# ── What a view says about itself ─────────────────────────────────────────

class TestAViewNamesTheCardsItWasCompiledFrom:

    def test_every_compiled_page_is_a_view_and_never_looks_like_a_card(self, family):
        views = family.views()

        assert {_fields(t)["view"] for t in views.values()} == {
            "home", "person", "correspondent", "topic", "folder",
            "diary-front", "diary-year", "diary-month"}
        for path, text in views.items():
            fields = _fields(text)
            assert fields["title"] and fields["derived_from"], path
            assert not {"type", "card_id", "generated"} & fields.keys(), path

    def test_a_view_about_one_thing_names_its_card(self, family):
        views = family.views()

        assert _fields(views["homer/about.md"])["about"] == "person-homer"
        assert _fields(views["family/camping/about.md"])["about"] == "topic-camping"
        assert _fields(views["family/correspondents/duff-insurance.md"])["about"] == \
            "correspondent-duff-insurance"

    def test_a_persons_view_is_derived_from_their_card_and_their_records(self, family):
        derived = _fields(family.views()["homer/about.md"])["derived_from"]

        assert set(derived) == {"person-homer", "note-aa11aa11aa", "diary-0a0a0a0a0a"}

    def test_a_correspondents_view_keeps_the_text_of_its_card(self, family):
        """The view shares its path with the card it is about (ADR-017 §9)."""
        view = family.views()["family/correspondents/duff-insurance.md"]

        assert "Insures the family car." in view
        assert "[Duff Insurance renewal](/family/documents/2026/09/duff-renewal-p12.md)" in view
        assert _fields(view)["derived_from"] == ["correspondent-duff-insurance",
                                                 "document-bb22bb22bb"]

    def test_a_diary_month_is_derived_from_its_entries_only(self, family):
        views = family.views()

        assert _fields(views["family/diary/2026/10.md"])["derived_from"] == ["diary-0a0a0a0a0a"]
        assert _fields(views["family/diary/2026/09.md"])["derived_from"] == ["diary-0b0b0b0b0b"]


# ── A pushed card compiles again exactly the views that name it ───────────

class TestAPushedCard:

    def test_compiles_again_exactly_the_views_that_name_it(self, family):
        """The tent card names Homer, is tagged with the camping topic, and
        is an entry of October: those views follow it, with the diary's
        year and front page and the home page, which are derived from every
        entry. Bart's, Marge's, September's and Duff's are left alone."""
        before = family.views()

        selection = asyncio.run(family.select(*_edit_tent(family, "Homer fell on the tent.")))
        assert asyncio.run(family.compile(selection, fast=True))

        after = family.views()
        assert {p for p in after if after[p] != before.get(p)} == {
            "homer/about.md", "family/camping/about.md", "index.md",
            "family/diary/about.md", "family/diary/2026/about.md", "family/diary/2026/10.md"}

    def test_at_once_the_views_show_the_card_without_asking_the_model(self, family):
        selection = asyncio.run(family.select(*_edit_tent(family, "Homer fell on the tent.")))

        asyncio.run(family.compile(selection, fast=True))

        assert family.model.calls == []
        october = family.views()["family/diary/2026/10.md"]
        assert "Homer fell on the tent." in october
        # The model's paragraph from before stays until the model's turn.
        assert "A month of camping, and a torn tent." in october
        assert "As the cards say." in family.views()["homer/about.md"]

    def test_a_new_card_reaches_the_views_it_would_be_in(self, family):
        """A card no view names yet: the person it names gets it at once."""
        path = "bart/notes/2026/10/slingshot-dd44.md"
        before, after = family.push(path, _record(
            "note", "note-dd44dd44dd", "Slingshot", "2026-10-06",
            "Bart's slingshot was taken away.", persons=("Bart",)))

        asyncio.run(family.compile(asyncio.run(family.select(before, after)), fast=True))

        bart = family.views()["bart/about.md"]
        assert "note-dd44dd44dd" in _fields(bart)["derived_from"]
        assert "Slingshot" in bart

    def test_a_burst_of_three_pushes_costs_one_model_call_per_view(self, family):
        recompile = curator.Recompile(family.select, family.compile, curator.Debounce(180))
        last = family.head

        async def burst() -> None:
            for second, body in enumerate(("Homer fell.", "Homer fell twice.", "Homer gave up.")):
                before, head = _edit_tent(family, body)
                await recompile.pushed(before, head)
                assert await recompile.settled(last, head, now=float(second)) is None
            assert family.model.calls == []
            assert await recompile.settled(last, family.head, now=2.0 + 180) is True

        asyncio.run(burst())

        # Homer's page, the camping topic's, the home page; October's opening.
        assert sorted(family.model.calls) == ["overview", "overview", "overview", "writer"]
        assert "Homer gave up." in family.views()["family/diary/2026/10.md"]

    def test_nothing_pushed_compiles_nothing_and_a_full_compile_changes_nothing(self, family):
        """The resting state, and the nightly sweep over current views."""
        recompile = curator.Recompile(family.select, family.compile, curator.Debounce(180))
        before = family.views()

        assert asyncio.run(recompile.settled(family.head, family.head, now=0.0, forced=True))
        asyncio.run(family.compile_everything())

        assert family.views() == before


# ── What a reader of the card sees ────────────────────────────────────────

class TestTheCardSaysWhenAViewIsBehind:

    def _views_of_tent(self, family):
        return {v.path: v.behind for v in load_graph(family.vault, views=family.brain)
                .card("diary-0a0a0a0a0a").views}

    def test_a_card_comes_with_every_view_that_names_it(self, family):
        assert set(self._views_of_tent(family)) == {
            "homer/about.md", "family/camping/about.md", "index.md",
            "family/diary/about.md", "family/diary/2026/about.md", "family/diary/2026/10.md"}

    def test_a_view_compiled_before_the_card_changed_is_behind_it_until_compiled_again(self, family):
        selection = asyncio.run(family.select(*_edit_tent(family, "Homer fell on the tent.")))

        assert all(self._views_of_tent(family).values())
        asyncio.run(family.compile(selection, fast=True))
        assert not any(self._views_of_tent(family).values())
