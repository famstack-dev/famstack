"""The tools server ships every file it is made of.

Three lists name the tools server's files one by one, and each can miss a
new one without any other test noticing:

- the Dockerfile's `COPY` lines build core's tools image, its modules
  and its packages (`memory_ui/`);
- the compose file's read-only mounts put the checkout's files over the
  image's, so a change takes effect without a rebuild;
- `dashboard_page.py` serves only the page files it lists.

A module or a package missing from the first two makes `server.py` fail to import in
core's container on a real install, while every test here imports it from
the checkout. A script missing from the third is a 404, and a page module
that imports it does not run at all.

Read from the files as Docker reads them, and the page's files fetched
from the tools server as a browser fetches them.
"""

from __future__ import annotations

import re

from .setup_stand_in import on_this_mac
from .conftest import STACKLETS

CORE = STACKLETS / "core"
TOOLS = CORE / "tools-server"
PAGE = TOOLS / "dashboard"


def modules() -> set[str]:
    """The tools server's Python modules, by file name."""
    return {path.name for path in TOOLS.glob("*.py")}


def packages() -> set[str]:
    """The tools server's packages, by directory name (`memory_ui`)."""
    return {path.parent.name for path in TOOLS.glob("*/__init__.py")}


def page_files() -> set[str]:
    """The page's scripts and styles under `dashboard/`, by path below it;
    the components are served by pattern, so they need no list."""
    return {str(path.relative_to(PAGE)) for path in PAGE.rglob("*")
            if path.suffix in (".js", ".css") and path.relative_to(PAGE).parts[0] != "components"}


def test_the_image_copies_every_module():
    copied = set(re.findall(r"^COPY (\S+\.py) \.$", (TOOLS / "Dockerfile").read_text(), re.M))

    assert modules() - copied == set()


def test_the_compose_file_mounts_every_module():
    mounted = set(re.findall(r"- \./tools-server/(\S+\.py):/app/\1:ro",
                             (CORE / "docker-compose.yml").read_text()))

    assert modules() - mounted == set()


def test_the_image_copies_every_package():
    copied = set(re.findall(r"^COPY (\w+)/ \1/$", (TOOLS / "Dockerfile").read_text(), re.M))

    assert packages() - copied == set()


def test_the_compose_file_mounts_every_package():
    mounted = set(re.findall(r"- \./tools-server/(\w+):/app/\1:ro",
                             (CORE / "docker-compose.yml").read_text()))

    assert packages() - mounted == set()


def test_the_page_serves_every_script_and_style():
    browser = on_this_mac()

    unserved = {name for name in page_files()
                if browser.client.get(f"/dashboard/static/{name}").status_code != 200}

    assert unserved == set()
