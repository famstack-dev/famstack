"""Whether a person's edit of a note's clean version survives a rebuild, asked of a real model.

A note card (ADR-018) shows a clean version of the note's words, and
keeps the words and the chat corrections under `## Sources`. A refile
(`stack memory reprocess`) rebuilds a note somebody corrected or edited
as a card conversation (ADR-018 §7, `card_conversation`): the clean
version before the first event, then each correction and each person's
edit of the clean version in time order, read from the card's history.

Each case is a note, the clean version the model wrote for it, and a
history in time order: chat corrections (a reply, a source of the card)
and a person's edits of the clean version. The history is played into a
git repository per case as it would happen: the card and its sidecar
committed when filed, a correction appending its source (and, when the
card was refiled then, the clean version that refile wrote), an edit a
commit of the person's own. What the model is given is read back by the
shipped code: the curator's `CardStore.history` and `history_of` for the
starting point and the edits, the sidecar for the corrections, and
`Classifier.rebuild_note` for the call.

Deterministic checks on each answer, the ones `correction_fold.py` makes
(`correction_fold.failures`: values in the right item or sentence and only
once, old values gone, item count, and the pipeline's check
`clean_words.rebuild_problems`), plus the person's exact wording where
they reworded something, and words of the note nothing changed.
"verbatim" counts the answers that keep every line a person's edit wrote,
character for character.

The format was chosen on the probe branch (`test/edit-replay-probe`):
24 of 24 against 15 to 21 for the same events in one prompt or as raw
diffs, and 2 of 8 for applying the diffs with `git apply`.

Opt-in and report-only, like the rest of `tests/e2e/eval/`: not a
pytest module, nothing collects it.

    AI_DEFAULT_MODEL=<model> EVAL_AI_URL=<base url> EVAL_AI_KEY=<key> \\
        uv run --extra test python tests/e2e/eval/edit_replay.py [--runs 3] [-k trip] [--show]

Cost: one call per case and run, 8 cases x 3 runs = 24 calls, about 4
seconds each on a 35B-A3B model.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[3]
for _path in (_ROOT / "lib", Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from stack.stacklet_code import import_stacklets  # noqa: E402

import_stacklets([_ROOT / "stacklets"])

import correction_fold  # noqa: E402
from correction_fold import Endpoint, Fold, mentions  # noqa: E402
from stacklets.memory.cards import CardStore  # noqa: E402
from stacklets.memory.note_card import card_sources  # noqa: E402
from stack.cards.events import Correction, in_order, name_of, text_of  # noqa: E402
from stack.cards.history import history_of  # noqa: E402
from stack.cards.sources import ORIGINAL, REPLY, Source, render_sources, sidecar_text  # noqa: E402

# ── Cases ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Said:
    """A correction in the chat. `refiled` is the clean version the
    refile it caused wrote, when the card was refiled before the next
    event; None when the rebuild under test is the first to read it."""

    person: str
    words: str
    refiled: str | None = None


@dataclass(frozen=True)
class Edited:
    """A person's edit of the clean version: `old` replaced by `new` in
    the card as it stood. An empty `new` removes `old`."""

    person: str
    old: str
    new: str


@dataclass(frozen=True)
class Case:
    name: str
    writer: str
    title: str
    words: str
    clean: str  # the clean version the model wrote from the words
    events: tuple[Said | Edited, ...]
    folds: tuple[Fold, ...] = ()
    gone: tuple[str, ...] = ()
    items: int | None = None
    wording: tuple[str, ...] = ()  # a person's own wording, kept as they wrote it
    kept: tuple[str, ...] = ()  # words of the note nothing changed
    language: str = "en"


_TRIP_WORDS = (
    "for the trip:\nhomer needs to call the hotel about the late check-in\n"
    "bart forgot his swimsuit again, buy one at the mall before saturday\n"
    "lisa wants the museum visit on sunday, not saturday\nbudget max 200")
_TRIP_CLEAN = (
    "For the trip:\n\n"
    "- Homer needs to call the hotel about the late check-in.\n"
    "- Bart forgot his swimsuit again; buy one at the mall before Saturday.\n"
    "- Lisa wants the museum visit on Sunday, not Saturday.\n"
    "- Budget max 200.")
_TRIP_KEPT = ("hotel", "late check-in", "swimsuit", "mall")

CASES = (
    Case(
        "a changed value", "marge", "Trip", _TRIP_WORDS, _TRIP_CLEAN,
        (Edited("marge", "- Budget max 200.", "- Budget max 250."),),
        folds=(Fold("250", "budget"),), gone=("200",), items=4,
        kept=(*_TRIP_KEPT, "museum visit"),
    ),
    Case(
        "a reworded item", "marge", "Trip", _TRIP_WORDS, _TRIP_CLEAN,
        (Edited("marge", "- Lisa wants the museum visit on Sunday, not Saturday.",
                "- Lisa wants the science museum on Sunday, not Saturday."),),
        folds=(Fold("science museum", "Sunday"),), gone=("museum visit",), items=4,
        wording=("Lisa wants the science museum on Sunday",), kept=(*_TRIP_KEPT, "200"),
    ),
    Case(
        "an added item", "homer", "Barbecue",
        "groceries for sunday bbq:\nburgers\nbuns\ncorn\n2 packs of duff",
        "Groceries for the Sunday barbecue:\n\n- Burgers\n- Buns\n- Corn\n- 2 packs of Duff",
        (Edited("marge", "- 2 packs of Duff", "- 2 packs of Duff\n- Ketchup and mustard"),),
        folds=(Fold("ketchup"),), items=5,
        wording=("Ketchup and mustard",), kept=("burgers", "buns", "corn", "Duff"),
    ),
    Case(
        "a removed item", "marge", "Lisa's birthday",
        "lisas bday party sat:\ncake from the bakery\n12 kids invited\nballoons\n"
        "ask patty if she can help",
        "Lisa's birthday party on Saturday:\n\n- Cake from the bakery\n- 12 kids invited\n"
        "- Balloons\n- Ask Patty if she can help.",
        (Edited("marge", "\n- Ask Patty if she can help.", ""),),
        gone=("Patty",), items=3, kept=("cake", "bakery", "12", "balloons"),
    ),
    Case(
        "a correction, then an edit of another point", "homer", "Camping",
        "camping trip next weekend:\nflanders lends us his tent\nwe leave fri at 4\n"
        "bring 3 sleeping bags\nbart packs the flashlights",
        "Camping trip next weekend:\n\n- Flanders lends us his tent.\n"
        "- We leave on Friday at 4.\n- Bring 3 sleeping bags.\n- Bart packs the flashlights.",
        (Said("homer", "we leave at 5, not 4",
              refiled="Camping trip next weekend:\n\n- Flanders lends us his tent.\n"
                      "- We leave on Friday at 5.\n- Bring 3 sleeping bags.\n"
                      "- Bart packs the flashlights."),
         Edited("marge", "- Bring 3 sleeping bags.", "- Bring 4 sleeping bags.")),
        folds=(Fold("5", "leave"), Fold("4", "sleeping")), gone=("3",), items=4,
        kept=("Flanders", "tent", "flashlights"),
    ),
    Case(
        "a correction, then an edit of the same point", "marge", "School week",
        "school stuff this week:\nlisa sax recital tue 6pm\nbart's permission slip due wed\n"
        "bake 24 cupcakes for fri",
        "School stuff this week:\n\n- Lisa's sax recital on Tuesday at 6pm.\n"
        "- Bart's permission slip is due on Wednesday.\n- Bake 24 cupcakes for Friday.",
        (Said("marge", "the recital is at 7, not 6",
              refiled="School stuff this week:\n\n- Lisa's sax recital on Tuesday at 7pm.\n"
                      "- Bart's permission slip is due on Wednesday.\n"
                      "- Bake 24 cupcakes for Friday."),
         Edited("marge", "- Lisa's sax recital on Tuesday at 7pm.",
                "- Lisa's sax recital on Tuesday at 8pm.")),
        folds=(Fold("8", "recital"),), gone=("6", "7"), items=3,
        kept=("permission slip", "24", "cupcakes"),
    ),
    Case(
        "an edit, then a correction of the same point", "marge", "Saturday dinner",
        "saturday:\ndinner at patty and selmas at 7\nbring dessert\nmaggie stays with grandpa",
        "Saturday:\n\n- Dinner at Patty and Selma's at 7.\n- Bring dessert.\n"
        "- Maggie stays with Grandpa.",
        (Edited("marge", "- Dinner at Patty and Selma's at 7.",
                "- Dinner at Patty and Selma's at 6."),
         Said("homer", "dinner is at 8 now")),
        folds=(Fold("8", "dinner"),), gone=("7", "6"), items=3,
        kept=("Patty", "Selma", "dessert", "Maggie", "Grandpa"),
    ),
    # Said into a phone and transcribed in German, the household's language
    # here; the transcript misheard the name. The prompts stay English.
    Case(
        "prose with a misread name, in German", "marge", "Klempner",
        "heute hat herr flenders angerufen, er kommt montag früh wegen der spüle in der "
        "küche, kostet ca 120 euro und wir müssen vorher den schrank darunter ausräumen",
        "Heute hat Herr Flenders angerufen. Er kommt am Montag früh wegen der Spüle in der "
        "Küche. Das kostet ca. 120 Euro, und wir müssen vorher den Schrank darunter ausräumen.",
        (Edited("marge", "Herr Flenders", "Herr Flanders"),),
        folds=(Fold("Flanders", "angerufen"),), gone=("Flenders",),
        wording=("Herr Flanders",), kept=("Montag", "Spüle", "120", "Schrank"),
        language="de",
    ),
)


# ── The history, as git records it ───────────────────────────────────────


@dataclass(frozen=True)
class Asked:
    """What a rebuild of the case gives the model, read back by the shipped code."""

    writer: str
    prior: str
    events: tuple  # stack.cards.events.Correction | TextEdit, in time order
    edited: tuple[str, ...]  # every line a person's edit wrote

    @property
    def messages(self) -> list[str]:
        return [e.message() for e in self.events]


def _at(n: int) -> str:
    return f"2026-10-20T{8 + n:02d}:00:00+00:00"


def _git(repo: Path, *args: str, author: str = "", at: str = "") -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    if author:
        env |= {"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@simpsons.test",
                "GIT_COMMITTER_NAME": "archivist-bot", "GIT_COMMITTER_EMAIL": "archivist-bot@stack.test",
                "GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at}
    return subprocess.run(["git", "-C", str(repo), *args], env=env, check=True,
                          capture_output=True, text=True).stdout


def _card(case: Case, clean: str, sources: list[Source]) -> str:
    return (f"---\ntitle: {case.title}\ntype: note\ncard_id: note-0000000001\n---\n\n"
            f"# {case.title}\n\n{clean}\n\n{render_sources(sources)}\n")


def asked(case: Case, workdir: Path) -> Asked:
    """Play the case's history into a git repository and read it back as a rebuild does.

    Commits, each at its event's time: the card as filed (`capture:`), a
    correction appending its source (`reply:`, by its writer), an edit of
    the clean version (`edit:`, by the person). The rebuild's starting
    point and the edits come from `stack.cards.history`, the corrections from
    the card's sources, as `stack memory reprocess` reads them.
    """
    repo = workdir / case.name.replace(" ", "-").replace(",", "")
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    path = "note.md"
    sources = [Source(ORIGINAL, case.writer, _at(0), "chat", "text",
                      "$original", quote=case.words)]
    clean = case.clean

    def commit(subject: str, author: str, at: str) -> None:
        (repo / path).write_text(_card(case, clean, sources))
        (repo / "note.json").write_text(sidecar_text(
            card_id="note-0000000001", filed_by=case.writer, sources=sources,
            made={"model": "eval"}))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "--no-verify", "-m", subject, author=author, at=at)

    commit(f"capture: {case.title}", case.writer, _at(0))
    edited: list[str] = []
    for n, event in enumerate(case.events, 1):
        if isinstance(event, Said):
            sources.append(Source(REPLY, event.person, _at(n), "chat", "text",
                                  f"$reply{n}", quote=event.words))
            clean = event.refiled or clean
            commit(f"reply: {case.title}", event.person, _at(n))
        else:
            assert clean.count(event.old) == 1, (case.name, event.old)
            clean = clean.replace(event.old, event.new)
            edited += [ln for ln in event.new.splitlines() if ln.strip()]
            commit(f"edit: {case.title}", event.person, _at(n))

    store = CardStore(repo, remote="origin", server_name="simpson")
    history = history_of(store.history(path, "note-0000000001"), read_sources=card_sources)
    said = [Correction(s.person, s.time, s.words) for s in sources if s.role == REPLY]
    return Asked(name_of(case.writer), history.prior, tuple(in_order([*said, *history.edits])),
                 tuple(edited))


# ── Checks (pure) ────────────────────────────────────────────────────────


def _flat(text: str) -> str:
    return " ".join(text.split()).casefold()


def failures(case: Case, a: Asked, clean: str) -> list[str]:
    """Why `clean` lost a person's edit, a later event, or the note; [] when it did not."""
    seen = SimpleNamespace(words=case.words, said=text_of(a.events), folds=case.folds,
                           gone=case.gone, items=case.items)
    found = correction_fold.failures(seen, clean)  # pyright: ignore[reportArgumentType]
    found += [f"reworded {w!r}" for w in case.wording if _flat(w) not in _flat(clean)]
    found += [f"lost {w!r}" for w in case.kept if not mentions(clean, w)]
    return found


