"""drill_release: the local releases a rollback drill updates to and rolls back from.

    releases = make(checkout)     # Releases(old="v0.5.0-beta.1", build="v0.5.0-drill.1", broken="v0.5.0-drill.2")
    remove(checkout)              # ["v0.5.0-drill.1", "v0.5.0-drill.2"]: the tags it deleted

The rollback drill (`rollback_drill.py`) needs three releases in the
checkout it installs from:

    old      v0.5.0-beta.1    a real release from before the snapshot feature (no `stack rollback`)
    build    v0.5.0-drill.1   the build under test: the commit the checkout was cloned at, plus
                              the line that lets the archivist reach Paperless (below)
    broken   v0.5.0-drill.2   the build plus one docs migration that empties Paperless's document index

The broken release's migration is a real one-way change. It deletes every
document's row in Paperless's database, so the docs page lists nothing,
while the files stay in the archive folder (`paperless/media`) and new
documents are still consumed. Only the restart's snapshot brings the
rows back. That is the case a snapshot exists for: a release that breaks
the family's data on its first start.

SAFETY: the tags and the commit are local to the drill's own clone, which
has no remote (`rollback_drill.py` removes `origin` after the clone), and
the drill never pushes. A pushed `v0.5.0-drill.2` would be a false
release that `stack update` offers to every beta tester. The label
"drill" is not one that the project uses, so the tag cannot shadow a
real release either. The drill's cleanup deletes the clone, tags and all.

Paperless 3.5 takes its allowed host names from `PAPERLESS_URL` alone,
so it refuses the archivist's calls to `stack-docs-paperless:8000`
("DisallowedHost") and no letter is filed. The fault is in the docs
stacklet since it moved to 3.5, not in the snapshot feature. The build
the drill tests carries one line that allows that host, so the drill
can file letters; the report names it.

`stack update` reads `v?MAJOR.MINOR.PATCH[-label.N]`. A pre-release sorts
by its label, then by its number, so drill.1 and drill.2 are both newer
than beta.1 and drill.2 is the newest.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

OLD = "v0.5.0-beta.1"
BUILD = "v0.5.0-drill.1"
BROKEN = "v0.5.0-drill.2"

# The commit of the broken release carries this identity. It never
# leaves the drill's clone, so it is not the project's identity.
DRILL_IDENTITY = ["-c", "user.name=rollback drill", "-c", "user.email=drill@localhost"]

COMPOSE_PATH = "stacklets/docs/docker-compose.yml"
PAPERLESS_URL_LINE = "      PAPERLESS_URL: ${PAPERLESS_URL}\n"
# Any host, because the drill's instance lives for one run on a test Mac.
ALLOWED_HOSTS_LINE = '      PAPERLESS_ALLOWED_HOSTS: "*"\n'

MIGRATION_PATH = "stacklets/docs/migrations/0002_drill_empty_the_index.py"

MIGRATION = '''"""Empty Paperless's document index (the rollback drill's one-way change).

The drill's broken release ships this migration to break the docs page:
every document row goes, the files in the archive folder stay. There is
no undo but the snapshot that the restart took before it ran.
"""


def run(ctx):
    ctx.step("Emptying the document index")
    ctx.run_in_container(
        ["sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "TRUNCATE documents_document CASCADE"'],
        service="db",
    )
'''


@dataclass(frozen=True)
class Releases:
    """The three tags of one drill, oldest first."""

    old: str
    build: str
    broken: str


def make(checkout: Path) -> Releases:
    """Tag the checkout's commit as the build and a commit with the migration as the broken release.

    Expects a clone without a remote, on the build's commit, with the
    old release's tag present. Leaves the checkout on the build's tag,
    detached, as `stack update` leaves an instance. Refuses (raises)
    when the old tag is missing or a remote is set.
    """
    if _git(checkout, "remote").strip():
        raise RuntimeError("the drill's clone has a remote; a drill tag must never reach one")
    _git(checkout, "rev-parse", "--verify", f"refs/tags/{OLD}")
    compose = checkout / COMPOSE_PATH
    compose.write_text(compose.read_text().replace(PAPERLESS_URL_LINE, PAPERLESS_URL_LINE + ALLOWED_HOSTS_LINE, 1))
    _git(checkout, "add", COMPOSE_PATH)
    _git(checkout, *DRILL_IDENTITY, "commit", "-q", "-m", "drill: let the archivist reach Paperless 3.5")
    _git(checkout, "tag", BUILD)
    (checkout / MIGRATION_PATH).write_text(MIGRATION)
    _git(checkout, "add", MIGRATION_PATH)
    _git(checkout, *DRILL_IDENTITY, "commit", "-q", "-m", "drill: empty the document index on start")
    _git(checkout, "tag", BROKEN)
    _git(checkout, "checkout", "-q", "--detach", BUILD)
    return Releases(OLD, BUILD, BROKEN)


def remove(checkout: Path) -> list[str]:
    """Delete the drill's tags from the checkout; the tags it deleted.

    The cleanup calls it before it removes the clone, so a clone that
    survives a failed removal still holds no drill tag.
    """
    if not (checkout / ".git").exists():
        return []
    deleted = []
    for tag in (BUILD, BROKEN):
        done = subprocess.run(["git", "-C", str(checkout), "tag", "-d", tag], capture_output=True, text=True)
        if done.returncode == 0:
            deleted.append(tag)
    return deleted


def _git(checkout: Path, *args: str) -> str:
    """Run git in the checkout; its stdout. Raises with git's stderr on a failure."""
    done = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout
