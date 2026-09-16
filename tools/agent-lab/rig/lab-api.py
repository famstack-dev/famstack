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
import importlib.util
import sys
import urllib.request
from pathlib import Path

ARGS = None

# Run the real store transform, so the rig cannot drift from production.
# The transforms are pure; the module also imports store deps at load
# time, so put their paths on sys.path before executing it.
_REPO = Path(__file__).resolve().parents[3]
for _p in ("lib", "stacklets/memory", "stacklets/memory/cli"):
    sys.path.insert(0, str(_REPO / _p))
_spec = importlib.util.spec_from_file_location(
    "prod_list_edit", _REPO / "stacklets" / "memory" / "cli" / "list-edit.py")
_prod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prod)
_list_edit_transform = _prod.apply_list_edit
_list_edit_batch = _prod.apply_list_edits

# The ranked engine, for the `--backend fts5` arm. Imported from the
# stacklet rather than reimplemented, for the same reason the list-edit
# transform is: an A/B against a copy of the engine measures the copy.
import fts_index  # noqa: E402


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


# Coverage below this and the gated backend says it found nothing,
# rather than handing over its best guess. Measured in
# tools/retrieval-lab; see docs/design/brain/retrieval-engine-poc.md.
GATE = 0.40


def _block(vault: Path, rel: str, pattern) -> str:
    """One result, in the shape the agent has always been shown.

    Identical across backends on purpose. The A/B is about which pages
    come back and in what order; changing how they are rendered would
    change the model's reading of them too, and then the comparison
    would be measuring two things at once.
    """
    body = _body_only((vault / rel).read_text())
    lines = [ln for ln in body.splitlines() if pattern.search(ln)]
    return f"— vault/{rel}\n" + "\n".join(f"  {ln}" for ln in lines[:3])


def _keywords_for(ns) -> list[str]:
    """The search terms, resolved the same way for every backend."""
    if ns.nl and len(ns.query.split()) > 1:
        return _rewrite_keywords(ns.query)
    return ns.query.split()


def _search_regex(ns, vault: Path, pattern) -> tuple[str, int]:
    """Today's engine: every file matched, newest first."""
    hits = []
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault)
        if ns.scope and not str(rel).startswith(ns.scope):
            continue
        text = path.read_text()
        if any(pattern.search(ln) for ln in _body_only(text).splitlines()):
            m = re.search(r"^date:\s*(\S+)", text, re.MULTILINE)
            hits.append((m.group(1) if m else "", _block(vault, str(rel), pattern)))
    if not hits:
        return "no results\n", 1
    hits.sort(key=lambda h: h[0], reverse=True)
    return "\n".join(h[1] for h in hits[: ns.limit]) + "\n", 0


def _search_ranked(ns, vault: Path, keywords, pattern,
                   gated: bool) -> tuple[str, int]:
    """The ranked engine: best match first, optionally behind a gate."""
    db = vault.parent / "vault-index.sqlite3"
    fts_index.build_index(vault, db)
    hits = fts_index.search(
        db, keywords, scopes=[ns.scope] if ns.scope else None,
        limit=ns.limit, substrings=True)
    if not hits:
        return "no results\n", 1
    if gated:
        covered = len(hits[0].matched) / max(len(keywords), 1)
        if covered < GATE:
            # The honest answer when the best page carries almost none
            # of what was asked. Says so plainly rather than handing
            # over a neighbour for the model to answer from.
            return ("no confident match: the closest pages do not contain "
                    "what you asked about\n"), 1
    return "\n".join(_block(vault, h.rel, pattern) for h in hits) + "\n", 0


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

    keywords = _keywords_for(ns)
    if ns.nl and len(ns.query.split()) > 1:
        pattern = re.compile("|".join(re.escape(t) for t in keywords),
                             re.IGNORECASE)
    else:
        # The real CLI treats the query as a regex (lib.py search engine).
        # Keep that contract; fall back to a literal match on a bad regex.
        try:
            pattern = re.compile(ns.query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(ns.query), re.IGNORECASE)

    vault = Path(ARGS.vault)
    if ARGS.backend == "regex":
        return _search_regex(ns, vault, pattern)
    return _search_ranked(ns, vault, keywords, pattern,
                          gated=ARGS.backend == "fts5+gate")


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


_BOX = re.compile(r"^\s*- \[([ xX])\]\s*(.+?)\s*$")


def _boxes(text: str) -> dict[str, bool]:
    """Map checkbox item text -> done state."""
    out = {}
    for ln in text.splitlines():
        if m := _BOX.match(ln):
            out[m.group(2)] = m.group(1).lower() == "x"
    return out


def _diff_sentence(old: str, new: str, page: str) -> str:
    """Describe what a write actually changed, like the real store does."""
    ob, nb = _boxes(old), _boxes(new)
    ticked = [t for t in ob if t in nb and not ob[t] and nb[t]]
    unticked = [t for t in ob if t in nb and ob[t] and not nb[t]]
    added = [t for t in nb if t not in ob]
    removed = [t for t in ob if t not in nb]
    parts = []
    if ticked:
        parts.append(f"ticked off {len(ticked)}: " + "; ".join(ticked))
    if unticked:
        parts.append(f"unticked {len(unticked)}: " + "; ".join(unticked))
    if added:
        parts.append(f"added {len(added)}: " + "; ".join(added))
    if removed:
        parts.append(f"REMOVED {len(removed)}: " + "; ".join(removed))
    return "; ".join(parts) if parts else f"updated {page}"


def memory_write(argv: list[str]) -> tuple[str, int]:
    parser = argparse.ArgumentParser(prog="stack memory write", add_help=False)
    parser.add_argument("page")
    parser.add_argument("--by", default="someone")
    parser.add_argument("--patch", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    try:
        ns = parser.parse_args(argv)
    except SystemExit:
        return parser.format_usage(), 2

    buffer = Path(ARGS.buffer)
    if not buffer.exists():
        return "no write buffer\n", 3
    payload = buffer.read_text()
    path = Path(ARGS.vault) / ns.page
    old = path.read_text() if path.exists() else ""

    if ns.patch:
        try:
            edits = json.loads(payload)
        except json.JSONDecodeError as e:
            return f"patch is not valid JSON: {e}\n", 2
        new = old
        for edit in edits:
            old_text = edit.get("old_text") or ""
            if old_text and old_text not in new:
                return (f"old_text was not found in {ns.page}; the page has "
                        f"changed. Read it again and patch what is there "
                        f"now.\n"), 1
            if old_text:
                new = new.replace(old_text, edit.get("new_text") or "", 1)
            else:
                new = new + (edit.get("new_text") or "")
    else:
        new = payload

    # Same frontmatter guard as the production write seam.
    if new.lstrip().startswith("---") and not new.startswith("---\n"):
        return (f"{ns.page} must start with '---' at column one; the write "
                "begins with whitespace before the frontmatter. Re-send the "
                "page with the frontmatter block exactly as you read it.\n"), 1
    if old.startswith("---\n") and not new.startswith("---\n"):
        return (f"{ns.page} has frontmatter and this write drops it. Keep "
                "the frontmatter block exactly as you read it.\n"), 1

    # Same restructure guard as the production write seam: a whole-page
    # rewrite of a list must not reopen or remove items.
    if not ns.patch and ns.page.endswith("todos.md"):
        ob, nb = _boxes(old), _boxes(new)
        lost = [t for t in ob if (t in nb and ob[t] and not nb[t]) or t not in nb]
        if lost:
            return (f"this rewrite reopens or removes items: "
                    f"{'; '.join(lost)}. A restructure keeps every item and "
                    "every [x]. Resend the full page with them unchanged. To "
                    "reopen or remove an item on purpose, use list_edit.\n"), 1

    sentence = _diff_sentence(old, new, ns.page)
    if ns.dry_run:
        return f"dry run: {sentence}\n", 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    env = {"GIT_AUTHOR_NAME": ns.by, "GIT_AUTHOR_EMAIL": f"{ns.by}@demo.invalid",
           "GIT_COMMITTER_NAME": ns.by, "GIT_COMMITTER_EMAIL": f"{ns.by}@demo.invalid"}
    subprocess.run(["git", "add", ns.page], cwd=ARGS.vault, env=env, timeout=10)
    subprocess.run(["git", "commit", "-q", "-m", f"docs(memory): {sentence[:60]}"],
                   cwd=ARGS.vault, env=env, timeout=10)
    return sentence + _link_line(ns.page) + "\n", 0


def _link_line(page: str) -> str:
    """Rig stand-in for the production wiki link line on touched pages."""
    parts = page.strip("/").split("/")
    if parts[-1] == "todos.md":
        return f"\n  https://rig.invalid/go/topic/{'/'.join(parts[:-1])}/todo"
    if parts[-1] == "about.md":
        return f"\n  https://rig.invalid/go/topic/{'/'.join(parts[:-1])}"
    return ""


def _commit(page: str, actor: str, sentence: str) -> None:
    env = {"GIT_AUTHOR_NAME": actor, "GIT_AUTHOR_EMAIL": f"{actor}@demo.invalid",
           "GIT_COMMITTER_NAME": actor, "GIT_COMMITTER_EMAIL": f"{actor}@demo.invalid"}
    subprocess.run(["git", "add", page], cwd=ARGS.vault, env=env, timeout=10)
    subprocess.run(["git", "commit", "-q", "-m", f"docs(memory): {sentence[:60]}"],
                   cwd=ARGS.vault, env=env, timeout=10)


def memory_list_edit(argv: list[str]) -> tuple[str, int]:
    """Item-level and bulk list operations.

    Runs the production transform (stacklets/memory/cli/list-edit.py)
    so the rig cannot drift from the real store contract.
    """
    parser = argparse.ArgumentParser(prog="stack memory list-edit", add_help=False)
    parser.add_argument("page")
    parser.add_argument("--op", required=True,
                        choices=["add", "tick", "untick", "remove",
                                 "clear-done", "reset"])
    parser.add_argument("--item", action="append", default=[])
    parser.add_argument("--section", default=None)
    parser.add_argument("--by", default="someone")
    try:
        ns = parser.parse_args(argv)
    except SystemExit:
        return parser.format_usage(), 2

    path = Path(ARGS.vault) / ns.page
    if not path.exists():
        return f"no such page: {ns.page}\n", 1
    items = [i.strip() for i in ns.item if i.strip()]
    text = path.read_text()
    if ns.op in ("clear-done", "reset"):
        new, sentence, kind = _list_edit_transform(text, ns.op, "", ns.section)
    else:
        new, sentence, kind = _list_edit_batch(text, ns.op, items, ns.section)

    if kind == "refuse":
        return sentence + "\n", 1
    if kind == "noop":
        return sentence + "\n", 0
    path.write_text(new)
    _commit(ns.page, ns.by, sentence)
    return sentence + _link_line(ns.page) + "\n", 0


def dispatch(line: str) -> tuple[str, int]:
    try:
        argv = shlex.split(line)
    except ValueError as e:
        return f"parse error: {e}\n", 2
    if len(argv) >= 2 and argv[0] == "memory":
        handlers = {"search": memory_search, "person": memory_person,
                    "history": memory_history, "write": memory_write,
                    "list-edit": memory_list_edit}
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
    parser.add_argument("--buffer", default=str(
        Path(__file__).parent / "state" / "nanobot" / ".write-buffer"))
    parser.add_argument("--llm", default="http://localhost:8888/v1")
    parser.add_argument("--key", default="none")
    parser.add_argument("--model", default=None)
    parser.add_argument("--backend", default="regex",
                        choices=["regex", "fts5", "fts5+gate"],
                        help="search engine to serve: the shipping regex "
                             "walk, the ranked index, or the ranked index "
                             "behind a confidence gate")
    ARGS = parser.parse_args()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("127.0.0.1", ARGS.listen), Handler)
    print(f"lab-api: 127.0.0.1:{ARGS.listen}, vault {ARGS.vault}")
    server.serve_forever()


if __name__ == "__main__":
    main()
