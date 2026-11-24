"""Images: which image each service of a stacklet runs, by digest or local id.

A manifest records the image of each service, so that a rollback starts
the same images again. These tests drive `stack.images.images_of` and the
real adapter `DockerImages`.

Docker is outside the unit lane. The tests of `images_of` give it a
stand-in for the Docker of this Mac (`ThisMac`). The tests of
`DockerImages` put a `docker` first on `PATH` that answers with output
recorded from a real Docker (OrbStack, 2026-11-24), trimmed to the
fields the adapter reads. Both stand-ins answer in Docker's own forms:
a Docker Hub image keeps its digest without `docker.io/` and `library/`.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from stack.images import (
    Container,
    DockerImages,
    Image,
    ImageError,
    ImageFacts,
    Service,
    images_of,
)

VALKEY = "docker.io/valkey/valkey:8.1-alpine"
VALKEY_ID = "sha256:8a1d0d158cb8491d9167d726f6c73c518dab1ae7b588a49ce03c2fe9d0bb60a2"
VALKEY_DIGEST = "valkey/valkey@sha256:9971099af41c36e887aabdfe22b80c0fe45df949163dfc9cb434febc57cfc3fc"
POSTGRES_ID = "sha256:31b7df755869f3d5b0a8a87e0f29ea85cac62a522f008681d304e311160d181f"
POSTGRES_DIGEST = "postgres@sha256:1d5b02d3a9941fbd52437db4107f8c52d6297dac553ca1e5c299ebb3756b4857"
PYTHON = "python:3.12-slim"
PYTHON_ID = "sha256:ce9a404c2c0138e747a43e6ea022d2f7e670ed868df35d627a663ba7fb940ea9"
PYTHON_DIGEST = "python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
BOT_RUNNER = "stack-core-stack-core-bot-runner"
BOT_RUNNER_ID = "sha256:c457650318d287fdb713122f9d11bba3cb3aaab558f6077ec40731e5eddc871d"
PAPERLESS_OLD = "ghcr.io/paperless-ngx/paperless-ngx:3.4"
PAPERLESS_OLD_ID = "sha256:" + "4" * 64
PAPERLESS_OLD_DIGEST = "ghcr.io/paperless-ngx/paperless-ngx@sha256:" + "a" * 64
PAPERLESS_NEW = "ghcr.io/paperless-ngx/paperless-ngx:3.5"


# ── The stand-in for Docker ──────────────────────────────────────────────

@dataclass
class ThisMac:
    """Docker on this Mac as `images_of` asks it: the services of each
    compose file, the containers of each stacklet, and the images the Mac
    holds, by reference and by id."""

    services_by_file: dict[Path, list[Service]] = field(default_factory=dict)
    containers_by_stacklet: dict[str, list[Container]] = field(default_factory=dict)
    images: dict[str, ImageFacts] = field(default_factory=dict)

    def services(self, compose_file: Path) -> list[Service]:
        return self.services_by_file[Path(compose_file)]

    def containers(self, stacklet_id: str) -> list[Container]:
        return self.containers_by_stacklet.get(stacklet_id, [])

    def inspect(self, image: str) -> ImageFacts | None:
        return self.images.get(image)


def a_stacklet(tmp_path: Path, stacklet_id: str, dockerfile: str | None = None) -> Path:
    """A stacklet's folder with a compose file, and a Dockerfile when it
    builds its image."""
    stacklet_dir = tmp_path / "stacklets" / stacklet_id
    stacklet_dir.mkdir(parents=True)
    (stacklet_dir / "docker-compose.yml").write_text(f"name: stack-{stacklet_id}\n")
    if dockerfile is not None:
        (stacklet_dir / "Dockerfile").write_text(textwrap.dedent(dockerfile))
    return stacklet_dir


def built_from(tmp_path: Path, dockerfile: str) -> Image:
    """The record of the bot runner's image, built here from `dockerfile`,
    on a Mac that holds `python:3.12-slim`."""
    stacklet_dir = a_stacklet(tmp_path, "core", dockerfile)
    mac = ThisMac(
        services_by_file={stacklet_dir / "docker-compose.yml": [
            Service("stack-core-bot-runner", BOT_RUNNER, dockerfile=stacklet_dir / "Dockerfile")]},
        images={BOT_RUNNER: ImageFacts(BOT_RUNNER_ID),
                PYTHON: ImageFacts(PYTHON_ID, (PYTHON_DIGEST,))})
    return images_of(stacklet_dir, mac)["stack-core-bot-runner"]


# ── Pulled images ────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, facts, digest", [
    pytest.param(VALKEY, ImageFacts(VALKEY_ID, (VALKEY_DIGEST,)), VALKEY_DIGEST,
                 id="docker.io namespace"),
    pytest.param("docker.io/library/postgres:16-alpine",
                 ImageFacts(POSTGRES_ID, (POSTGRES_DIGEST,)), POSTGRES_DIGEST, id="docker.io library"),
    pytest.param("index.docker.io/library/postgres:16-alpine",
                 ImageFacts(POSTGRES_ID, (POSTGRES_DIGEST,)), POSTGRES_DIGEST, id="index.docker.io"),
    pytest.param("postgres:16-alpine", ImageFacts(POSTGRES_ID, (POSTGRES_DIGEST,)), POSTGRES_DIGEST,
                 id="short name"),
])
def test_a_docker_hub_image_gets_the_digest_docker_stores_without_the_prefix(tmp_path, name, facts, digest):
    """Given a compose file that names a Docker Hub image with or without
    `docker.io/` and `library/`, when its image is recorded, then the
    record holds the digest that Docker stores without those prefixes.
    `docs` and `photos` name `docker.io/valkey/valkey:8.1-alpine`, and
    Docker answers `valkey/valkey@sha256:…` for it."""
    stacklet_dir = a_stacklet(tmp_path, "docs")
    mac = ThisMac(services_by_file={stacklet_dir / "docker-compose.yml": [Service("redis", name)]},
                  images={name: facts})

    assert images_of(stacklet_dir, mac) == {"redis": Image(name=name, id=facts.id, digest=digest)}


def test_a_running_stacklet_records_the_image_its_container_runs_not_the_compose_tag(tmp_path):
    """Given a running stacklet whose compose file now names a new tag
    (after `stack update` moved the checkout), when its images are
    recorded, then the record holds the image the container runs, with
    its digest. The new tag is not on this Mac yet, and a rollback needs
    the image that ran."""
    stacklet_dir = a_stacklet(tmp_path, "docs")
    mac = ThisMac(
        services_by_file={stacklet_dir / "docker-compose.yml": [Service("paperless", PAPERLESS_NEW)]},
        containers_by_stacklet={"docs": [Container("paperless", PAPERLESS_OLD, PAPERLESS_OLD_ID)]},
        images={PAPERLESS_OLD_ID: ImageFacts(PAPERLESS_OLD_ID, (PAPERLESS_OLD_DIGEST,))})

    assert images_of(stacklet_dir, mac) == {
        "paperless": Image(name=PAPERLESS_OLD, id=PAPERLESS_OLD_ID, digest=PAPERLESS_OLD_DIGEST)}


def test_an_image_the_mac_does_not_hold_is_told_apart_from_one_without_a_digest(tmp_path):
    """Given one image the Mac does not hold and one it holds without a
    registry digest of its name, when they are recorded, then the first
    has no id and the second has its id. A rollback can start the second
    by its id, and must pull the first."""
    stacklet_dir = a_stacklet(tmp_path, "docs")
    loaded = "example/loaded:1"
    mac = ThisMac(
        services_by_file={stacklet_dir / "docker-compose.yml": [
            Service("paperless", PAPERLESS_NEW), Service("loaded", loaded)]},
        images={loaded: ImageFacts("sha256:" + "9" * 64, ())})

    assert images_of(stacklet_dir, mac) == {
        "paperless": Image(name=PAPERLESS_NEW),
        "loaded": Image(name=loaded, id="sha256:" + "9" * 64),
    }


def test_a_stacklet_without_a_compose_file_has_no_images(tmp_path):
    """Given a host stacklet (no compose file), when its images are
    recorded, then it has none, and Docker is not asked."""
    (tmp_path / "ai").mkdir()

    assert images_of(tmp_path / "ai", ThisMac()) == {}


# ── Images built here ────────────────────────────────────────────────────

def test_an_image_built_here_records_its_local_id_and_the_digest_of_its_base(tmp_path):
    """Given a stacklet whose image is built on this Mac, when it is
    recorded, then the record holds the local image's id and the digest
    of the base that the Dockerfile's final stage starts from, followed
    back through an earlier stage. A local build has no registry digest."""
    image = built_from(tmp_path, """\
        FROM python:3.12-slim AS deps
        RUN pip install aiohttp
        FROM deps
        COPY . /app
        """)

    assert image == Image(name=BOT_RUNNER, id=BOT_RUNNER_ID, built=True, base=PYTHON_DIGEST)


def test_a_platform_flag_on_the_from_line_does_not_hide_the_base(tmp_path):
    """Given a Dockerfile whose FROM line carries `--platform`, when its
    image is recorded, then the base is the image after the flag."""
    image = built_from(tmp_path, "FROM --platform=linux/arm64 python:3.12-slim\n")

    assert image.base == PYTHON_DIGEST


@pytest.mark.parametrize("dockerfile", [
    pytest.param("FROM scratch\nCOPY app /app\n", id="scratch"),
    pytest.param("ARG BASE=python:3.12-slim\nFROM ${BASE}\n", id="ARG"),
])
def test_a_base_that_names_no_image_is_recorded_as_none(tmp_path, dockerfile):
    """Given a Dockerfile that starts from `scratch` or from an `ARG`,
    when its image is recorded, then it is built here with no base. Its
    id still identifies it on this Mac."""
    image = built_from(tmp_path, dockerfile)

    assert image == Image(name=BOT_RUNNER, id=BOT_RUNNER_ID, built=True, base=None)


# ── The real adapter, over recorded Docker output ────────────────────────

DOCS_CONFIG = {"name": "stack-docs", "services": {
    "stack-docs-db": {"image": "postgres:16-alpine", "container_name": "stack-docs-db"},
    "stack-docs-redis": {"image": VALKEY, "container_name": "stack-docs-redis"}}}

CORE_CONFIG = {"name": "stack-core", "services": {
    "stack-core-bot-runner": {"build": {"context": "/stacklets/core/bot-runner", "dockerfile": "Dockerfile"},
                              "container_name": "stack-core-bot-runner"},
    "stack-core-watchtower": {"image": "nickfedor/watchtower:1.16",
                              "container_name": "stack-core-watchtower"}}}

DOCS_CONTAINERS = [
    {"Name": "/stack-docs-redis", "Image": VALKEY_ID,
     "Config": {"Image": VALKEY, "Labels": {"com.docker.compose.project": "stack-docs",
                                            "com.docker.compose.service": "stack-docs-redis"}}},
    {"Name": "/stack-docs-db", "Image": POSTGRES_ID,
     "Config": {"Image": "postgres:16-alpine", "Labels": {"com.docker.compose.project": "stack-docs",
                                                          "com.docker.compose.service": "stack-docs-db"}}},
]

DOCS_PS = ("stack-docs-redis\tstack-docs\trunning\tUp 3 days\n"
           "stack-docs-db\tstack-docs\trunning\tUp 3 days\n")

IMAGE_INSPECT = {
    VALKEY_ID: {"Id": VALKEY_ID, "RepoTags": ["valkey/valkey:8.1-alpine"], "RepoDigests": [VALKEY_DIGEST]},
    POSTGRES_ID: {"Id": POSTGRES_ID, "RepoTags": ["postgres:16-alpine"], "RepoDigests": [
        POSTGRES_DIGEST,
        "postgres@sha256:f2f98726820d0342055351c6e9d36e705f5bfffb3dd53ff556325114b08afb6d"]},
    PYTHON: {"Id": PYTHON_ID, "RepoTags": [PYTHON], "RepoDigests": [PYTHON_DIGEST]},
}

# The stand-in `docker`: drops `--context <name>`, then answers with the
# first recorded answer whose arguments start the command line.
DOCKER_SCRIPT = """
import json, sys
args = sys.argv[2:]
if args[:1] == ["--context"]:
    args = args[2:]
