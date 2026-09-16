#!/usr/bin/env python3
"""Measure both search engines against the same questions.

    python3 tools/retrieval-lab/generate.py
    uv run --extra test python tools/retrieval-lab/evaluate.py

Two things get measured, because the retrieval handover asks for both
and they fail independently.

**Query quality.** Does the page that answers the question come back,
and does it come back first? recall@1, recall@5 and MRR over the gold
set, broken down by the kind of question, so "it got better" can be
traced to which cases got better.

**Confidence correctness.** A search that returns the right page and a
search that returns the nearest wrong page look identical to whatever
reads the results, and the second one is how a family gets a confident
answer about something nobody ever wrote down. So each result set also
carries candidate confidence signals, and this harness asks which of
them actually separates "the top hit answers the question" from "it does
not" -- by AUC, by where a threshold would have to sit, and by what that
threshold costs in wrongly suppressed real answers.

Fairness rules, since the whole point is a comparison:

- Both engines get the same keywords, extracted mechanically from the
  question by dropping stopwords. No model, no hand-tuning -- a keyword
  list written by hand would be written, however unconsciously, to suit
  whichever engine the author is hoping wins.
- Coverage is computed the same way for both, from the page on disk, so
  the confidence comparison is about ranking rather than about one
  engine having a signal the other cannot produce.
- Both are capped at the same result limit, which is what the CLI's
  default `--limit` gives an agent today.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "stacklets" / "memory"))

import fts_index  # noqa: E402
from lib import keywords_to_regex, search_memory  # noqa: E402

LIMIT = 20

# Function words in both languages the family writes in. Dropping them
# is the entire keyword extraction: mechanical, so neither engine gets a
# query shaped in its favour.
STOPWORDS = set("""
a an and are as at be been but by can could did do does for from had has have
how i in is it its of on or our should that the their them they this to too
up us was we were what when where which who why will with would you your

aber alle als am an auch auf aus bei beim bis da damit dann das dass dem den
der des dessen die dies diese diesem diesen dieser dieses doch dort du ein
eine einem einen einer eines er es euch euer für hat hatte hatten hier ich
ihm ihn ihr ihre im in ist ja kann kein keine keinen können mal man mehr
mein mit muss müssen nach nicht noch nun nur ob oder ohne schon sein seine
sich sie sind so soll sollen um und uns unser vom von vor war waren was
weil welche welchem welchen welcher welches wem wen wenn wer werden wie
wieso wir wird wo wofür wohin wurde wurden zu zum zur über
""".split())


# ── the query the engines actually receive ──────────────────────────────

def keywords_for(question: str) -> list[str]:
    """Content words of the question, in order, deduplicated.

    Stands in for the model's rewrite step. Using the real model here
    would make the engine comparison depend on which words it happened
    to pick that run, and the handover's own warning about A/B hygiene
    applies: freeze everything that is not the thing under test.
    """
    seen: list[str] = []
    for raw in question.replace("?", " ").split():
        word = raw.strip(",.:;!\"'()").lower()
        if not word or word in STOPWORDS or len(word) < 2:
            continue
        if word not in seen:
            seen.append(word)
    return seen


# ── backends ────────────────────────────────────────────────────────────

@dataclass
class Result:
    """One backend's answer to one question."""

    ranked: list[str]
    scores: list[float]
    seconds: float


Backend = Callable[[Sequence[str]], Result]


def regex_backend(vault: Path) -> Backend:
    """Today's engine: OR the keywords into a regex, sort by date.

    This is `search_memory` unchanged, driven exactly as `stack memory
    search --nl` drives it, so the baseline is the shipping behaviour
    and not a reconstruction of it.
    """
    def run(keywords: Sequence[str]) -> Result:
        started = time.perf_counter()
        hits = search_memory(keywords_to_regex(list(keywords)), vault,
                             limit=LIMIT)
        elapsed = time.perf_counter() - started
        return Result([h["rel"] for h in hits], [], elapsed)
    return run


def fts5_backend(db: Path, substrings: bool = False) -> Backend:
    def run(keywords: Sequence[str]) -> Result:
        started = time.perf_counter()
        hits = fts_index.search(db, list(keywords), limit=LIMIT,
                                substrings=substrings)
        elapsed = time.perf_counter() - started
        return Result([h.rel for h in hits], [h.score for h in hits], elapsed)
    return run


# ── scoring ─────────────────────────────────────────────────────────────

@dataclass
class Measured:
    """Everything one (question, backend) pair produced."""

    question: str
    klass: str
    lang: str
    gold: list[str]
    ranked: list[str]
    seconds: float
    rank: Optional[int]          # 1-based rank of the first gold page
    signals: dict[str, float] = field(default_factory=dict)

    @property
    def answerable(self) -> bool:
        return bool(self.gold)

    @property
    def top_is_gold(self) -> bool:
        return bool(self.ranked) and self.ranked[0] in self.gold


