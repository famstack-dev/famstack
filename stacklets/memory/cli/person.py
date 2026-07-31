"""stack memory person <name> - read a household member's profile.

Person pages are first-class vault entities at `<slug>/about.md`. This command
is the exact read surface for identity/profile questions, parallel to
`stack memory topic <name>` for shared topics. It reads the vault directly, so
the answer is deterministic and carries the source path the agent must cite.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    brain_path_for,
    load_persons_from_vault,
    refresh_vault_if_stale,
    vault_path_for,
)


HELP = "Read a household member profile"

_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_GEN_MARKER = re.compile(r"<!--\s*(?:begin|end):\s*generated\s*-->\n?")
_CITE = re.compile(r"\[\d+(?:,\s*\d+)*\]")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stack memory person",
        description=HELP,
    )
    p.add_argument("name", nargs="*", help="person slug, canonical name, or synonym")
    p.add_argument(
        "--vault", default=None, metavar="PATH",
        help="vault path override (defaults to <data_dir>/memory/vault/)",
    )
    p.add_argument(
        "--no-refresh", action="store_true",
        help="skip the upstream-HEAD check before reading",
    )
    return p


def _roots(config, override: str | None) -> list[Path]:
    """Where a person page can live, most authoritative first.

    Two trees hold `<slug>/about.md` and they mean different things.
    memory is source: what the household actually wrote. The brain is a
    projection the wiki pass generates and the installer purges out of
    source again ("purged 1 generated source page(s)"), so a generated
    profile exists *only* there.

    Reading source alone therefore answered "no profile" for every
    member who had one, which is the state this command shipped in.
    Source is still checked first: a hand-curated page beats rebuildable
    output. `_todos.py` consults both roots for the same reason.
    """
    if override:
        return [Path(override)]
    data_dir = config.get("data_dir") if config else None
    if not data_dir:
        return []
    base = Path(data_dir)
    return [vault_path_for(base), brain_path_for(base)]


def _clean_profile(text: str) -> str:
    text = _FRONTMATTER.sub("", text)
    text = _GEN_MARKER.sub("", text)
    text = _CITE.sub("", text)
    return text.strip()


def _resolve_person(vault: Path, query: str):
    q = query.strip().lower()
    for person in load_persons_from_vault(vault):
        names = [person.slug, person.canonical, *person.synonyms]
        if any(str(name).strip().lower() == q for name in names):
            return person
    return None


def _known_people(vault: Path) -> list[str]:
    return [p.slug for p in load_persons_from_vault(vault)]


def run(args, stacklet, config):
    try:
        ns = _parser().parse_args(args or [])
    except SystemExit as e:
        return {"error": "usage: stack memory person <name>"} if e.code else {"ok": True}

    name = " ".join(ns.name).strip()
    if not name:
        return {"error": "usage: stack memory person <name>"}

    roots = [r for r in _roots(config, ns.vault) if r.exists()]
    if not roots:
        return {"error": "no vault found - is the memory stacklet installed?"}
    if not ns.no_refresh:
        refresh_vault_if_stale(roots[0])

    fallback = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    path = None
    for root in roots:
        person = _resolve_person(root, name)
        candidate = root / (person.slug if person else fallback) / "about.md"
        if candidate.exists():
            path = candidate
            break
    if path is None:
        # Names from every root, so the hint lists everyone the household
        # could have asked for, not just those in the first tree checked.
        known = sorted({slug for root in roots for slug in _known_people(root)})
        hint = ("  people: " + ", ".join(known)) if known else ""
        return {"error": f"no profile for person {name!r}\n{hint}".rstrip()}

    text = _clean_profile(path.read_text(encoding="utf-8"))
    rel = f"{path.parent.name}/about.md"
    print(f"Source: vault/{rel}\n")
    print(text)
    return {"ok": True, "person": path.parent.name, "path": rel}