def verbatim(a: Asked, clean: str) -> bool:
    """Whether every line a person's edit wrote stands in `clean` as written."""
    lines = {ln.strip() for ln in clean.splitlines()}
    return all(e.strip() in lines or e.strip() in clean for e in a.edited)


# ── Running ──────────────────────────────────────────────────────────────


@dataclass
class Result:
    case: Case
    cleans: list[str] = field(default_factory=list)
    reasons: list[list[str]] = field(default_factory=list)
    verbatim: int = 0

    @property
    def passed(self) -> int:
        return sum(1 for r in self.reasons if not r)


async def measure(endpoint: Endpoint, cases, histories, runs: int) -> list[Result]:
    import stack.ai.models as models

    models._DEFAULT_MODEL, models._MODELS = endpoint.model, {}
    results = []
    for case in cases:
        a = histories[case.name]
        result = Result(case)
        for _ in range(runs):
            try:
                answer = await endpoint.classifier(case.language).rebuild_note(
                    writer=a.writer, words=case.words, prior=a.prior, events=a.messages)
                clean = str(answer.get("clean") or "")
            except Exception as e:  # noqa: BLE001 -- a failed call is a failed run, not a crash
                clean, reasons = "", [f"call failed: {e}"]
            else:
                reasons = failures(case, a, clean)
            result.cleans.append(clean)
            result.reasons.append(reasons)
            result.verbatim += verbatim(a, clean) if clean else 0
        results.append(result)
    return results


