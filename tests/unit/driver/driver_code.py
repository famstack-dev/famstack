"""driver_code: the driver's modules for a unit test, and stand-in commands on PATH.

    walk = driver_code.load("setup_walk")             # tests/driver/setup_walk.py
    calls = driver_code.command(tmp_path, "adb")         # every call's arguments, one line each
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")

The driver (`tests/driver/`) is a script folder, not a package: its modules
import each other by bare names (`import phone`). `load` imports one with
that folder on `sys.path` and takes both away again, because some of those
bare names (`speech`) are names of stacklet modules too, and a cached driver
`speech` would shadow the ai stacklet's in a later test of the same worker.
The returned module keeps its own references to the modules it imported.

The driver talks to the phone through `adb` and to another Mac through
`ssh`. A unit test never reaches either: `command` writes a stand-in that
answers in the test's folder, and the test puts it first on `PATH`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

DRIVER = Path(__file__).resolve().parents[2] / "driver"


def load(name: str) -> ModuleType:
    """The driver's module `name`, freshly imported; nothing of the driver stays cached."""
    sys.path.insert(0, str(DRIVER))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(DRIVER))
        for cached, module in list(sys.modules.items()):
            if Path(getattr(module, "__file__", None) or "/").is_relative_to(DRIVER):
                del sys.modules[cached]


def command(folder: Path, name: str, then: str = "exit 0") -> Path:
    """Write the stand-in `<folder>/bin/<name>`; the file it logs its calls to.

    Each call appends its arguments as one line to `<folder>/<name>.log`,
    then runs the shell line `then` (by default it succeeds and prints
    nothing). The test puts `<folder>/bin` first on `PATH` with
    `monkeypatch`, so `PATH` is given back after it.
    """
    log = folder / f"{name}.log"
    bin_dir = folder / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n{then}\n')
    script.chmod(0o755)
    return log
