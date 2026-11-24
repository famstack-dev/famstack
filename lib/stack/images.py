"""Images: which image each service of a stacklet runs, by digest or local id.

A manifest records the image of each service, so that a rollback starts
the same images again. This module answers that question for one
stacklet:

    from stack.images import DockerImages, images_of

    images_of(Path("stacklets/docs"), DockerImages())
    # {"stack-docs-redis": Image(name="docker.io/valkey/valkey:8.1-alpine",
    #                            id="sha256:8a1d…",
    #                            digest="valkey/valkey@sha256:9971…"),
    #  "stack-docs-db": Image(name="postgres:16-alpine", id="sha256:31b7…",
    #                         digest="postgres@sha256:1d5b…"), …}

Where the answer comes from:

- **The containers first.** A stacklet that has containers, running or
  stopped, runs the image of each container. `stack update` moves the
  checkout before the restart. The compose file on disk can then name a
  tag that this Mac never pulled, or a tag that now points to another
  image. The container still names the image that it started from.
- **The compose file when there is no container.** A stacklet without
  containers would start the images that its compose file names.
- **Digests, not tags.** A pulled image is recorded with its registry
  digest, which names the image and nothing else. Docker keeps an image
  from Docker Hub without its `docker.io/` and `library/` prefixes
  (`docker.io/valkey/valkey:8.1-alpine` has the digest
  `valkey/valkey@sha256:…`). The names are compared without them.
- **An image built here has no registry digest.** Its record holds the
  local image id and the digest of the base image that its Dockerfile's
  final stage starts from. The Dockerfile is the one on disk. After an
  update it can name another base than the one the image was built from.
  The local id is what a rollback on this Mac uses, and it is exact.

Each `Image` says what this Mac knows of it:

| The Mac | `id` | `digest` | `built` |
|---|---|---|---|
| holds a pulled image | the local id | the registry digest | false |
| holds a pulled image without a digest of its name | the local id | None | false |
| holds an image built here | the local id | None | true, and `base` |
| does not hold the image | None | None | false |

Docker is a port. `images_of` asks Docker through `ImageSource`.
`DockerImages` answers from the Docker of this Mac. Tests give a stand-in,
because the unit lane has no Docker.

The manifest (`stack.manifest`)
keeps the records. This module only resolves them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import docker as docker_runtime

__all__ = [
    "COMPOSE_FILE",
    "Container",
    "DockerImages",
    "Image",
    "ImageError",
    "ImageFacts",
    "ImageSource",
    "Service",
    "images_of",
]

COMPOSE_FILE = "docker-compose.yml"

# The registry and the namespace that Docker leaves out of a Docker Hub
# image's digest: `docker.io/library/postgres` is stored as `postgres`.
_DOCKER_HUB_PREFIXES = ("docker.io/", "index.docker.io/")
_DOCKER_HUB_OFFICIAL = "library/"

_SERVICE_LABEL = "com.docker.compose.service"


# ── The record of an image ───────────────────────────────────────────────

class ImageError(Exception):
    """Docker cannot say which images a stacklet runs. The message names
    the compose file.

        ImageError("stacklets/docs/docker-compose.yml: docker compose cannot read it: …")
    """


@dataclass(frozen=True)
class Image:
    """The image of one service, as a manifest keeps it.

    `name` is the image reference that compose or the container names.
    `id` is the local image id, set when this Mac holds the image.
    `digest` is the registry digest of a pulled image. `built` is true for
    an image built on this Mac. Its `base` is the digest of its base image.

        Image(name="nickfedor/watchtower:1.16", id="sha256:5d0a…",
              digest="nickfedor/watchtower@sha256:62be…")
        Image(name="stack-core-stack-core-bot-runner", id="sha256:c457…",
              built=True, base="python@sha256:a6e3…")
        Image(name="ghcr.io/paperless-ngx/paperless-ngx:3.5")   # not on this Mac
    """

    name: str
    id: str | None = None
    digest: str | None = None
    built: bool = False
    base: str | None = None


# ── The port to Docker ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Service:
    """One service of a compose file: the image it starts, and the
    Dockerfile that builds that image when it is built on this Mac.

        Service("stack-docs-db", "postgres:16-alpine")
        Service("stack-core-bot-runner", "stack-core-stack-core-bot-runner",
                dockerfile=Path("stacklets/core/bot-runner/Dockerfile"))
    """

    name: str
    image: str
    dockerfile: Path | None = None


@dataclass(frozen=True)
class Container:
    """One container of a stacklet: its compose service, the image
    reference it was started with, and the id of that image.

        Container("stack-docs-redis", "docker.io/valkey/valkey:8.1-alpine",
                  "sha256:8a1d…")
    """

    service: str
    image: str
    image_id: str


@dataclass(frozen=True)
class ImageFacts:
    """What Docker knows of an image on this Mac: its local id and its
    registry digests. An image built here has no registry digests.

        ImageFacts(id="sha256:ce9a…", repo_digests=("python@sha256:a6e3…",))
    """

    id: str
    repo_digests: tuple[str, ...] = ()


class ImageSource(Protocol):
    """Answers the three questions that `images_of` asks Docker.

    `services` lists the services of a compose file as compose resolves
    it. `containers` lists the containers of a stacklet, running or
    stopped. `inspect` gives the facts of one image by reference or id,
    or None when the Mac does not hold it. `DockerImages` is the real one.

        source.containers("docs")
        # [Container("stack-docs-db", "postgres:16-alpine", "sha256:31b7…"), …]
    """

    def services(self, compose_file: Path) -> list[Service]:
        """The services of `compose_file`. Raises `ImageError` when the
        file cannot be read."""
        ...

    def containers(self, stacklet_id: str) -> list[Container]:
        """The containers of the stacklet, running or stopped."""
        ...

    def inspect(self, image: str) -> ImageFacts | None:
        """The facts of `image`, or None when this Mac does not hold it."""
        ...


class DockerImages:
    """`ImageSource` over the Docker of this Mac.

        DockerImages().inspect("python:3.12-slim")
        # ImageFacts(id="sha256:ce9a…", repo_digests=("python@sha256:a6e3…",))

    `services` reads `docker compose config`, because the image names in
    a compose file can hold variables from the stacklet's `.env`.
    """

    def services(self, compose_file: Path) -> list[Service]:
        """The services of `compose_file`, as `docker compose config`
        resolves them. Raises `ImageError` when compose cannot read it,
        because a record without the stacklet's images cannot roll it
        back."""
        code, out, err = docker_runtime.compose(compose_file, "config", "--format", "json")
        if code != 0:
            raise ImageError(f"{compose_file}: docker compose cannot read it: {err.strip()}")
        return _services_of(json.loads(out))

    def containers(self, stacklet_id: str) -> list[Container]:
        """The containers of the stacklet, from `docker container inspect`."""
        names = [row["name"] for row in docker_runtime.containers_for(stacklet_id)]
        return [_container_of(found) for found in docker_runtime.inspect_containers(names)]

    def inspect(self, image: str) -> ImageFacts | None:
        """The facts of `image` from `docker image inspect`."""
        found = docker_runtime.inspect_image(image)
        if found is None:
            return None
        return ImageFacts(id=found["Id"], repo_digests=tuple(found.get("RepoDigests") or ()))


# ── Resolving a stacklet's images ────────────────────────────────────────

def images_of(stacklet_dir: Path, source: ImageSource) -> dict[str, Image]:
    """The image of each service of the stacklet in `stacklet_dir`, by
    service name.

    Takes the images of the stacklet's containers when it has any, and
    the images its compose file starts when it has none. A stacklet
    without a compose file (a host stacklet) has no images, and `source`
    is not asked. Raises `ImageError` when the compose file cannot be
    read. Changes nothing.

        images_of(Path("stacklets/ai"), DockerImages())     # {}
    """
    compose_file = Path(stacklet_dir) / COMPOSE_FILE
    if not compose_file.is_file():
        return {}
    services = {service.name: service for service in source.services(compose_file)}
    containers = source.containers(Path(stacklet_dir).name)
    if containers:
        return {c.service: _image(c.image, c.image_id, _dockerfile(services, c.service), source)
                for c in containers}
    return {s.name: _image(s.image, s.image, s.dockerfile, source) for s in services.values()}


# ── One image ────────────────────────────────────────────────────────────

def _dockerfile(services: dict[str, Service], name: str) -> Path | None:
    service = services.get(name)
    return service.dockerfile if service else None


def _image(name: str, reference: str, dockerfile: Path | None, source: ImageSource) -> Image:
    """The record of the image `name`, found on this Mac by `reference`
    (the name itself, or a container's image id). A service with a
    Dockerfile has its image built here."""
    facts = source.inspect(reference)
    if facts is None:
        return Image(name=name)
    if dockerfile is None:
        return Image(name=name, id=facts.id, digest=_digest_of(name, facts))
    return Image(name=name, id=facts.id, built=True, base=_base_digest(dockerfile, source))


def _base_digest(dockerfile: Path, source: ImageSource) -> str | None:
    """The registry digest of the base image of `dockerfile`, or None
    when it has no base image or this Mac does not hold it."""
    base = _base_image(dockerfile)
    facts = source.inspect(base) if base else None
    return _digest_of(base, facts) if base and facts else None


def _digest_of(image: str, facts: ImageFacts) -> str | None:
    """The registry digest of `image`'s own repository, as Docker gives it.
    An image pulled from two repositories has a digest for each. A pull
    by digest needs the one that names the image's repository.

    >>> _digest_of("docker.io/valkey/valkey:8.1-alpine",
    ...            ImageFacts("sha256:8a1d", ("valkey/valkey@sha256:9971",)))
    'valkey/valkey@sha256:9971'
    """
    repository = _normalized(_repository(image))
    for digest in facts.repo_digests:
        if _normalized(digest.split("@", 1)[0]) == repository:
            return digest
    return None


def _repository(image: str) -> str:
    """The repository part of an image reference, without tag or digest.

    >>> _repository("localhost:5000/caddy:2.11")
    'localhost:5000/caddy'
    """
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    return name[: len(name) - len(last.split(":", 1)[1]) - 1] if ":" in last else name


def _normalized(repository: str) -> str:
    """A repository name as Docker stores a Docker Hub image: without the
    registry and without the namespace of the official images.

    >>> _normalized("docker.io/library/postgres")
    'postgres'
    """
    for prefix in _DOCKER_HUB_PREFIXES:
        if repository.startswith(prefix):
            repository = repository[len(prefix):]
            break
    return repository.removeprefix(_DOCKER_HUB_OFFICIAL)


# ── The Dockerfile's base ────────────────────────────────────────────────

def _base_image(dockerfile: Path) -> str | None:
    """The image the Dockerfile's final stage starts from.

    A stage can start from an earlier stage (`FROM deps`). The name is
    then followed back to an image from a registry. `scratch` and a name
    built from an `ARG` (`${BASE}`) are returned as they are. The base
    then gets no digest, because Docker holds no image of that name.
    """
    stages: dict[str, str] = {}
    base = None
    for words in _from_lines(Path(dockerfile).read_text(encoding="utf-8")):
        image = stages.get(words[0].lower(), words[0])
        if len(words) == 3 and words[1].lower() == "as":
            stages[words[2].lower()] = image
        base = image
    return base


def _from_lines(text: str) -> list[list[str]]:
    """The words after each FROM, without flags such as `--platform`."""
    lines = []
    for line in text.splitlines():
        words = line.split()
        if words and words[0].upper() == "FROM":
            lines.append([w for w in words[1:] if not w.startswith("--")])
    return lines


# ── Docker's answers ─────────────────────────────────────────────────────

def _services_of(config: dict) -> list[Service]:
    """The services of `docker compose config --format json`. A service
    that builds without an image name gets compose's own name for it:
    `<project>-<service>`."""
    project = config.get("name", "")
    services = []
    for name, spec in (config.get("services") or {}).items():
        image = spec.get("image") or f"{project}-{name}"
        build = spec.get("build")
        dockerfile = None
        if build:
            dockerfile = Path(build["context"]) / build.get("dockerfile", "Dockerfile")
        services.append(Service(name, image, dockerfile))
    return services


def _container_of(found: dict) -> Container:
    """A Container from one entry of `docker container inspect`."""
    config = found.get("Config") or {}
    labels = config.get("Labels") or {}
    return Container(service=labels.get(_SERVICE_LABEL, found.get("Name", "").lstrip("/")),
                     image=config.get("Image", ""), image_id=found["Image"])
