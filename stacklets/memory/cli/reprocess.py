"""stack memory reprocess: refile one note under today's rules, previewed first.

    stack memory reprocess note-7f3a9c21e4 [--by lisa]
    stack memory reprocess note-7f3a9c21e4 --msg "the budget is 250, not 200" --by marge
    stack memory reprocess --apply rp-3f9a1c2b7d4e

A note filed months ago was filed under the rules of the day. This files
it again under today's, from its source: the words it was sent with and
the replies it keeps, never its summary (ADR-010). Notes only, one at a
time.

PREVIEW, THEN APPLY
    The first form writes nothing to the family memory. It prints the card
    as it is and as it would be, both the way the card renderer shows them,
    the diff of the card and its sidecar, and a proposal id. `--apply`
    commits exactly the previewed files, with no second model call, as one
    commit `reprocess: <title>` that names the commit it replaced, so one
    `git revert` undoes it. The proposal is kept beside the memory, never in
    it, so a preview nobody applies leaves no trace in its history.

    The model runs in the bot runner, which only composes the files. The
    preview reads the card and its sidecar from Forgejo and keeps the blob
    hashes it read them at; the apply runs here and commits both through
    the memory's one write seam for a reviewed card change (`lib.write_card`),
    which refuses when either is at another hash by then.

    The refile reads the note's sources (ADR-018), never its summary: the
    ones the card keeps, then replies in its message's thread the card does
    not hold yet. `--msg` is a correction, appended as a `note` source by
    `--by`, so the next refile reads it again; the commit is theirs, with
    their words in its body.

A PERSON'S EDITS ARE KEPT
    A note somebody edited by hand is rebuilt as a card conversation
    (ADR-018 §7): the clean version it had before the first correction or
    edit, then each of them in time order, read from the sources and from
    the card's history in the local memory (`card_history`). A note with
    corrections alone is rewritten from its words by the correction prompt. A person's edit of the fields (a tag added, a title
    set) is applied again after the model, as the operation it was
    (`field_ops`), by the model half, where a correction in the chat
    applies it too; one that no longer fits the card is left out and named.

WHO THE COMMIT IS BY
    A refile without `--msg` is a machine rebuild: the memory bot commits
    it, as it commits a migration, with `Requested-By:` naming who asked,
    and the sidecar's `made.requested_by` names the same person. That is
    `--by <person>`, or `admin`, the admin at the terminal, when none is
    given (as `stack core schedule add --by`). A refile with `--msg` carries
    a person's words, so the commit is theirs (ADR-018 § 6, ADR-015).

WHAT IS REFUSED, AND WHY
    A card that is not a note with sources (a sidecar, or a `capture_id` in
    the format before ADR-018): only such a note has a source to refile
    from. A note without its words, or whose words carry prompt text an
    older reprocess left in them. And an apply when the card changed after the preview, or
    the local copy of the memory has uncommitted changes or is not at its
    remote's head: the preview was of a card that is no longer the one.

`--json` gives the card browser what it shows: `current` and `proposed`,
each as `file`, `rendered` and `sidecar`, the `diff`, the `proposal_id`,
`sources`, what the model read (`memory.note_card`: each sidecar
record with its `quote`), and `left_out`, the field edits that no longer
fit; a refusal is `error` with a `refusal` code.
Exit codes: 0 previewed or applied, 1 refused.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    BOT_USERNAME,
    read_memory_file,
    vault_local_head,
    vault_path_for,
    vault_remote_head,
    write_card,
)

from stack.bot_runner import call  # noqa: E402
from stack.card_commits import commit_subject  # noqa: E402
from stack.card_lookup import find_by_card_id  # noqa: E402
from stack.card_render import render_file  # noqa: E402
from stack.people import load_people  # noqa: E402

from card_history import CardHistory, history_of, read_versions  # noqa: E402
from note_card import is_legacy_note, sidecar_path  # noqa: E402

HELP = "Refile a note under the current rules: preview it, then --apply it"

# The model half runs in the bot runner, beside the capture pipeline.
_ENTRYPOINT = "/stacklets/docs/bot/cli_entrypoint.py"

_USAGE = ('usage: stack memory reprocess <card-id> [--by <person>] [--msg "<text>" --by <person>]\n'
          "       stack memory reprocess --apply <proposal-id>")

# Who asked for a refile when nobody is named: the admin at the terminal.
_ADMIN = "admin"


@dataclass(frozen=True)
class Proposal:
    """A previewed refile: the file it would write and the file it was made from.

    `base_sha` is Forgejo's blob hash of the card the preview read, and
    `sidecar_sha` of its sidecar; the apply writes `after` and
    `after_sidecar` only while both are still at them.

        proposal = Proposal("rp-3f9a1c2b7d4e", "note-7f3a9c21e4", path, after,
                            base_sha="9d4c…", replaces="a1b2c3…", title="Trip to-dos")
    """

    id: str
    card_id: str
    path: str
    after: str
    base_sha: str
    replaces: str
    title: str
    msg: str = ""
    # Who asked for it; with `msg`, who wrote the correction.
    by: str = ""
    # The sidecar it would write, and the hash of the one it was made from
    # ("" for a note in the old format, which had none).
    after_sidecar: str = ""
    sidecar_sha: str = ""


class Refusal(Exception):
    """Why a note is not refiled: a stable `code` and a sentence for people.

        Refusal("behind", "the local memory is not at its remote's head").envelope()
        # {'error': "the local memory is not at its remote's head", 'refusal': 'behind'}
    """

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason

    def envelope(self) -> dict:
        return {"error": self.reason, "refusal": self.code}


def run(args, stacklet, config):
    argv = list(args or [])
    apply_id, argv = _opt(argv, "--apply")
    msg, argv = _opt(argv, "--msg")
    by, argv = _opt(argv, "--by")
    refs = [a for a in argv if not a.startswith("-")]
    data_dir = Path(config["data_dir"]) if config and config.get("data_dir") else None
    if data_dir is None:
        return {"error": "no data_dir configured"}
    if apply_id and (refs or msg or by):
        return {"error": "--apply takes a proposal id alone: the card, --msg and --by "
                         "were given to the preview it applies"}
    if apply_id:
        return _answer(lambda: apply(apply_id, config, data_dir))
    if msg and not by:
        return {"error": "--msg needs --by: a correction is somebody's"}
    if len(refs) != 1:
        return {"error": _USAGE}
    return _answer(lambda: preview(refs[0], config, data_dir, msg=msg or "", by=by or _ADMIN))


def _answer(step) -> dict:
    try:
        return step()
    except Refusal as refused:
        return refused.envelope()


# ── Preview ───────────────────────────────────────────────────────────

def preview(card_id: str, config: dict, data_dir: Path, *, msg: str = "", by: str = _ADMIN) -> dict:
    """Propose the refile of note `card_id`, store the proposal, write nothing.

    The local copy of the memory is checked first, as the apply checks it:
    its history is where a person's edits of the card are read from. The
    card itself is read from Forgejo, with the hash the apply is based on.
    """
    vault = vault_path_for(data_dir)
    vault_refusal(vault)
    found = find_by_card_id(card_id, root=vault)
    if found is None:
        raise Refusal("no-card", f"no card {card_id}; find one with `stack memory find`")
    side_path = sidecar_path(found.path)
    note_refusal(found.fields, (vault / found.path).read_text(encoding="utf-8"),
                 has_sidecar=(vault / side_path).is_file())
    history = file_history(vault, found.path)
    edited = history_of(read_versions(vault, found.path, card_id=found.card_id))
    read = read_memory_file(config, found.path)
    if "error" in read:
        raise Refusal("unreachable", read["error"])
    before = read["content"]
    side = read_memory_file(config, side_path)
    side_before = side.get("content", "")

    answer = _propose(found.path, before, side_before, msg=msg, by=by, history=edited)
    after, side_after = answer["proposed"], answer.get("proposed_sidecar") or ""
    left_out = [str(line) for line in answer.get("left_out") or []]
    proposal = Proposal(
        id=proposal_id(found.path, before + side_before, after + side_after),
        card_id=found.card_id, path=found.path,
        after=after, base_sha=read["sha"], replaces=history[0][0] if history else "",
        title=answer.get("title") or str(found.fields.get("title") or ""),
        msg=msg, by=by.split(":")[0].lstrip("@"),
        after_sidecar=side_after, sidecar_sha=side.get("sha", ""),
    )
    _store(data_dir, proposal)

    names, language = _names(vault, config), _language(config)
    current = render_file(before, names=names, language=language).markdown
    proposed = render_file(after, names=names, language=language).markdown
    diff = file_diff(found.path, before, after) + file_diff(side_path, side_before, side_after)
    print(f"{found.card_id} ({found.path})\n\n── Now ──\n\n{current}\n── Proposed ──\n\n"
          f"{proposed}\n── Diff ──\n\n{diff}\n")
    sources = answer.get("sources") or []
    print("── Read by the model ──\n\n" + "\n".join(_source_line(s) for s in sources) + "\n")
    if answer.get("clean_rejected"):
        print(f"No clean version: the model's {answer['clean_rejected']}.")
    for line in left_out:
        print(f"A field edit left out, it no longer fits: {line}.")
    print(f"Nothing written. Apply it with `stack memory reprocess --apply {proposal.id}`.")
    return {
        "card_id": found.card_id, "path": found.path, "proposal_id": proposal.id,
        "current": {"file": before, "rendered": current, "sidecar": side_before},
        "proposed": {"file": after, "rendered": proposed, "sidecar": side_after},
        "diff": diff, "clean_rejected": answer.get("clean_rejected") or "",
        "sources": sources, "left_out": left_out,
    }


def _propose(path: str, text: str, sidecar: str, *, msg: str, by: str,
             history: CardHistory) -> dict:
    """The proposed card and sidecar, from the model half in the bot runner.

    The history is read here, from the local clone, which follows a move
    of the card; the clean version the card conversation starts from, a
    person's edits of it and their field edits go with the request.
    """
    request = json.dumps({"path": path, "text": text, "sidecar": sidecar, "msg": msg, "by": by,
                          "prior": history.prior,
                          "edits": [e.to_json() for e in history.edits],
                          "ops": [o.to_json() for o in history.ops]})
    try:
        answer = json.loads(call(_ENTRYPOINT, "reprocess-note", input_text=request))
    except (RuntimeError, ValueError) as e:
        raise Refusal("no-proposal", f"no proposal could be made: {e}") from e
    if answer.get("refused"):
        raise Refusal("words", answer["refused"])
    return answer


def _source_line(source: dict) -> str:
    """One source as a line: what it is, who, when, on which channel, and its quote."""
    return (f"{source.get('role')} by {source.get('person') or 'someone'} {source.get('time')} "
            f"({source.get('channel')}, {source.get('kind')}): "
            f"{' '.join(str(source.get('quote') or '').split())}")


def note_refusal(fields: dict, text: str, *, has_sidecar: bool) -> None:
    """Refuse a card that is not a note with sources: nothing to replay.

    A note filed today has a sidecar; one filed before ADR-018 its
    `capture_id` and its words in the old sections. A note written in the
    memory has neither, and is the household's.
    """
    if str(fields.get("type") or "") != "note":
        raise Refusal("not-a-note", f"it is a {fields.get('type') or 'card without a type'}; "
                                    "only notes are refiled for now")
    if not (has_sidecar or (is_legacy_note(text) and fields.get("capture_id"))):
        raise Refusal("no-sources", "the note has no sources to refile from; "
                                    "it was written in the memory, not filed")


def file_history(vault: Path, path: str) -> list[tuple[str, str, str]]:
    """`(sha, author, subject)` of each commit to `path`, newest first, across renames."""
    out = subprocess.run(
        ["git", "-C", str(vault), "log", "--follow", "--format=%H%x1f%an%x1f%s", "--", path],
        capture_output=True, text=True, timeout=60,
    ).stdout
    fields = (line.split("\x1f", 2) for line in out.splitlines() if line.count("\x1f") == 2)
    return [(sha, who, subject) for sha, who, subject in fields]


def proposal_id(path: str, before: str, after: str) -> str:
    """The same refile of the same card gets the same id."""
    digest = hashlib.sha256("\0".join((path, before, after)).encode()).hexdigest()
    return f"rp-{digest[:12]}"


def file_diff(path: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))


# ── Apply ─────────────────────────────────────────────────────────────

def apply(proposal_ref: str, config: dict, data_dir: Path) -> dict:
    """Commit exactly the previewed file, or refuse when it no longer fits.

    No model runs here: the file is the stored one, written through
    `write_card` against the hash the preview read.
    """
    proposal = _load(data_dir, proposal_ref)
    vault = vault_path_for(data_dir)
    vault_refusal(vault)
    result = write_card(config, proposal.card_id, proposal.after, base_sha=proposal.base_sha,
                        sidecar=proposal.after_sidecar or None,
                        sidecar_sha=proposal.sidecar_sha,
                        actor=proposal.by if proposal.msg else BOT_USERNAME,
                        message=commit_message(proposal))
    if result.get("moved"):
        raise Refusal("changed", f"the card changed after the preview ({result['error']}); "
                                 "preview it again")
    if "error" in result:
        raise Refusal("write-failed", str(result["error"]))
    _proposal_file(data_dir, proposal.id).unlink(missing_ok=True)
    commit = vault_local_head(vault) or ""
    print(f"Refiled {proposal.card_id} ({proposal.path}) as {commit[:7] or 'a new commit'}, "
          f"replacing {proposal.replaces[:7]}. `git revert {commit[:7]}` undoes it.")
    return {"ok": True, "committed": bool(result.get("committed")), "card_id": proposal.card_id,
            "path": proposal.path, "commit": commit, "replaces": proposal.replaces}


def vault_refusal(vault: Path) -> None:
    """Refuse when the local copy of the memory is not the memory: changed or behind."""
    status = subprocess.run(["git", "-C", str(vault), "status", "--porcelain"],
                            capture_output=True, text=True, timeout=30)
    if status.returncode != 0:
        raise Refusal("no-vault", f"{vault} is no git repository")
    # Each line is two status letters, a space and the path; the first
    # letter is often a space, so the output is not stripped.
    if changed := [line[3:] for line in status.stdout.splitlines() if line.strip()]:
        files = ", ".join(changed[:3])
        raise Refusal("uncommitted", f"the memory has uncommitted changes ({files}); "
                                     "commit or discard them first")
    remote = vault_remote_head(vault)
    if remote is None:
        raise Refusal("unreachable", "the memory's remote cannot be reached to check it is current")
    if remote != vault_local_head(vault):
        raise Refusal("behind", "the local memory is not at its remote's head; "
                                "`stack memory pull`, then preview again")


def commit_message(proposal: Proposal) -> str:
    """`reprocess: <title>`, the commit it replaces, and who asked or what they said.

    A refile is derived and may be redone: the memory bot's commit, naming
    who asked for it. One that carries a correction is a person's change:
    their commit, their words in the body (ADR-018 §6).
    """
    lines = [commit_subject("reprocess", proposal.title), ""]
    if proposal.msg:
        lines += [proposal.msg, "", f"Replaces: {proposal.replaces}"]
    else:
        lines += [f"Replaces: {proposal.replaces}", f"Requested-By: {proposal.by}"]
    return "\n".join(lines) + "\n"


# ── Proposals, beside the memory ──────────────────────────────────────

def _proposal_file(data_dir: Path, ref: str) -> Path:
    return data_dir / "memory" / "proposals" / f"{Path(ref).name}.json"


def _store(data_dir: Path, proposal: Proposal) -> None:
    path = _proposal_file(data_dir, proposal.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(proposal)), encoding="utf-8")


def _load(data_dir: Path, ref: str) -> Proposal:
    try:
        return Proposal(**json.loads(_proposal_file(data_dir, ref).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as e:
        raise Refusal("no-proposal", f"no proposal {ref}; preview the card first") from e


# ── Small parts ───────────────────────────────────────────────────────

def _names(vault: Path, config: dict) -> dict[str, str]:
    shared = ((config or {}).get("stack", {}).get("memory", {}) or {}).get("shared_bucket", "family")
    return {p.id: p.first_name for p in load_people(vault, shared) or []}


def _language(config: dict) -> str:
    return ((config or {}).get("stack", {}).get("core", {}) or {}).get("language") or "en"


def _opt(argv: list[str], flag: str) -> tuple[str | None, list[str]]:
    """Pull `--flag value` out of argv, returning (value, remaining)."""
    out, value, i = [], None, 0
    while i < len(argv):
        if argv[i] == flag and i + 1 < len(argv):
            value = argv[i + 1]
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return value, out
