"""Shared by `find`, `card`, `links` and `check-format`: the graph of the family memory.

Also the one home of the audience for every memory command that takes
`--audience` (`search`, `person show`, `history`, `list show`, `topic`): it
parses the option the same way for all of them, and decides with the memory's
one rule (`stack.cards.audience.readable`, which `graph` applies) which pages
the people listening may read.

    take_audience(["gift", "--audience", "bart"])   (["gift"], frozenset({"bart"}))
    may_read("marge/notes/gift.md", {"bart"}, config)   False
    scopes_for({"marge"}, config)                   ["family/", "marge/"]

SAFETY: None is the admin's view and reads every page; it is what a command
gets only when no `--audience` was given at all. An empty `--audience` is an
empty audience, which reads only the shared pages.

Its cards are the vault's files; the brain only adds the views compiled about them.
"""

from __future__ import annotations

from pathlib import Path


from ..graph import Graph, load_graph, readable

from ..lib import brain_path_for, vault_path_for


# Who will see the answer: `--audience marge` or `--audience bart,homer`. Only
# what they may all read is in the graph (`stack.cards.audience.readable`).
def take_audience(args: list[str]) -> tuple[list[str], frozenset[str] | None]:
    """`args` without `--audience <people>`, and the audience it named (None: not given)."""
    args = list(args or [])
    if "--audience" not in args:
        return args, None
    i = args.index("--audience")
    value = args[i + 1] if i + 1 < len(args) else ""
    del args[i:i + 2]
    return args, frozenset(p.strip().lower() for p in value.split(",") if p.strip())


def graph_for(config, audience: frozenset[str] | None = None) -> Graph | None:
    data_dir = (config or {}).get("data_dir")
    if not data_dir:
        return None
    brain = brain_path_for(Path(data_dir))
    vault = vault_path_for(Path(data_dir))
    if not vault.exists():
        return None
    return load_graph(vault, views=brain if brain.exists() else None, audience=audience,
                      shared_bucket=shared_bucket_of(config))


def shared_bucket_of(config) -> str:
    """The folder of the family's shared pages, `family` unless the instance names another."""
    return ((config or {}).get("stack", {}).get("memory", {}) or {}).get("shared_bucket", "family")


def may_read(rel: str, audience: frozenset[str] | None, config) -> bool:
    """Whether everyone in `audience` may read the vault path `rel`; None reads all."""
    return audience is None or readable(rel, audience, shared_bucket=shared_bucket_of(config))


def scopes_for(audience: frozenset[str] | None, config) -> list[str] | None:
    """The vault folders `audience` may read, as `search_memory`'s scopes; None: all.

    The candidates are the shared folder and each listener's own; the rule
    keeps a listener's folder only when that listener is the whole audience.
    """
    if audience is None:
        return None
    candidates = sorted({shared_bucket_of(config), *audience})
    return [f"{bucket}/" for bucket in candidates if may_read(f"{bucket}/page.md", audience, config)]
