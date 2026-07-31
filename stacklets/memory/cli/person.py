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
from lib import load_persons_from_vault, refresh_vault_if_stale, vault_path_for  # noqa: E402


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


def _vault(config, override: str | None) -> Path | None:
    if override:
        return Path(override)
    data_dir = config.get("data_dir") if config else None
    return vault_path_for(Path(data_dir)) if data_dir else None


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

    vault = _vault(config, ns.vault)
    if vault is None or not vault.exists():
        return {"error": "no vault found - is the memory stacklet installed?"}
    if not ns.no_refresh:
        refresh_vault_if_stale(vault)

    person = _resolve_person(vault, name)
    slug = person.slug if person else re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    path = vault / slug / "about.md"
    if not path.exists():
        known = _known_people(vault)
        hint = ("  people: " + ", ".join(known)) if known else ""
        return {"error": f"no profile for person {name!r}\n{hint}".rstrip()}

    text = _clean_profile(path.read_text(encoding="utf-8"))
    rel = f"{path.parent.name}/about.md"
    print(f"Source: vault/{rel}\n")
    print(text)
    return {"ok": True, "person": path.parent.name, "path": rel}
