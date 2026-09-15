#!/usr/bin/env python3
"""Validation harness for agent experiments.

This tool measures the LLM serving path in isolation. It sends only
synthetic data to the oMLX endpoint. It does not touch stack services,
Matrix, or family data.

Subcommands:
  probe   Send one small completion. Show the raw usage object.
  cache   Measure KV-cache behavior: cold, append-only, and mutated prompts.

Each run writes a raw JSON result to tools/agent-lab/results/ and
appends a summary to docs/design/agent/agent-improvement-log.md.
The tool uses only the Python standard library.
"""

import argparse
import datetime as dt
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"
LOG_PATH = REPO_ROOT / "docs" / "design" / "agent" / "agent-improvement-log.md"

# oMLX serves Qwen models. The stack disables thinking for all calls.
# The harness does the same so measurements match production behavior.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}

# Fixed vocabulary for deterministic filler text. The seed is fixed, so
# every run builds byte-identical prompts. Byte-identical prompts are
# necessary to compare cache behavior across runs.
_WORDS = (
    "system module sensor value record window channel signal filter state "
    "buffer packet metric report table index handle timer socket thread "
    "queue cache token prompt model layer weight block page memory disk"
).split()


def _config_defaults() -> dict:
    """Read endpoint defaults from stack.toml. Flags override these."""
    import tomllib

    path = REPO_ROOT / "stack.toml"
    if not path.exists():
        return {}
    ai = tomllib.loads(path.read_text()).get("ai", {})
    return {
        "url": ai.get("openai_url"),
        "key": ai.get("openai_key"),
        "model": ai.get("default"),
    }


def filler_text(words: int, seed: int) -> str:
    """Build deterministic text with approximately the given word count."""
    rng = random.Random(seed)
    out = []
    while len(out) < words:
        sentence = [rng.choice(_WORDS) for _ in range(rng.randint(6, 12))]
        sentence[0] = sentence[0].capitalize()
        out.extend(sentence)
        out[-1] += "."
    return " ".join(out[:words]) + "."


class Endpoint:
    """One OpenAI-compatible chat endpoint."""

    def __init__(self, url: str, key: str, model: str, timeout: float):
        self.url = url.rstrip("/")
        self.key = key
        self.model = model
        self.timeout = timeout

    def _request(self, path: str, body: dict | None = None) -> urllib.request.Request:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            method="POST" if body is not None else "GET",
        )
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Content-Type", "application/json")
        return req

    def models(self) -> dict:
        with urllib.request.urlopen(self._request("/models"), timeout=10) as resp:
            return json.load(resp)

    def chat(self, messages: list[dict], max_tokens: int, stream: bool) -> dict:
        """Send one chat completion. Return timing and usage data.

        Timing fields:
          t_first_chunk   time to the first SSE chunk (stream only)
          t_first_token   time to the first content delta (stream only)
          t_total         time to the full response
        """
        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            **NO_THINKING,
        }
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}

        start = time.monotonic()
        req = self._request("/chat/completions", body)
        result: dict = {
            "t_first_chunk": None,
            "t_first_token": None,
            "t_total": None,
            "usage": None,
            "text": "",
            "error": None,
        }
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if not stream:
                    data = json.load(resp)
                    result["t_total"] = time.monotonic() - start
                    result["usage"] = data.get("usage")
                    result["text"] = data["choices"][0]["message"].get("content") or ""
                    return result
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    now = time.monotonic() - start
                    if result["t_first_chunk"] is None:
                        result["t_first_chunk"] = now
                    if chunk.get("usage"):
                        result["usage"] = chunk["usage"]
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta", {})
                        content = delta.get("content")
                        if content:
                            if result["t_first_token"] is None:
                                result["t_first_token"] = now
                            result["text"] += content
            result["t_total"] = time.monotonic() - start
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["t_total"] = time.monotonic() - start
        return result


def build_conversation(prefix_words: int, history_turns: int, seed: int) -> list[dict]:
    """Build a synthetic conversation: one large system prompt plus history.

    The shape follows the production agent: a stable ~10K-token prefix and
    a multi-turn transcript. All content is synthetic.
    """
    messages = [{"role": "system", "content": filler_text(prefix_words, seed)}]
    for i in range(history_turns):
        messages.append(
            {"role": "user", "content": f"Question {i}: " + filler_text(60, seed + 10 + i)}
        )
        messages.append(
            {"role": "assistant", "content": f"Answer {i}: " + filler_text(80, seed + 100 + i)}
        )
    return messages


