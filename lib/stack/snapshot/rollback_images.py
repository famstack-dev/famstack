"""The images of a rollback: the check before it stops, the tags before it starts.

A snapshot's manifest names the image id that each service ran
(`stack.manifest`). The tag in the compose file may name a newer image by
now: a pull, or a newer release that uses the same tag. The rollback
starts the old database on the image that wrote it, so before anything
stops, the Mac must still hold each image id, and before the start, each
tag names that id again:

    from stack.snapshot import rollback_images as images

    images.missing(manifest, ["docs"], source)
    # ["ghcr.io/paperless-ngx/paperless-ngx:2.20.1 (docs, paperless)"] after an image prune
    images.moves(manifest, ["docs"], ["search"], source)
    # [TagMove(name="ghcr.io/paperless-ngx/paperless-ngx:2.20.1", image_id="sha256:44…",
    #          now="sha256:99…", also_used_by=("search",))]
    images.retag(manifest, ["docs"], source)
    # (["ghcr.io/paperless-ngx/paperless-ngx:2.20.1"], [])   docker image tag sha256:44… <name>

Why it works this way:

- **Only a tag that moved is tagged again.** A tag that names the
  manifest's id is left alone, so a rollback right after an update that
  changed nothing of an image runs no `docker image tag`.
- **A tag is shared.** `docker image tag` moves the tag for every
  stacklet that starts it, also one outside the rollback. The plan names
  each running stacklet outside the rollback whose container starts the
  tag, so the admin knows which one gets the older image at its next
  start.
- **An image built on this Mac is neither checked nor tagged.** Its start
  builds it again from the Dockerfile of the snapshot's commit, as every
  start of that release does, so its old id is not what runs.
- **A name with a digest names one image already,** and is not tagged.

Debt: Watchtower can pull a newer image for a tag that the rollback
moved back, and so undo the rollback's images without a word. Trigger:
Watchtower's image changes get a snapshot, or a stacklet reports it.

Debt: a tag shared with a stacklet outside the rollback moves for that
stacklet too; the plan names it and changes nothing for it. Trigger: a
stacklet reports a start on an older image after a rollback.

This module only asks Docker and tags. The rollback's plan
(`stack.snapshot.rollback`) refuses on `missing` and shows `moves`; its
steps (`stack.snapshot.rollback_steps`) call `retag` before the start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .. import docker
from ..images import Image, ImageSource
from ..manifest import Manifest

__all__ = ["TagMove", "missing", "moves", "retag"]


@dataclass(frozen=True)
class TagMove:
    """A tag that names another image than the manifest's: `name` is the
    tag, `image_id` the manifest's id it names again, `now` the id it
    names now (None when the Mac has no image of that tag), and
    `also_used_by` the running stacklets outside the rollback that start it.

        TagMove(name="ghcr.io/paperless-ngx/paperless-ngx:2.20.1",
                image_id="sha256:44…", now="sha256:99…", also_used_by=("search",))
    """

    name: str
    image_id: str
    now: str | None
    also_used_by: tuple[str, ...] = ()


def missing(manifest: Manifest, starts: list[str], source: ImageSource) -> list[str]:
    """Each image of the stacklets in `starts` that the Mac no longer
    holds, as "<name> (<stacklet>, <service>)". Empty when it holds all.

        missing(manifest, ["docs"], source)
        # ["ghcr.io/paperless-ngx/paperless-ngx:2.20.1 (docs, paperless)"]
    """
    return [f"{image.name} ({sid}, {service})"
            for sid, service, image in _pulled(manifest, starts)
            if image.id is None or source.inspect(image.id) is None]


def moves(manifest: Manifest, starts: list[str], others: list[str],
          source: ImageSource) -> list[TagMove]:
    """The tags of the stacklets in `starts` that name another image than
    the manifest's, each with the stacklets among `others` whose
    containers start it. Empty when every tag names its image already.
    Asks Docker only.

        moves(manifest, ["docs"], ["search"], source)
        # [TagMove(name="…paperless-ngx:2.20.1", image_id="sha256:44…", now="sha256:99…",
        #          also_used_by=("search",))]
    """
    found: dict[str, TagMove] = {}
    for _sid, _service, image in _pulled(manifest, starts):
        if "@" in image.name or image.id is None or image.name in found:
            continue
        facts = source.inspect(image.name)
        now = facts.id if facts else None
        if now != image.id:
            found[image.name] = TagMove(image.name, image.id, now,
                                        _users(image.name, others, source))
    return list(found.values())


def retag(manifest: Manifest, starts: list[str],
          source: ImageSource) -> tuple[list[str], list[str]]:
    """Make each tag that `moves` finds name the manifest's image again,
    with `docker image tag <id> <name>`. Returns the tags it moved and,
    for each tag Docker refused, "<name>: <error>". Each call asks Docker
    again, so a second call after a stop halfway moves only what is left.

        retag(manifest, ["docs"], source)
        # (["ghcr.io/paperless-ngx/paperless-ngx:2.20.1"], [])
    """
    moved, failed = [], []
    for move in moves(manifest, starts, [], source):
        tagged, error = docker.tag_image(move.image_id, move.name)
        if tagged:
            moved.append(move.name)
        else:
            failed.append(f"{move.name}: {error}")
    return moved, failed


# ── Which images ─────────────────────────────────────────────────────────

def _pulled(manifest: Manifest, starts: list[str]) -> Iterator[tuple[str, str, Image]]:
    """Each image of the stacklets in `starts` that is not built on this
    Mac, with its stacklet and service."""
    for sid in starts:
        for service, image in manifest.stacklets[sid].images.items():
            if not image.built:
                yield sid, service, image


def _users(name: str, others: list[str], source: ImageSource) -> tuple[str, ...]:
    """The stacklets among `others` with a container started from `name`."""
    return tuple(sid for sid in others
                 if any(c.image == name for c in source.containers(sid)))
