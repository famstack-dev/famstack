#!/usr/bin/env python3
"""Run the same hard questions through the agent on each search backend.

    uv run --extra test python tools/retrieval-lab/agent_ab.py

The engine bench measures whether retrieval hands over the right page.
That is an intermediate result. What decides whether any of this is
worth shipping is whether the *family* gets a better answer, and an
agent that can search twice and read a page closes a lot of the gap on
its own. So these questions are deliberately ones a single lookup
cannot answer: they need several pages combined, arithmetic across
them, or the discipline to say "that is not written down anywhere".

Three backends, served by three lab-api instances on three ports so a
turn can pick one without a restart:

    regex       today's engine: every file matched, newest first
    fts5        the ranked index, best match first
    fts5+gate   the same, but silent when the best hit carries almost
                none of what was asked

Prerequisites, all started by hand first (see the rig README):

    rig.py reset --corpus <a vault of a few hundred pages>
    proxy.py --target <oMLX>
    lab-api.py --listen 42011 --backend regex      ... and 42013, 42014

Every turn runs in its own session, so no answer is helped by a
previous one, and `--no-log` keeps 20-odd engine turns out of the
agent improvement log. The summary goes to `out/agent-ab.json`, replies
included, because the numbers cannot tell you whether an answer was
actually right. That part is a human reading them.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RIG = REPO / "tools" / "agent-lab" / "rig" / "rig.py"

BACKENDS = {"regex": 42011, "fts5": 42013, "fts5+gate": 42014}

# Each question needs more than one page, or needs the agent to decline.
# `expect` is what a correct answer has to contain, for a human reading
# the replies afterwards; nothing here is scored automatically, because
# an answer can carry the right number and still be wrong about why.
SCENARIOS = [
    {
        "id": "camping-todo",
        "q": "Was müssen wir vor der Campingfahrt noch erledigen?",
        "kind": "multi-page",
        "expect": "Kocher defekt (Neds leihen), Anzahlung ist raus / Rest vor "
                  "Ort, Batterien für die Campinglampe",
    },
    {
        "id": "repair-total",
        "q": "Wie viel haben wir dieses Jahr insgesamt für Reparaturen bezahlt?",
        "kind": "arithmetic",
        "expect": "90 + 320 + 20 + 140 = 570 Euro (Spülmaschine, TÜV, "
                  "Fahrrad, Heizung)",
    },
    {
        "id": "nut-cake",
        "q": "Können wir Tante Hilde die Nusstorte servieren?",
        "kind": "constraint",
        "expect": "Nein, Haselnussallergie, auch keine Spuren; "
                  "Zitronenkuchen stattdessen",
    },
    {
        "id": "expiring",
        "q": "Was läuft demnächst ab oder muss rechtzeitig erneuert werden?",
        "kind": "temporal",
        "expect": "Barts Pass (Mai), Hausratversicherung (Kündigung 3 Monate "
                  "vor Jahresende), Winterreifen (vor Oktober), Heizung "
                  "(12 Monate)",
    },
    {
        "id": "feier",
        "q": "Wann ist die Feier und wie viele Leute kommen?",
        "kind": "compound",
        "expect": "Sonntagnachmittag, Anfang um drei, vierzehn Zusagen",
    },
    {
        "id": "absent-birthday",
        "q": "Wann hat Maggie Geburtstag?",
        "kind": "absent",
        "expect": "must say it is not in the vault; must not borrow Omas "
                  "Geburtstagsfeier",
    },
    {
        "id": "absent-ticket",
        "q": "Wie viel hat das Flugticket gekostet?",
        "kind": "absent-adjacent",
        "expect": "must say the price is not recorded; the 400 Euro on that "
                  "page is compensation, not the ticket",
    },
]


def run_turn(question: str, backend: str, port: int, session: str) -> dict:
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(RIG), "turn", question,
         "--session", session, "--no-log",
         "--env", f"STACK_API_ADDR=host.docker.internal:{port}"],
        capture_output=True, text=True)
    wall = time.monotonic() - started

    # rig.py prints the reply, then the metrics object last.
    summary: dict = {}
    head, brace, tail = result.stdout.rpartition("\n{\n")
    if brace:
        try:
            summary = json.loads("{\n" + tail)
        except ValueError:
            head = result.stdout
    else:
        head = result.stdout
    return {
        "backend": backend,
        "reply": head.strip(),
        "wall_s": summary.get("wall_s", round(wall, 2)),
        "llm_calls": summary.get("llm_calls"),
        "rows": summary.get("rows", []),
        "exit": result.returncode,
        "stderr": result.stderr[-400:] if result.returncode else "",
    }


def main() -> None:
    out: list[dict] = []
    for scenario in SCENARIOS:
        for backend, port in BACKENDS.items():
            session = f"ab:{scenario['id']}:{backend.replace('+', '-')}"
            print(f"[{backend:>9}] {scenario['id']}", flush=True)
            turn = run_turn(scenario["q"], backend, port, session)
            out.append({**scenario, **turn})
            print(f"            {turn['llm_calls']} calls, "
                  f"{turn['wall_s']}s", flush=True)

    target = HERE / "out" / "agent-ab.json"
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                      encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"{'question':<18} {'backend':>9}  {'calls':>5}  {'wall_s':>7}")
    for row in out:
        print(f"{row['id']:<18} {row['backend']:>9}  "
              f"{str(row['llm_calls']):>5}  {row['wall_s']:>7}")
    print(f"\nreplies: {target}")


if __name__ == "__main__":
    main()
