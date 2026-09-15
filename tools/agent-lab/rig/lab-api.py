#!/usr/bin/env python3
"""Lab replacement for the famstack API, for the isolated rig only.

The rig agent's `stack` shim sends one shlex line over TCP. This server
answers `stack memory ...` commands from the demo vault. It never talks
to the production API, Forgejo, or the family vault.

Protocol: read one line until EOF. Send plain text back. On a non-zero
exit, append a trailing `stack-exit: N` line. This copies the contract
of stacklets/core/famstack-api.py.

The `--nl` flag makes one keyword-rewrite call to the LLM endpoint,
like the production search path does. This keeps the latency shape.

Usage: lab-api.py [--listen 42011] [--vault DIR] [--llm URL] [--model M]
"""

import argparse
import json
import re
import shlex
import socketserver
import subprocess
import urllib.request
from pathlib import Path

ARGS = None


def _body_only(text: str) -> str:
    """Strip YAML frontmatter, so field names do not match every page."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def _rewrite_keywords(query: str) -> list[str]:
    """One LLM call that turns a question into 2-4 literal keywords."""
    body = {
        "model": ARGS.model,
        "messages": [{"role": "user", "content":
                      "Give 2 to 4 literal search keywords for this query. "
                      "Reply with the keywords only, separated by spaces.\n\n"
                      f"Query: {query}"}],
        "max_tokens": 32,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        ARGS.llm.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {ARGS.key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = json.load(resp)["choices"][0]["message"]["content"] or ""
        words = [w.strip(".,;:!?\"'") for w in text.split()]
        return [w for w in words if w][:4] or query.split()
    except Exception:
        return query.split()


def memory_search(argv: list[str]) -> tuple[str, int]:
    parser = argparse.ArgumentParser(prog="stack memory search", add_help=False)
    parser.add_argument("query")
    parser.add_argument("--nl", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--scope", default=None)
    parser.add_argument("--person", default=None)
    parser.add_argument("--tag", default=None)
    try:
        ns = parser.parse_args(argv)
    except SystemExit:
        return parser.format_usage(), 2

    if ns.nl and len(ns.query.split()) > 1:
        terms = _rewrite_keywords(ns.query)
        pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    else:
        # The real CLI treats the query as a regex (lib.py search engine).
        # Keep that contract; fall back to a literal match on a bad regex.
        try:
            pattern = re.compile(ns.query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(ns.query), re.IGNORECASE)

    vault = Path(ARGS.vault)
    hits = []
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault)
        if ns.scope and not str(rel).startswith(ns.scope):
            continue
        text = path.read_text()
        body = _body_only(text)
        lines = [ln for ln in body.splitlines() if pattern.search(ln)]
        if lines:
            m = re.search(r"^date:\s*(\S+)", text, re.MULTILINE)
            date = m.group(1) if m else ""
            hits.append((date, f"— vault/{rel}\n"
                         + "\n".join(f"  {ln}" for ln in lines[:3])))
    if not hits:
        return "no results\n", 1
    # The real CLI sorts by frontmatter date, newest first, then limits.
    hits.sort(key=lambda h: h[0], reverse=True)
    return "\n".join(h[1] for h in hits[: ns.limit]) + "\n", 0


def memory_person(argv: list[str]) -> tuple[str, int]:
    if not argv:
        return "usage: stack memory person <name>\n", 2
    page = Path(ARGS.vault) / argv[0].lower() / "about.md"
    if not page.exists():
        return f"no page for {argv[0]}\n", 1
    return page.read_text(), 0


def memory_history(argv: list[str]) -> tuple[str, int]:
    cmd = ["git", "-C", ARGS.vault, "log", "--oneline", "-n", "10"]
    if argv:
        cmd += ["--", argv[0]]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return (r.stdout or "no history\n"), (0 if r.returncode == 0 else 1)


def dispatch(line: str) -> tuple[str, int]:
    try:
        argv = shlex.split(line)
    except ValueError as e:
        return f"parse error: {e}\n", 2
    if len(argv) >= 2 and argv[0] == "memory":
        handlers = {"search": memory_search, "person": memory_person,
                    "history": memory_history}
        if argv[1] in handlers:
            return handlers[argv[1]](argv[2:])
    return f"lab-api: command not allowed in the rig: {' '.join(argv[:2])}\n", 126


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.read().decode("utf-8", "replace").strip()
        print(f"lab-api: {line}")
        text, code = dispatch(line)
        if code != 0:
            text = text + f"stack-exit: {code}\n"
        self.wfile.write(text.encode())


def main():
    global ARGS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", type=int, default=42011)
    parser.add_argument("--vault", default=str(Path(__file__).parent / "state" / "vault"))
    parser.add_argument("--llm", default="http://localhost:8888/v1")
    parser.add_argument("--key", default="none")
    parser.add_argument("--model", default=None)
    ARGS = parser.parse_args()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("127.0.0.1", ARGS.listen), Handler)
    print(f"lab-api: 127.0.0.1:{ARGS.listen}, vault {ARGS.vault}")
    server.serve_forever()


if __name__ == "__main__":
    main()