def first_gold_rank(ranked: Sequence[str], gold: Sequence[str]) -> Optional[int]:
    for position, rel in enumerate(ranked, start=1):
        if rel in gold:
            return position
    return None


def page_tokens(vault: Path, rel: str, cache: dict[str, set[str]]) -> set[str]:
    """Every token on a page, the way the index would have tokenised it."""
    if rel not in cache:
        text = (vault / rel).read_text(encoding="utf-8", errors="ignore")
        cache[rel] = set(fts_index.tokens(text))
    return cache[rel]


def signals_for(result: Result, keywords: Sequence[str], vault: Path,
                cache: dict[str, set[str]]) -> dict[str, float]:
    """Candidate answers to "should anything be said about this at all".

    None of these is the confidence number yet. They are the cheap
    things a caller could compute from a result set, measured side by
    side so the one that actually tracks correctness can be chosen from
    evidence rather than picked because it sounds principled.

    top_coverage     share of the query's keywords present on the top hit.
    best_coverage    the same, over the whole result set.
    margin           how far the top hit's score sits above the
                     runner-up, relative to the top score. Ranking
                     engines only, and zero when there is no runner-up:
                     one lonely hit is not evidence of anything, and
                     scoring it against nothing would make every
                     single-hit query look maximally certain.
    top_score        the top hit's own score. Ranking engines only.
    coverage_margin  the two multiplied, on the theory that confidence
                     needs both "this page has the words" and "no other
                     page is as good". A candidate like the rest; the
                     AUC decides whether it earns its place.
    hit_share        how much of the result limit came back. A query
                     that fills the page with matches is usually a query
                     whose words are too common to mean anything.
    """
    if not result.ranked:
        return {"top_coverage": 0.0, "best_coverage": 0.0, "margin": 0.0,
                "top_score": 0.0, "coverage_margin": 0.0, "hit_share": 0.0}

    coverages = [
        sum(1 for k in keywords
            if fts_index.covers(k, page_tokens(vault, rel, cache)))
        / max(len(keywords), 1)
        for rel in result.ranked
    ]
    signals = {
        "top_coverage": coverages[0],
        "best_coverage": max(coverages),
        "hit_share": len(result.ranked) / LIMIT,
        "margin": 0.0,
        "top_score": 0.0,
    }
    if len(result.scores) >= 2 and result.scores[0]:
        top, second = result.scores[0], result.scores[1]
        signals["top_score"] = top
        signals["margin"] = (top - second) / top
    elif result.scores:
        signals["top_score"] = result.scores[0]
    signals["coverage_margin"] = signals["top_coverage"] * signals["margin"]
    return signals


def auc(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """Probability a positive scores above a negative, ties counting half.

    The Mann-Whitney form, written out rather than imported: the lab has
    no scientific-stack dependency and this is six lines. 0.5 means the
    signal carries no information about whether the answer was found.
    """
    if not positives or not negatives:
        return float("nan")
    wins = 0.0
    for p in positives:
        for n in negatives:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(positives) * len(negatives))


def wilson(hits: int, total: int) -> tuple[float, float]:
    """A 95% interval for a rate measured on very few queries.

    The gold set has tens of questions, not thousands, so a rate like
    "17% of absent facts still get answered" is two queries out of
    twelve and could as honestly be 5% or 45%. The Wilson interval says
    so, where the bare percentage invites a decision the sample cannot
    support. 1.96 is the normal quantile for 95%.
    """
    if total == 0:
        return (0.0, 0.0)
    z = 1.96
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = (z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5)
              / denominator)
    return (max(0.0, centre - spread), min(1.0, centre + spread))


@dataclass
class Threshold:
    """Where a confidence cut would have to sit, and what it would cost."""

    value: float
    balanced_accuracy: float
    false_confident_absent: float
    false_confident_wrong: float
    false_abstain: float


