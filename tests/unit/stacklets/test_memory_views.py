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
import posixpath
import re
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

import pytest
import yaml
from werkzeug import Response

_REPO_ROOT = Path(__file__).resolve().parents[3]
# Core's link resolver, last so none of its modules shadows the memory's.
sys.path.append(str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))

from stacklets.memory.bot import curator  # noqa: E402
from stacklets.memory.bot import diary_card  # noqa: E402
from stacklets.memory.bot.cli import wiki  # noqa: E402
from stacklets.memory.cards_api import CardsAPI, serve  # noqa: E402
from stacklets.memory.graph import load_graph  # noqa: E402
from stacklets.memory.lib import CURATOR_PUSH_URL, ensure_push_webhook  # noqa: E402
from resolver import card_link_answer  # noqa: E402
from stack.cards.lookup import find_by_card_id  # noqa: E402
from stack.forgejo import ForgejoClient  # noqa: E402
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
    # As the archivist declares it: the title, and nothing written below it yet.
    "family/correspondents/duff-insurance.md": (
        "---\ntype: correspondent\ncard_id: correspondent-duff-insurance\ntitle: Duff Insurance\n---\n"
        "\n# Duff Insurance\n"),
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
    """The stub model: one answer for every page it is asked for, each call recorded.

    `wording` is what it writes on a page and `opening` a diary month's
    paragraph, so a test can tell which compile wrote a view. With
    `answering` cleared it takes a while: it answers once the test sets it,
    as a real model answers minutes later.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.wording = "As the cards say."
        self.opening = "A month of camping, and a torn tent."
        self.answering = asyncio.Event()
        self.answering.set()

    async def complete(self, role, prompt, **kwargs) -> str:
        self.calls.append(role)
        await self.answering.wait()
        if role == "writer":  # a diary month's opening paragraph
            return self.opening
        return json.dumps({"tagline": "A Simpson.", "about": f"{self.wording} [1]",
                           "summary": f"{self.wording} [1]", "welcome": "Welcome home."})


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


def _above_documents(view: str) -> str:
    """The text a correspondent's view shows above its list of documents."""
    body = view.split("---\n", 2)[2].split("## Documents", 1)[0]
    return re.sub(r"<!-- (begin|end): generated -->", "", body).strip()


def _resolved(page: str, link: str) -> str:
    """The vault file a relative `link` on `page` points at."""
    return posixpath.normpath(posixpath.join(posixpath.dirname(page), link))


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
        assert _fields(views[DUFF_VIEW])["about"] == "correspondent-duff-insurance"

    def test_a_persons_view_is_derived_from_their_card_and_their_records(self, family):
        derived = _fields(family.views()["homer/about.md"])["derived_from"]

        assert set(derived) == {"person-homer", "note-aa11aa11aa", "diary-0a0a0a0a0a"}

    def test_a_diary_month_is_derived_from_its_entries_only(self, family):
        views = family.views()

        assert _fields(views["family/diary/2026/10.md"])["derived_from"] == ["diary-0a0a0a0a0a"]
        assert _fields(views["family/diary/2026/09.md"])["derived_from"] == ["diary-0b0b0b0b0b"]


# ── A correspondent's view and its card ───────────────────────────────────

DUFF_CARD = "family/correspondents/duff-insurance.md"
DUFF_VIEW = "family/correspondents/duff-insurance/about.md"
RENEWAL = "family/documents/2026/09/duff-renewal-p12.md"


