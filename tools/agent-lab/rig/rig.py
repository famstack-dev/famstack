#!/usr/bin/env python3
"""Isolated end-to-end rig for the famstack agent.

The rig runs the real agent image, the real runtime shims, and the real
tool loop. It replaces every production dependency:

  Matrix        -> `nanobot agent -m` (direct turn, no channel)
  family vault  -> demo vault with fabricated data (git repo)
  famstack-api  -> lab-api.py on port 42011
  oMLX direct   -> proxy.py on port 42012 (logs every request)

Subcommands:
  build   Build the agent image from stacklets/agent/.
  reset   Wipe and seed the rig state (workspace, config, demo vault).
  turn    Run one agent turn. Print the reply and the per-call metrics.

Start lab-api.py and proxy.py before the first turn.
"""

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

RIG = Path(__file__).resolve().parent
REPO = RIG.parents[2]
STATE = RIG / "state"
IMAGE = "famstack-agent-lab"
LOG_PATH = REPO / "docs" / "design" / "agent" / "agent-improvement-log.md"
PROXY_LOG = STATE / "proxy-log.jsonl"


def _ai_config() -> dict:
    ai = tomllib.loads((REPO / "stack.toml").read_text()).get("ai", {})
    return {"key": ai.get("openai_key", "none"), "model": ai.get("default", "")}


def cmd_build(_args):
    subprocess.run(["docker", "build", "-t", IMAGE,
                    str(REPO / "stacklets" / "agent")], check=True)


def cmd_reset(_args):
    """Seed a fresh rig state. Keeps nothing from earlier runs."""
    shutil.rmtree(STATE, ignore_errors=True)
    ws = STATE / "nanobot" / "workspace"
    ws.mkdir(parents=True)
    shutil.copy(RIG / "config.json", STATE / "nanobot" / "config.json")

    seed = REPO / "stacklets" / "agent" / "workspace"
    for name in ("SOUL.md", "AGENTS.md"):
        text = (seed / name).read_text().replace("__AGENT_NAME__", "Stacky")
        (ws / name).write_text(text)
    # `--skills` seeds from somewhere else, which is how two versions of
    # a skill get compared: the prompt is the thing under test, so it
    # has to be swappable without editing the tree between runs.
    shutil.copytree(Path(_args.skills) if _args.skills else seed / "skills",
                    ws / "skills")
    # Same minimal USER.md the production entrypoint seeds.
    (ws / "USER.md").write_text(
        "# User Profile\n\n"
        "The runtime brief names the current speaker each turn. No data here.\n")

    # The brief reads git history from the vault. Make the demo vault a
    # real git repo, with the same shape as the production clone.
    #
    # `--corpus` seeds from somewhere else. Thirteen pages is enough to
    # exercise the scenarios but not to tell two search engines apart:
    # an agent that can read every page finds everything regardless of
    # ranking. Comparing engines needs a vault the size of a real one.
    vault = STATE / "vault"
    shutil.copytree(Path(_args.corpus) if _args.corpus else RIG / "demo-vault",
                    vault)
    env = {"GIT_AUTHOR_NAME": "homer", "GIT_AUTHOR_EMAIL": "homer@demo.invalid",
           "GIT_COMMITTER_NAME": "homer", "GIT_COMMITTER_EMAIL": "homer@demo.invalid"}
    for cmd in (["git", "init", "-q", "-b", "main"],
                ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "docs(memory): seed demo vault"]):
        subprocess.run(cmd, cwd=vault, check=True, env=env)
    print(f"rig state ready: {STATE}")


def _proxy_offset() -> int:
    return PROXY_LOG.stat().st_size if PROXY_LOG.exists() else 0


def _proxy_calls(offset: int) -> list[dict]:
    if not PROXY_LOG.exists():
        return []
    with PROXY_LOG.open() as fh:
        fh.seek(offset)
        return [json.loads(ln) for ln in fh if ln.strip()]


def cmd_turn(args):
    cfg = _ai_config()
    offset = _proxy_offset()
    docker_cmd = [
        "docker", "run", "--rm",
        "--add-host", "host.docker.internal:host-gateway",
        "-v", f"{STATE / 'nanobot'}:/home/nanobot/.nanobot",
        "-v", f"{STATE / 'vault'}:/home/nanobot/.nanobot/workspace/vault:ro",
        "-e", "AGENT_OPENAI_URL=http://host.docker.internal:42012/v1",
        "-e", f"AGENT_OPENAI_KEY={cfg['key']}",
        "-e", f"AGENT_MODEL={cfg['model']}",
        "-e", "STACK_API_ADDR=host.docker.internal:42011",
        *[x for pair in (args.env or []) for x in ("-e", pair)],
        "--entrypoint", "nanobot",
        IMAGE, "agent", "-m", args.message, "-s", args.session,
        "--no-markdown",
    ]
    if args.logs:
        docker_cmd.append("--logs")
    start = time.monotonic()
    r = subprocess.run(docker_cmd, capture_output=True, text=True)
    wall = time.monotonic() - start
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)

    calls = [c for c in _proxy_calls(offset) if c.get("path", "").endswith("/chat/completions")]
    rows = []
    for i, c in enumerate(calls, 1):
        u = c.get("usage") or {}
        rows.append({
            "call": i,
            "n_messages": c.get("n_messages"),
            "prompt_tokens": u.get("prompt_tokens"),
            "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "completion_tokens": u.get("completion_tokens"),
            "ttft_s": u.get("time_to_first_token"),
            "duration_s": c.get("duration_s"),
        })
    summary = {"wall_s": round(wall, 2), "llm_calls": len(calls), "rows": rows,
               "exit": r.returncode}
    print(json.dumps(summary, indent=2))

    if args.log:
        _log_turn(args, cfg, summary)
    return 0 if r.returncode == 0 else 1


def _log_turn(args, cfg, summary):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"\n## {stamp} - rig turn ({args.session})\n"]
    lines.append(f"- message: `{args.message}`")
    lines.append(f"- model: `{cfg['model']}`, wall time {summary['wall_s']} s, "
                 f"{summary['llm_calls']} LLM call(s), exit {summary['exit']}")
    if summary["rows"]:
        lines.append("")
        lines.append("| call | messages | prompt_tokens | cached_tokens | completion_tokens | ttft_s | duration_s |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in summary["rows"]:
            lines.append(f"| {r['call']} | {r['n_messages']} | {r['prompt_tokens']} | "
                         f"{r['cached_tokens']} | {r['completion_tokens']} | "
                         f"{r['ttft_s']} | {r['duration_s']} |")
    if args.note:
        lines.append(f"\n- note: {args.note}")
    with LOG_PATH.open("a") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"logged to {LOG_PATH.relative_to(REPO)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("--corpus", default=None, metavar="DIR",
                         help="seed the vault from DIR instead of demo-vault")
    p_reset.add_argument("--skills", default=None, metavar="DIR",
                         help="seed skills from DIR instead of the repo's")
    p_turn = sub.add_parser("turn")
    p_turn.add_argument("message")
    p_turn.add_argument("--session", default="rig:main")
    p_turn.add_argument("--logs", action="store_true", help="show nanobot runtime logs")
    p_turn.add_argument("--no-log", dest="log", action="store_false")
    p_turn.add_argument("--note", default="")
    p_turn.add_argument("--env", action="append", metavar="KEY=VAL",
                        help="extra env for the agent container, repeatable")
    args = parser.parse_args()
    return {"build": cmd_build, "reset": cmd_reset, "turn": cmd_turn}[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