for prefix, out, code in json.load(open(sys.argv[1])):
    if args[:len(prefix)] == prefix:
        sys.stdout.write(out)
        sys.exit(code)
sys.stderr.write("the stand-in has no answer for: docker " + " ".join(args) + "\\n")
sys.exit(1)
"""


@pytest.fixture
def docker_answering(tmp_path, monkeypatch):
    """Put a `docker` first on `PATH` that answers with the given
    `(arguments, stdout, exit code)` entries."""
    folder = tmp_path / "docker-stand-in"
    folder.mkdir()
    (folder / "answer.py").write_text(DOCKER_SCRIPT)

    def answer(entries: list[tuple[list[str], str, int]]) -> None:
        (folder / "answers.json").write_text(json.dumps(entries))
        script = folder / "docker"
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{folder / "answer.py"}" '
                          f'"{folder / "answers.json"}" "$@"\n')
        script.chmod(0o755)
        monkeypatch.setenv("PATH", str(folder), prepend=os.pathsep)

    return answer


def image_answers() -> list[tuple[list[str], str, int]]:
    return [(["image", "inspect", ref], json.dumps([found]), 0) for ref, found in IMAGE_INSPECT.items()]


def test_the_real_adapter_records_a_running_stacklet_from_dockers_answers(tmp_path, docker_answering):
    """Given Docker's recorded answers for the running docs stacklet, when
    its images are recorded through `DockerImages`, then each service has
    the id of the image its container runs and the digest of its own
    repository, also for the `docker.io/` image."""
    stacklet_dir = a_stacklet(tmp_path, "docs")
    compose_file = str(stacklet_dir / "docker-compose.yml")
    docker_answering([
        (["compose", "-f", compose_file, "config"], json.dumps(DOCS_CONFIG), 0),
        (["ps"], DOCS_PS, 0),
        (["container", "inspect"], json.dumps(DOCS_CONTAINERS), 0),
        *image_answers(),
    ])

    assert images_of(stacklet_dir, DockerImages()) == {
        "stack-docs-redis": Image(name=VALKEY, id=VALKEY_ID, digest=VALKEY_DIGEST),
        "stack-docs-db": Image(name="postgres:16-alpine", id=POSTGRES_ID, digest=POSTGRES_DIGEST),
    }


def test_the_real_adapter_reads_built_services_from_compose_config(tmp_path, docker_answering):
    """Given `docker compose config` for core, where the bot runner is
    built without an image name, when `DockerImages` lists the services,
    then the bot runner has compose's name `<project>-<service>` and the
    Dockerfile of its build context."""
    compose_file = tmp_path / "docker-compose.yml"
    docker_answering([(["compose", "-f", str(compose_file), "config"], json.dumps(CORE_CONFIG), 0)])

    assert DockerImages().services(compose_file) == [
        Service("stack-core-bot-runner", BOT_RUNNER, Path("/stacklets/core/bot-runner/Dockerfile")),
        Service("stack-core-watchtower", "nickfedor/watchtower:1.16"),
    ]


def test_the_real_adapter_says_none_for_an_image_the_mac_does_not_hold(docker_answering):
    """Given Docker that does not know an image, when `DockerImages`
    inspects it, then the answer is None."""
    docker_answering([(["image", "inspect"], "[]\n", 1)])

    assert DockerImages().inspect(PAPERLESS_NEW) is None


def test_the_real_adapter_fails_on_a_compose_file_compose_cannot_read(tmp_path, docker_answering):
    """Given a compose file that `docker compose config` refuses, when
    `DockerImages` lists its services, then it raises an error that names
    the file. A record without the stacklet's images cannot roll it back."""
    compose_file = tmp_path / "docker-compose.yml"
    docker_answering([(["compose"], "", 15)])

    with pytest.raises(ImageError) as error:
        DockerImages().services(compose_file)

    assert str(compose_file) in str(error.value)