class TestACorrespondentsViewHasAPathOfItsOwn:
    """Like a person's and a topic's, a correspondent's view is `about.md` in
    a folder of its own, and its card stays a card in the brain (ADR-017 §9).
    A view written over the card's path took the card's `card_id` away, and
    with it every `/go/` link to the correspondent."""

    def test_lists_the_documents_from_the_correspondent(self, family):
        view = family.views()[DUFF_VIEW]

        assert _fields(view)["view"] == "correspondent"
        assert "[Duff Insurance renewal](/family/documents/2026/09/duff-renewal-p12.md)" in view
        assert _fields(view)["derived_from"] == ["correspondent-duff-insurance",
                                                 "document-bb22bb22bb"]

    def test_leaves_the_card_in_the_brain_as_the_memory_repo_holds_it(self, family):
        assert (family.brain / DUFF_CARD).read_bytes() == (family.vault / DUFF_CARD).read_bytes()

    def test_a_card_that_is_only_its_title_puts_nothing_above_the_documents(self, family):
        assert _above_documents(family.views()[DUFF_VIEW]) == ""

    def test_a_correspondents_view_keeps_the_text_of_its_card(self, family):
        """What a person wrote on the card below its title shows above the
        documents, so the view moving off the card's path loses none of it."""
        written = CARDS[DUFF_CARD] + "\nInsures the family car. The policy renews in October.\n"
        selection = asyncio.run(family.select(*family.push(DUFF_CARD, written)))
        assert asyncio.run(family.compile(selection, fast=True))

        assert _above_documents(family.views()[DUFF_VIEW]) == \
            "Insures the family car. The policy renews in October."
        assert (family.brain / DUFF_CARD).read_bytes() == (family.vault / DUFF_CARD).read_bytes()

    def test_a_link_in_the_cards_text_still_reaches_what_it_pointed_at(self, family):
        """A relative link on the card is relative to the card's folder; the
        view is a folder further down."""
        written = CARDS[DUFF_CARD] + "\nHomer pays it, ask [Homer](../people/homer.md).\n"
        assert _resolved(DUFF_CARD, "../people/homer.md") == "family/people/homer.md"
        selection = asyncio.run(family.select(*family.push(DUFF_CARD, written)))
        assert asyncio.run(family.compile(selection, fast=True))

        link = re.search(r"\[Homer\]\(([^)]+)\)", family.views()[DUFF_VIEW])
        assert link and _resolved(DUFF_VIEW, link[1]) == "family/people/homer.md"

    def test_a_brain_compiled_before_gets_the_card_back(self, family):
        """The view an older compile wrote over the card's path goes back to
        being the card, and the view is at its own path: no file is left that
        reads as both."""
        (family.brain / DUFF_CARD).write_text(family.views()[DUFF_VIEW], encoding="utf-8")
        (family.brain / DUFF_VIEW).unlink()

        selection = curator.Selection(wiki=["--correspondent", "Duff Insurance"])
        assert asyncio.run(family.compile(selection, fast=True))

        assert (family.brain / DUFF_CARD).read_bytes() == (family.vault / DUFF_CARD).read_bytes()
        about_duff = [path for path, text in family.views().items()
                      if _fields(text).get("about") == "correspondent-duff-insurance"]
        assert about_duff == [DUFF_VIEW]

    def test_a_document_that_names_another_correspondent_leaves_the_page_of_the_first(
            self, family):
        """The view that listed the document is compiled again from its own
        path, so the document leaves it."""
        claim = "family/documents/2026/10/duff-claim-p13.md"
        pushes = [
            (claim, _record("document", "document-dd44dd44dd", "Duff Insurance claim",
                            "2026-10-03", "The claim for the car is filed.",
                            extra="correspondent: Duff Insurance\npaperless_id: 13\n")),
            (RENEWAL, CARDS[RENEWAL].replace("correspondent: Duff Insurance",
                                             "correspondent: Springfield Mutual")),
        ]
        for path, text in pushes:
            selection = asyncio.run(family.select(*family.push(path, text)))
            assert asyncio.run(family.compile(selection, fast=True))

        duff = family.views()[DUFF_VIEW]
        assert "Duff Insurance claim" in duff
        assert "Duff Insurance renewal" not in duff


# ── A link to a card ──────────────────────────────────────────────────────

WIKI = "http://wiki.example"


