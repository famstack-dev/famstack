"""The extension lane: an extension's tests, run inside a famstack checkout.

    script/test extension <folder>...    an extension's folder, or one holding several

An extension lives outside this repository (ADR-013, ADR-022), in its own
repository, and its tests run here, because the extension runs inside the
stack. They get this checkout's framework, its pytest configuration, the
unit lane's rules (`tests/lanes/unit.py`) and two fixtures:

- `stack`: the extension the test belongs to (the nearest folder above the
  test file with a `stacklet.toml`), installed and up in a Simpsons
  instance (`tests/extensions/instance.py`). `stack.run("web", "search",
  "tent", "--json")` is `./stack web search tent --json`, as a process.
- `model`: the model stand-in (`tests/model.py`), which the instance's
  bot runner asks.

An extension's own modules are its package, `stacklets.<id>`, as the
framework loads them, so a module test imports them by that name:
`from stacklets.web.ask import build_prompt`. An extension whose id this
checkout ships is refused before any test runs: the release wins that
clash, so the extension would never load.

`script/test` loads this file as a pytest plugin (`-p tests.lanes.extension`),
so its hooks and fixtures apply to every test of the run. The network guard
covers the test's own process; the `./stack` processes a test starts are not
guarded, which is why the instance names a stand-in for every address.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from stack.stacklet_code import import_stacklets
from tests.extensions.instance import Extension, installed
from tests.lanes.unit import (  # noqa: F401
    diary_back_in_english, httpserver_listen_address, pytest_runtest_call,
    pytest_runtest_setup, pytest_runtest_teardown, stacklet_packages_as_they_were)
from tests.model import Model

SHIPPED = Path(__file__).resolve().parents[2] / "stacklets"


def extension_of(path: Path) -> Path | None:
    """The extension `path` is in: the nearest folder at or above it with a
    `stacklet.toml`, or None."""
    path = Path(path).resolve()
    for folder in (path, *path.parents):
        if (folder / "stacklet.toml").is_file():
            return folder
    return None


def _extensions(args: list[str]) -> list[Path]:
    """The extensions a run's arguments name: each one a test is in, or
    each one a folder holds."""
    found = []
    for arg in args:
        path = Path(arg.split("::", 1)[0])
        if not path.exists():
            continue
        if extension := extension_of(path):
            found.append(extension)
        elif path.is_dir():
            found += [m.parent.resolve() for m in sorted(path.glob("*/stacklet.toml"))]
    return found


def pytest_configure(config) -> None:
    """Refuse an extension the release shadows, then make each extension's
    modules importable as `stacklets.<id>`."""
    extensions = _extensions(config.args)
    for extension in extensions:
        if (SHIPPED / extension.name / "stacklet.toml").is_file():
            raise pytest.UsageError(
                f"{extension.name} is shipped in {SHIPPED / extension.name}: the release "
                f"wins that clash, so the extension in {extension} would never load.")
    import_stacklets(sorted({e.parent for e in extensions}))


@pytest.fixture
def stack(tmp_path, request) -> Iterator[Extension]:
    """The extension this test belongs to, installed and up (see the module)."""
    extension = extension_of(request.path)
    if extension is None:
        raise pytest.UsageError(f"{request.path} is in no extension: no folder above it "
                                f"has a stacklet.toml")
    with installed(tmp_path, extension) as running:
        yield running


@pytest.fixture
def model(stack, httpserver) -> Model:
    """The model the bot runner's commands ask, on the test's `httpserver`."""
    model = Model(httpserver)
    stack.uses_model(model)
    return model
