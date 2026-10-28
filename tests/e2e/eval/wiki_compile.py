"""The wiki pages a real model writes for a fabricated family, scored.

The prompt measurement (`tests/memory/unit/compile_prompts.py`) runs the
wiki compile on a fabricated family with a stand-in model and counts the
prompts. This script runs the same scenarios with a real model
and asks what the pages lose: which of the facts the family's first
filings plant (`fabricated_family.py`) reach their section of the home
page, whether every citation on every page resolves, and what each call
cost. Every change to the compile is meant to save tokens and seconds
without losing a fact, and two runs compared side by side show both.

The scenarios are the measurement's own (`compile_prompts.play`): the
seed (first boot, then a night), one document, one diary entry, a
correction, a quiet night, the next night, a ticked list item, and a
burst of 40 filings, on a memory of 50, 500 or 2,000 filings. Each step
is the curator's loop in a process of its own, which starts the memory
stacklet's entry point for every compile, as in the measurement. The
model's address they are given is a small proxy in this process, which
passes each request on to the real server with its key and the model's
name, and keeps the prompt and the answer. The key never reaches the compile's environment or the run
directory.

Each run writes `runs/<stamp>_<model>_<commit>/`:

    calls.jsonl     one line per model call: the compile's own record of it
                    (`call_costs`: page, kind, estimated, prompt, cached and
                    completion tokens, seconds, ok) with size, repeat and scenario
    score.json      per size, repeat and scenario: facts found in their section,
                    cited and stale, citations resolved, the scenario's seconds
    <size>/<repeat>/<scenario>/
        calls/NN-<page>.prompt.txt, NN-<page>.answer.txt
        pages/<path>                every page the model has written so far

`--runs N` (default 2) plays every scenario N times, each on a fresh
memory. The compile pins temperature 0, so the spread between repeats is
the server's. `--compare <run a> <run b>` prints one table per scenario
and size: tokens, seconds, calls, facts found and cited, citations
resolved, and the difference.

    AI_DEFAULT_MODEL=<model> EVAL_AI_URL=<base url, /v1> EVAL_AI_KEY=<key> \\
        uv run --extra test python tests/e2e/eval/wiki_compile.py [--sizes 50,500] [--runs 2]
    uv run --extra test python tests/e2e/eval/wiki_compile.py --compare <run a> <run b>

Opt-in and report-only, like the rest of `tests/e2e/eval/`: nothing
collects it, and it fails nothing. The scoring is pure and has unit tests
(`wiki_score.py`, `tests/memory/unit/test_memory_wiki_score.py`).

Cost: every page the compile writes is a call. At 50 filings a repeat is
about 70 calls; at 500 and 2,000 the calls are the same, and the prompts
grow with the memory (the measurement's report has the sizes). The model
server is shared with the rig, so run 2,000 filings once, outside the day's
busy hours.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _path in (_ROOT, _ROOT / "lib"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from loguru import logger  # noqa: E402

from stack.stacklet_code import import_stacklets  # noqa: E402

import_stacklets([_ROOT / "stacklets"])

from tests.e2e.eval._artifacts import _RUNS_DIR, _json, _slug  # noqa: E402
from tests.e2e.eval.wiki_score import compare, measures, score  # noqa: E402
from tests.memory.unit.fabricated_family import fabricated_family  # noqa: E402
from tests.memory.unit.instance import running_memory  # noqa: E402
from tests.memory.unit.compile_prompts import SCENARIOS, page_of, play  # noqa: E402

SIZES = (50, 500)
HOME = "index.md"
# The longest a call may take: the compile's own client waits 15 minutes.
CALL_TIMEOUT = 900
# The longest one step of the curator's loop may take: a night at 2,000
# filings asks for every page, each a call of minutes.
STEP_TIMEOUT = 4 * 3600


# ── The model server, through a proxy that keeps what it sends ───────────

class Upstream:
    """The real model server behind a local address the compile is given.

    Each request is passed on with the server's key and the model's name
    in place of the stand-in's, and kept in `calls` as `(page, prompt,
    answer)`: the page as the prompt names it, the messages' text, and
    the answer's text (or the server's error).
    """

    def __init__(self, url: str, key: str, model: str):
        self.base, self.key, self.model = url.rstrip("/"), key, model
        self.calls: list[tuple[str, str, str]] = []
        self.url = ""
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def serving(self) -> Iterator[str]:
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                status, answer = upstream.forward(self.path, body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{server.server_port}/v1"
        try:
            yield self.url
        finally:
            server.shutdown()
            server.server_close()

    def forward(self, path: str, body: dict) -> tuple[int, bytes]:
        """Pass one request on; the status and body the server answered."""
        body["model"] = self.model
        prompt = "\n".join(m["content"] for m in body.get("messages", [])
                           if isinstance(m.get("content"), str))
        request = urllib.request.Request(
            self.base + path.removeprefix("/v1"), data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(request, timeout=CALL_TIMEOUT) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except (urllib.error.URLError, TimeoutError) as e:
            status = 502
            raw = json.dumps({"error": {"message": str(e), "type": "proxy_error"}}).encode()
        try:
            answer = json.loads(raw)["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            answer = f"HTTP {status}: {raw.decode('utf-8', 'replace')[:2000]}"
        with self._lock:
            self.calls.append((page_of(prompt), prompt, answer))
        return status, raw


# ── One run ──────────────────────────────────────────────────────────────

class Run:
    """The run directory, written as the scenarios go, so a run cut short
    keeps everything up to the scenario it was in."""

    def __init__(self, model: str, url: str):
        self.started = dt.datetime.now()
        self.commit = _git("rev-parse", "--short", "HEAD")
        self.root = _RUNS_DIR / f"{self.started:%Y-%m-%d_%H-%M-%S}_{_slug(model)}_{self.commit}"
        self.root.mkdir(parents=True)
        self.summary = {"model": model, "url": url, "commit": self.commit,
                        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
                        "dirty": bool(_git("status", "--porcelain")),
                        "started": self.started.isoformat(timespec="seconds"),
                        "seconds": {}, "sizes": {}}

    def calls(self, records: list[dict]) -> None:
        with (self.root / "calls.jsonl").open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)

    def scored(self, size: int, repeat: int, scenario: str, result: dict) -> None:
        self.summary["sizes"].setdefault(str(size), {}).setdefault(str(repeat), {})[scenario] = result
        self.save()

    def save(self) -> None:
        self.summary["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        (self.root / "score.json").write_text(_json(self.summary), encoding="utf-8")


def play_once(run: Run, upstream: Upstream, size: int, repeat: int) -> None:
    """Every scenario on a fresh memory of `size` filings, scored after each."""
    family = fabricated_family(size)
    facts = family.facts
    with tempfile.TemporaryDirectory(prefix=f"wiki-eval-{size}-") as tmp:
        root = Path(tmp)
        bundle = family.history(root / f"{size}.bundle")
        with running_memory(root / "instance", {}, history=bundle, model=upstream.url) as memory:
            written: set[str] = set()

            def read(link: str) -> str | None:
                """A References row's link: a card in the memory, or a page
                in the wiki (a diary entry's row leads to its month's page)."""
                rel = link.lstrip("/")
                for base in (memory.vault, memory.brain):
                    if (base / rel).is_file():
                        return (base / rel).read_text(encoding="utf-8")
                return None

            def scenario(name: str, *steps) -> None:
                where = run.root / str(size) / str(repeat) / name
                logged, asked, began = len(memory.calls()), len(upstream.calls), time.monotonic()
                error = ""
                try:
                    for step in steps:
                        step()
                except Exception as e:  # noqa: BLE001 -- a failed scenario is scored, not a crash
                    error = f"{type(e).__name__}: {e}"
                seconds = round(time.monotonic() - began, 1)
                records = [{"size": size, "repeat": repeat, "scenario": name, **c}
                           for c in memory.calls()[logged:]]
                run.calls(records)
                _keep_calls(where / "calls", upstream.calls[asked:])
                written.update(r["page"] for r in records if r.get("page"))
                pages = {p: (memory.brain / p).read_text(encoding="utf-8")
                         for p in sorted(written) if (memory.brain / p).is_file()}
                for path, text in pages.items():
                    (where / "pages" / path).parent.mkdir(parents=True, exist_ok=True)
                    (where / "pages" / path).write_text(text, encoding="utf-8")
                home = (memory.brain / HOME).read_text(encoding="utf-8") \
                    if (memory.brain / HOME).is_file() else ""
                result = {**score(home, pages, facts, read), "seconds": seconds,
                          "calls": len(records), "error": error}
                run.scored(size, repeat, name, result)
                print(f"  {size} #{repeat} {name:<10} {len(records):>3} calls {seconds:>7.1f}s  "
                      f"facts {result['facts_found']}/{result['facts_total']}  citations "
                      f"{result['citations_resolved']}/{result['citations_total']}"
                      + (f"  {error}" if error else ""), flush=True)

            play(family, memory, scenario, timeout=STEP_TIMEOUT)


def _keep_calls(where: Path, calls: list[tuple[str, str, str]]) -> None:
    where.mkdir(parents=True, exist_ok=True)
    for n, (page, prompt, answer) in enumerate(calls, start=1):
        name = f"{n:02d}-{_slug(page.replace(':', '-'))}"
        (where / f"{name}.prompt.txt").write_text(prompt, encoding="utf-8")
        (where / f"{name}.answer.txt").write_text(answer, encoding="utf-8")


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(_ROOT), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


# ── Two runs side by side ────────────────────────────────────────────────

def load(run: Path) -> tuple[str, dict]:
    """A run directory's name and its measures (`wiki_score.measures`)."""
    run = run if run.is_dir() else _RUNS_DIR / run.name
    calls = [json.loads(line) for line in
             (run / "calls.jsonl").read_text(encoding="utf-8").splitlines() if line]
    summary = json.loads((run / "score.json").read_text(encoding="utf-8"))
    return run.name, measures(calls, summary["sizes"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sizes", default=",".join(map(str, SIZES)),
                        help="filings, comma-separated (default 50,500; 2000 is the large one)")
    parser.add_argument("--runs", type=int, default=2, help="repeats of every scenario (default 2)")
    parser.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"), type=Path,
                        help="print two run directories side by side, and run nothing")
    args = parser.parse_args()

    if args.compare:
        (name_a, a), (name_b, b) = (load(p) for p in args.compare)
        print(f"a: {name_a}\nb: {name_b}\n")
        print(compare(a, b, order=SCENARIOS))
        return 0

    model = os.environ.get("AI_DEFAULT_MODEL", "").strip()
    url = os.environ.get("EVAL_AI_URL", "").strip()
    if not model or not url:
        print("Set AI_DEFAULT_MODEL and EVAL_AI_URL (and EVAL_AI_KEY): the model to measure "
              "and its server.", file=sys.stderr)
        return 2
    sizes = [int(s) for s in re.split(r"[,\s]+", args.sizes) if s]
    # The curator's progress lines, a few hundred per scenario, would bury
    # the one line per scenario this prints.
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    upstream = Upstream(url, os.environ.get("EVAL_AI_KEY", ""), model)
    run = Run(model, url)
    print(f"{run.root}", flush=True)
    with upstream.serving():
        for size in sizes:
            began = time.monotonic()
            for repeat in range(1, args.runs + 1):
                play_once(run, upstream, size, repeat)
            run.summary["seconds"][str(size)] = round(time.monotonic() - began)
            run.save()
    print(f"\n{model}, {len(upstream.calls)} requests, {run.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