def best_threshold(rows: Sequence[Measured], signal: str) -> Threshold:
    """The cut that best separates "found it" from "did not", and its bill.

    Balanced accuracy rather than plain accuracy because the gold set is
    not balanced and a cut that answers everything would otherwise look
    respectable. The three costs are reported separately because they
    are not interchangeable: wrongly answering a question the vault
    cannot answer is the failure that produces an invented fact, and it
    deserves to be read on its own.
    """
    values = sorted({r.signals.get(signal, 0.0) for r in rows})
    candidates = values + [max(values) + 1e-9] if values else [0.0]
    found = [r for r in rows if r.top_is_gold]
    not_found = [r for r in rows if not r.top_is_gold]
    absent = [r for r in rows if not r.answerable]
    wrong = [r for r in rows if r.answerable and not r.top_is_gold]

    best = Threshold(0.0, 0.0, 1.0, 1.0, 0.0)
    for cut in candidates:
        tpr = (sum(1 for r in found if r.signals.get(signal, 0.0) >= cut)
               / len(found)) if found else 0.0
        tnr = (sum(1 for r in not_found if r.signals.get(signal, 0.0) < cut)
               / len(not_found)) if not_found else 0.0
        balanced = (tpr + tnr) / 2
        if balanced > best.balanced_accuracy:
            best = Threshold(
                value=cut,
                balanced_accuracy=balanced,
                false_confident_absent=(
                    sum(1 for r in absent if r.signals.get(signal, 0.0) >= cut)
                    / len(absent)) if absent else 0.0,
                false_confident_wrong=(
                    sum(1 for r in wrong if r.signals.get(signal, 0.0) >= cut)
                    / len(wrong)) if wrong else 0.0,
                false_abstain=1.0 - tpr,
            )
    return best


# ── reporting ───────────────────────────────────────────────────────────

def quality(rows: Sequence[Measured]) -> dict:
    answerable = [r for r in rows if r.answerable]
    if not answerable:
        return {}
    return {
        "queries": len(answerable),
        "recall@1": sum(1 for r in answerable if r.rank == 1) / len(answerable),
        "recall@5": sum(1 for r in answerable
                        if r.rank and r.rank <= 5) / len(answerable),
        "mrr": sum(1 / r.rank for r in answerable if r.rank) / len(answerable),
    }


def table(title: str, headers: Sequence[str],
          rows: Sequence[Sequence[object]]) -> str:
    """Fixed-width columns. Every cell is rendered with `str`."""
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows
              else len(str(h)) for i, h in enumerate(headers)]
    line = "  ".join(str(h).ljust(w) for h, w in zip(headers, widths))
    out = [f"\n{title}", line, "  ".join("-" * w for w in widths)]
    out += ["  ".join(str(c).ljust(w) for c, w in zip(r, widths)) for r in rows]
    return "\n".join(out)


def pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(HERE / "out" / "vault"))
    parser.add_argument("--json", default=str(HERE / "out" / "results.json"))
    ns = parser.parse_args()

    vault = Path(ns.vault)
    if not vault.exists():
        sys.exit(f"no vault at {vault} -- run generate.py first")

    fact_paths = yaml.safe_load(
        (vault.parent / "fact-paths.yaml").read_text(encoding="utf-8"))
    gold_spec = yaml.safe_load(
        (HERE / "goldset.yaml").read_text(encoding="utf-8"))

    db = vault.parent / "vault-index.sqlite3"
    if db.exists():
        db.unlink()
    built = time.perf_counter()
    stats = fts_index.build_index(vault, db)
    build_seconds = time.perf_counter() - built

    warm = time.perf_counter()
    fts_index.build_index(vault, db)
    rescan_seconds = time.perf_counter() - warm

    backends: dict[str, Backend] = {
        "regex": regex_backend(vault),
        "fts5": fts5_backend(db),
        "fts5+tri": fts5_backend(db, substrings=True),
    }

    cache: dict[str, set[str]] = {}
    measured: dict[str, list[Measured]] = {name: [] for name in backends}
    for query in gold_spec["queries"]:
        keywords = keywords_for(query["q"])
        gold = [fact_paths[fid] for fid in query.get("gold") or []]
        for name, backend in backends.items():
            result = backend(keywords)
            measured[name].append(Measured(
                question=query["q"], klass=query["class"], lang=query["lang"],
                gold=gold, ranked=result.ranked, seconds=result.seconds,
                rank=first_gold_rank(result.ranked, gold),
                signals=signals_for(result, keywords, vault, cache),
            ))

    # ── query quality ───────────────────────────────────────────────────
    print(f"corpus: {stats.total} pages   index build: "
          f"{build_seconds * 1000:.0f} ms   no-change rescan: "
          f"{rescan_seconds * 1000:.0f} ms")

    overall = {name: quality(rows) for name, rows in measured.items()}
    print(table(
        "Query quality (answerable questions only)",
        ["backend", "queries", "recall@1", "recall@5", "MRR", "p50 ms",
         "p95 ms"],
        [[name, overall[name]["queries"], pct(overall[name]["recall@1"]),
          pct(overall[name]["recall@5"]), f"{overall[name]['mrr']:.2f}",
          f"{statistics.median(r.seconds for r in rows) * 1000:.1f}",
          f"{sorted(r.seconds for r in rows)[int(len(rows) * 0.95) - 1] * 1000:.1f}"]
         for name, rows in measured.items()]))

    classes = [c for c in dict.fromkeys(q["class"] for q in gold_spec["queries"])
               if c != "absent"]
    per_class = []
    for klass in classes:
        row = [klass]
        for name in backends:
            rows = [r for r in measured[name] if r.klass == klass]
            q = quality(rows)
            row.append(f"{pct(q['recall@1'])} / {pct(q['recall@5'])}")
        per_class.append(row + [str(len([q for q in gold_spec["queries"]
                                         if q["class"] == klass]))])
    print(table("recall@1 / recall@5 by question kind",
                ["kind", *backends, "n"], per_class))

    # ── confidence correctness ──────────────────────────────────────────
    signal_names = ["top_coverage", "best_coverage", "margin", "top_score",
                    "coverage_margin", "hit_share"]

    def auc_for(rows: Sequence[Measured], signal: str) -> float:
        return auc([r.signals.get(signal, 0.0) for r in rows if r.top_is_gold],
                   [r.signals.get(signal, 0.0) for r in rows
                    if not r.top_is_gold])

    auc_rows = []
    for signal in signal_names:
        row = [signal]
        for name in backends:
            value = auc_for(measured[name], signal)
            row.append("--" if value != value else f"{value:.2f}")
        auc_rows.append(row)
    print(table(
        'Confidence signals: AUC for "the top hit answers the question"'
        "\n(0.5 = tells you nothing; below 0.5 = the signal runs backwards)",
        ["signal", *backends], auc_rows))

    # The AUC winner is not automatically the signal to ship. AUC ranks
    # separation across every possible cut; what a family lives with is
    # one cut, and two signals with the same AUC can put their mistakes
    # in very different places. So every signal gets its bill printed.
    thresholds: dict[str, dict] = {}
    cut_rows = []
    for name in backends:
        rows = measured[name]
        for signal in signal_names:
            if len({r.signals.get(signal, 0.0) for r in rows}) < 2:
                continue
            cut = best_threshold(rows, signal)
            thresholds.setdefault(name, {})[signal] = vars(cut)
            absent_n = sum(1 for r in rows if not r.answerable)
            low, high = wilson(
                round(cut.false_confident_absent * absent_n), absent_n)
            cut_rows.append([
                name, signal, f"{cut.value:.2f}",
                pct(cut.balanced_accuracy),
                f"{pct(cut.false_confident_absent)} [{pct(low)}-{pct(high)}]",
                pct(cut.false_confident_wrong), pct(cut.false_abstain)])
    print(table(
        "Every signal at its best cut, and what that cut costs",
        ["backend", "signal", "cut", "balanced acc",
         "answers an absent fact", "answers with wrong page",
         "suppresses a right answer"], cut_rows))

    # Answering with no gate at all is the situation today: any hit is
    # treated as an answer. Reported so the gate has something to beat.
    ungated = []
    absent_total = sum(1 for r in measured["regex"] if not r.answerable)
    for name in backends:
        absent = [r for r in measured[name] if not r.answerable]
        answered = sum(1 for r in absent if r.ranked)
        low, high = wilson(answered, len(absent))
        ungated.append([
            name, f"{pct(answered / len(absent))} [{pct(low)}-{pct(high)}]",
            f"{statistics.median(len(r.ranked) for r in absent):.0f}"])
    print(table("Without a confidence gate: what an absent fact returns today"
                f"\n(n={absent_total} absent questions, 95% interval)",
                ["backend", "returns at least one page", "median hits"],
                ungated))

    # Validity check, not a result. `top_coverage` is a fraction of the
    # query's keywords, so it falls as a question gets longer. If the
    # absent questions were systematically wordier than the answerable
    # ones, the signal would be measuring question length and the AUC
    # above would be an artefact of how the gold set was written.
    lengths = {
        "answerable": [len(keywords_for(q["q"])) for q in gold_spec["queries"]
                       if q.get("gold")],
        "absent": [len(keywords_for(q["q"])) for q in gold_spec["queries"]
                   if not q.get("gold")],
    }
    print(table(
        "Validity check: is the coverage signal just measuring question length?",
        ["questions", "n", "median keywords", "mean keywords"],
        [[kind, str(len(values)), f"{statistics.median(values):.1f}",
          f"{statistics.mean(values):.2f}"]
         for kind, values in lengths.items()]))

    Path(ns.json).write_text(json.dumps({
        "corpus_pages": stats.total,
        "index_build_ms": build_seconds * 1000,
        "rescan_ms": rescan_seconds * 1000,
        "quality": overall,
        "thresholds": thresholds,
        "queries": {name: [
            {"q": r.question, "class": r.klass, "lang": r.lang,
             "gold": r.gold, "rank": r.rank, "top": r.ranked[0] if r.ranked
             else None, "hits": len(r.ranked), "ms": r.seconds * 1000,
             "signals": r.signals}
            for r in rows] for name, rows in measured.items()},
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nfull per-query detail: {ns.json}")


if __name__ == "__main__":
    main()