def table(results: list[Result], runs: int) -> str:
    width = max(len(r.case.name) for r in results)
    lines = [f"{'case':<{width}}  pass  verbatim  first failure"]
    for r in results:
        failed = next((x for x in r.reasons if x), [])
        lines.append(f"{r.case.name:<{width}}  {r.passed}/{runs}   {r.verbatim}/{runs}       "
                     f"{'; '.join(failed)}")
    total = sum(r.passed for r in results)
    lines.append(f"{'total':<{width}}  {total}/{runs * len(results)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("-k", default="", help="only cases whose name contains this")
    parser.add_argument("--show", action="store_true", help="print every clean version")
    parser.add_argument("--inputs", action="store_true",
                        help="print what the model is given, ask nothing")
    args = parser.parse_args()

    cases = [c for c in CASES if args.k.casefold() in c.name.casefold()]
    with tempfile.TemporaryDirectory(prefix="edit-replay-") as tmp:
        histories = {c.name: asked(c, Path(tmp)) for c in cases}
    if args.inputs:
        for case in cases:
            a = histories[case.name]
            print(f"== {case.name}\nprior:\n{a.prior}\n" + "\n".join(a.messages) + "\n")
        return 0

    model = os.environ.get("AI_DEFAULT_MODEL", "").strip()
    if not model:
        print("AI_DEFAULT_MODEL is not set: name the model to measure.", file=sys.stderr)
        return 2
    endpoint = Endpoint(os.environ.get("EVAL_AI_URL", "http://localhost:42060/v1"),
                        os.environ.get("EVAL_AI_KEY", ""), model)
    results = asyncio.run(measure(endpoint, cases, histories, args.runs))
    if args.show:
        for r in results:
            for n, (clean, reasons) in enumerate(zip(r.cleans, r.reasons), 1):
                print(f"-- {r.case.name}, run {n}: {'; '.join(reasons) or 'pass'}\n{clean}\n")
    print(table(results, args.runs))
    print(f"\n{model}, {endpoint.requests} requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