def _phase_row(name: str, r: dict) -> dict:
    usage = r.get("usage") or {}
    return {
        "phase": name,
        "ttft_s": round(r["t_first_token"], 3) if r["t_first_token"] else None,
        "total_s": round(r["t_total"], 3) if r["t_total"] else None,
        "usage": usage,
        "error": r.get("error"),
    }


def cmd_probe(ep: Endpoint, args) -> dict:
    """Check the endpoint. Report the raw usage object and its cache fields."""
    models = ep.models()
    served = [m.get("id") for m in models.get("data", [])]
    print(f"endpoint: {ep.url}")
    print(f"served models: {served}")
    msgs = [
        {"role": "system", "content": "You are a test endpoint. Answer in one short sentence."},
        {"role": "user", "content": "Say the word ready."},
    ]
    first = ep.chat(msgs, max_tokens=16, stream=args.stream)
    second = ep.chat(msgs, max_tokens=16, stream=args.stream)
    print("first call usage:", json.dumps(first.get("usage"), indent=2))
    print("second call usage:", json.dumps(second.get("usage"), indent=2))
    return {
        "served_models": served,
        "first": _phase_row("first", first),
        "second": _phase_row("second (identical prompt)", second),
    }


def cmd_cache(ep: Endpoint, args) -> dict:
    """Measure prefix-cache behavior in four phases.

    cold:    full synthetic conversation, first contact.
    warm:    the same conversation plus the real reply and one new turn.
    warm2:   one more append-only turn.
    mutate:  the warm2 conversation with one early message rewritten,
             plus one new turn. This simulates the lean_state rewrite.
    Expected result: warm and warm2 have a low TTFT. mutate has a TTFT
    near the cold value, because the prefix diverges early.
    """
    msgs = build_conversation(args.prefix_words, args.history_turns, args.seed)
    rows = []

    def turn(question: str) -> dict:
        msgs.append({"role": "user", "content": question})
        r = ep.chat(msgs, max_tokens=args.max_tokens, stream=args.stream)
        msgs.append({"role": "assistant", "content": r["text"] or "(empty)"})
        return r

    print("phase cold ...")
    rows.append(_phase_row("cold", turn("Summarize the topic of this text in one sentence.")))
    print("phase warm ...")
    rows.append(_phase_row("warm", turn("Give one more sentence about it.")))
    print("phase warm2 ...")
    rows.append(_phase_row("warm2", turn("Give a final short sentence.")))

    # Rewrite one early assistant message in place. This is the same
    # operation lean_state applies to prior tool results.
    msgs[2]["content"] = "[prior result of tool(args); re-run for the current value]"
    print("phase mutate ...")
    rows.append(_phase_row("mutate", turn("Give one more short sentence.")))

    for row in rows:
        print(json.dumps(row))
    return {"phases": rows, "prefix_words": args.prefix_words, "history_turns": args.history_turns}


def build_scripted_history(seed: int, prefix_words: int, turns: int) -> list[list[dict]]:
    """Build a scripted tool-using conversation, one block list per turn.

    Each turn has the shape nanobot produces: user question, assistant
    tool call, tool result, assistant answer. All content is synthetic.
    The script is deterministic, so both replay arms see identical bytes.
    """
    blocks = [[{"role": "system", "content": filler_text(prefix_words, seed)}]]
    for t in range(turns):
        call_id = f"call_{t}"
        args = json.dumps({"query": f"topic {t} " + " ".join(filler_text(4, seed + t).split())})
        blocks.append([
            {"role": "user", "content": f"Question {t}: " + filler_text(30, seed + 10 + t)},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": "memory_search", "arguments": args},
            }]},
            {"role": "tool", "tool_call_id": call_id,
             "content": f"Result {t}: " + filler_text(150, seed + 100 + t)},
            {"role": "assistant", "content": f"Answer {t}: " + filler_text(60, seed + 200 + t)},
        ])
    return blocks


