"""Every bind mount of a shipped stacklet names a file or folder that exists.

A stacklet's `docker-compose.yml` mounts parts of its own directory into
its containers (`./tools-server/setup_page.py:/app/setup_page.py:ro`).
When the source is gone, Docker does not fail: it creates an empty
directory of that name in the checkout on every start, and the code that
then reads the name as a file breaks far from the cause. So a mount that
names a missing source, or only such an empty directory, fails here.

Sources from the environment (`${DATA_DIR}/...`) and the instance's state
folder (`.stack`, which the stack writes) are not checked: they exist only
on an instance.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
INSTANCE_STATE = REPO / ".stack"
COMPOSE_FILES = sorted((REPO / "stacklets").glob("*/docker-compose.yml"))


def bind_sources(compose: Path) -> list[str]:
    """The relative sources of every service's bind mounts, as written."""
    services = (yaml.safe_load(compose.read_text()) or {}).get("services") or {}
    found = []
    for service in services.values():
        for volume in service.get("volumes") or []:
            source = volume.get("source", "") if isinstance(volume, dict) else volume.split(":", 1)[0]
            if source.startswith(("./", "../")):
                found.append(source)
    return found


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda p: p.parent.name)
def test_every_bind_mount_names_what_exists(compose):
    missing = []
    for source in bind_sources(compose):
        path = (compose.parent / source).resolve()
        if path == INSTANCE_STATE or INSTANCE_STATE in path.parents:
            continue
        empty_dir = path.is_dir() and not any(path.iterdir())
        if not path.exists() or empty_dir:
            missing.append(source)

    assert missing == [], f"{compose.relative_to(REPO)} mounts what does not exist: {missing}"