class TestALinkToACardLandsOnThePageAboutIt:
    """`/go/<card_id>` as core's tools server answers it, over the brain the
    wiki serves. A person, a topic and a correspondent are read through the
    view compiled from their card, so the link lands on that view."""

    @pytest.mark.parametrize("card_id", ["person-homer", "topic-camping",
                                         "correspondent-duff-insurance"])
    def test_lands_on_the_view_about_the_card(self, family, card_id):
        answer = card_link_answer(
            card_id, wiki_base=WIKI,
            find=lambda cid: find_by_card_id(cid, root=family.brain),
            removal=lambda cid: None,
        )

        assert answer is not None and answer[0] == 302, answer
        page = family.brain / f"{answer[1].removeprefix(WIKI + '/')}.md"
        assert _fields(page.read_text(encoding="utf-8")).get("about") == card_id


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
                await recompile.settle(last, head, now=float(second))
                assert not recompile.busy
            assert family.model.calls == []
            await recompile.settle(last, family.head, now=2.0 + 180)
            assert await recompile.wait(now=2.0 + 180) == (family.head, True)

        asyncio.run(burst())

        # Homer's page, the camping topic's, the home page; October's opening.
        assert sorted(family.model.calls) == ["overview", "overview", "overview", "writer"]
        assert "Homer gave up." in family.views()["family/diary/2026/10.md"]

    def test_nothing_pushed_compiles_nothing_and_a_full_compile_changes_nothing(self, family):
        """The resting state, and the nightly sweep over current views."""
        recompile = curator.Recompile(family.select, family.compile, curator.Debounce(180))
        before = family.views()

        async def nothing() -> tuple[str, bool] | None:
            await recompile.settle(family.head, family.head, now=0.0, forced=True)
            return await recompile.wait(now=0.0)

        assert asyncio.run(nothing()) == (family.head, True)
        asyncio.run(family.compile_everything())

        assert family.views() == before
        assert family.model.calls.count("writer") == 0

    def test_a_push_during_a_model_compile_gets_its_facts_at_once_and_the_models_text_after(
            self, family):
        """The model's turn takes minutes on a real model. A push meanwhile is
        compiled at once all the same; when the model answers, its text is
        put over the view as it is then, and the view that changed meanwhile
        is compiled with the model once more after the next quiet window."""
        recompile = curator.Recompile(family.select, family.compile, curator.Debounce(0))
        model = family.model
        model.wording, model.opening = "Homer and the tent, retold.", "The month of the tent."
        model.answering.clear()
        october = "family/diary/2026/10.md"

        async def scenario() -> None:
            last = family.head
            first = _edit_tent(family, "Homer fell on the tent.")
            await recompile.pushed(*first)
            await recompile.settle(last, first[1], now=0.0, forced=True)
            await asyncio.sleep(0)  # the model is asked, and takes its time
            assert recompile.busy and model.calls

            loop = asyncio.get_running_loop()
            started = loop.time()
            await recompile.pushed(*_edit_tent(family, "Homer patched the tent."))
            assert loop.time() - started < 5 and recompile.busy
            assert "Homer patched the tent." in family.views()[october]

            model.answering.set()
            assert await recompile.wait(now=1.0) == (first[1], True)

            views = family.views()
            assert "Homer patched the tent." in views[october]
            assert "The month of the tent." in views[october]
            assert "Homer and the tent, retold." in views["homer/about.md"]
            behind = [v.path for v in load_graph(family.vault, views=family.brain)
                      .card("diary-0a0a0a0a0a").views if v.behind]
            assert behind == []

            asked = len(model.calls)
            await recompile.settle(first[1], family.head, now=2.0)  # the quiet window opens
            await recompile.settle(first[1], family.head, now=3.0)  # and has passed
            assert await recompile.wait(now=3.0) == (family.head, True)
            assert len(model.calls) > asked

        asyncio.run(scenario())


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


# ── Learning of a push ────────────────────────────────────────────────────

class TestLearningOfAPush:

    def test_forgejos_notice_wakes_the_curator_at_once_without_the_models_turn(self, tmp_path):
        server = serve(CardsAPI(vault=tmp_path), host="127.0.0.1", port=0,
                       on_push=lambda: curator.request_push(tmp_path))
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/push",
                data=b'{"ref": "refs/heads/main"}', method="POST")
            with urllib.request.urlopen(request) as r:
                assert r.status == 204
        finally:
            server.shutdown()

        forced, waited = asyncio.run(_timed(curator.sleep_until_tick(30, tmp_path)))

        assert forced is False and waited < 1.0

    def test_a_push_whose_notice_never_came_is_found_by_the_poll(self, tmp_path):
        """No notice: the tick ends with the poll interval, and the cycle
        reads what moved from git as it always did."""
        forced, waited = asyncio.run(_timed(curator.sleep_until_tick(0.2, tmp_path)))

        assert forced is False and waited >= 0.2

    def test_a_model_compile_that_ended_wakes_the_curator_to_commit_it(self, tmp_path):
        """Its views reach the wiki with the commit after it, not a poll later."""
        forced, waited = asyncio.run(_timed(curator.sleep_until_tick(30, tmp_path,
                                                                     wake=lambda: True)))

        assert forced is False and waited < 1.0

    def test_the_memory_repo_is_set_up_to_notify_the_curator_once(self, httpserver):
        hooks: list[dict] = []
        route = "/api/v1/repos/family/memory/hooks"
        httpserver.expect_request(route, method="GET").respond_with_handler(
            lambda request: Response(json.dumps(hooks), content_type="application/json"))

        def create(request):
            hooks.append(json.loads(request.data))
            return Response(json.dumps(hooks[-1]), status=201, content_type="application/json")
        httpserver.expect_request(route, method="POST").respond_with_handler(create)
        forgejo = ForgejoClient(url=httpserver.url_for(""), admin_user="admin",
                                admin_password="duff")

        assert ensure_push_webhook(forgejo) is True
        assert ensure_push_webhook(forgejo) is False
        [hook] = hooks
        assert hook["config"]["url"] == CURATOR_PUSH_URL and hook["events"] == ["push"]


async def _timed(waiting) -> tuple[bool, float]:
    """(what the tick returned, how many seconds it took)."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    forced = await waiting
    return forced, loop.time() - start