def cmd_replay(ep: Endpoint, args) -> dict:
    """Replay the real lean_state transform against an append-only arm.

    Arm A builds each turn's payload with lean_messages() from the agent
    runtime, as production does. Arm B sends the same history verbatim.
    The two arms use different seeds, so they do not share cache blocks.
    Expected result: arm B hits the prefix cache from turn 2 on. Arm A
    misses it on every turn after the first tool result.
    """
    sys.path.insert(0, str(REPO_ROOT / "stacklets" / "agent" / "runtime"))
    from lean_state import lean_messages

    arms = {}
    for arm, (transform, seed) in {
        "lean_state (production)": (lean_messages, args.seed),
        "append-only": (lambda m: m, args.seed + 1000),
    }.items():
        blocks = build_scripted_history(seed, args.prefix_words, args.turns)
        rows = []
        print(f"arm: {arm}")
        for t in range(1, args.turns + 1):
            # The payload at turn t: system + t-1 full turns + this turn's
            # user message. This is the state at the start of a turn.
            history = [m for block in blocks[:t] for m in block]
            payload = transform(history + [blocks[t][0]])
            r = ep.chat(payload, max_tokens=args.max_tokens, stream=args.stream)
            rows.append(_phase_row(f"turn {t}", r))
            print(json.dumps(rows[-1]))
        arms[arm] = rows
    return {"arms": arms, "prefix_words": args.prefix_words, "turns": args.turns}


def append_log(entry_title: str, ep: Endpoint, payload: dict, note: str, result_file: Path):
    """Append one STE summary entry to the improvement log."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"\n## {stamp} - {entry_title}\n"]
    lines.append(f"- endpoint: `{ep.url}`, model: `{ep.model}`")
    lines.append(f"- raw result: `tools/agent-lab/results/{result_file.name}`")
    groups = payload.get("arms") or {"": payload.get("phases") or
                                     [v for v in payload.values() if isinstance(v, dict) and "phase" in v]}
    for group, rows in groups.items():
        if not rows:
            continue
        if group:
            lines.append(f"\n**{group}**")
        lines.append("")
        lines.append("| phase | ttft_s | total_s | prompt_tokens | cached_tokens | completion_tokens |")
        lines.append("|---|---|---|---|---|---|")
        for r in rows:
            u = r.get("usage") or {}
            cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
            lines.append(
                f"| {r['phase']} | {r['ttft_s']} | {r['total_s']} | "
                f"{u.get('prompt_tokens')} | {cached} | {u.get('completion_tokens')} |"
            )
    if note:
        lines.append(f"\n- note: {note}")
    with LOG_PATH.open("a") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"logged to {LOG_PATH.relative_to(REPO_ROOT)}")


def main() -> int:
    defaults = _config_defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=defaults.get("url"), help="OpenAI-compatible base URL")
    parser.add_argument("--key", default=defaults.get("key") or "none")
    parser.add_argument("--model", default=defaults.get("model"))
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--no-stream", dest="stream", action="store_false")
    parser.add_argument("--no-log", dest="log", action="store_false",
                        help="do not append to the improvement log")
    parser.add_argument("--note", default="", help="note for the log entry")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="endpoint sanity check, show raw usage")

    p_cache = sub.add_parser("cache", help="KV-cache experiment: cold/warm/mutate")
    p_cache.add_argument("--prefix-words", type=int, default=3000,
                         help="system prompt size in words (~1.3 tokens per word)")
    p_cache.add_argument("--history-turns", type=int, default=6)
    p_cache.add_argument("--max-tokens", type=int, default=48)
    p_cache.add_argument("--seed", type=int, default=42)

    p_replay = sub.add_parser("replay", help="replay real lean_state vs append-only")
    p_replay.add_argument("--prefix-words", type=int, default=3000)
    p_replay.add_argument("--turns", type=int, default=4)
    p_replay.add_argument("--max-tokens", type=int, default=48)
    p_replay.add_argument("--seed", type=int, default=7)

    args = parser.parse_args()
    if not args.url or not args.model:
        print("error: no endpoint or model. Set --url/--model or stack.toml [ai].", file=sys.stderr)
        return 2

    ep = Endpoint(args.url, args.key, args.model, args.timeout)
    payload = {"probe": cmd_probe, "cache": cmd_cache, "replay": cmd_replay}[args.cmd](ep, args)

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_file = RESULTS_DIR / f"{stamp}-{args.cmd}.json"
    record = {
        "at": stamp,
        "cmd": args.cmd,
        "endpoint": ep.url,
        "model": ep.model,
        "args": {k: v for k, v in vars(args).items() if k not in ("key",)},
        "payload": payload,
    }
    result_file.write_text(json.dumps(record, indent=2) + "\n")
    print(f"raw result: {result_file.relative_to(REPO_ROOT)}")

    if args.log:
        append_log(f"lab {args.cmd}", ep, payload, args.note, result_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
