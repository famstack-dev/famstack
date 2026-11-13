"""`as_stack_runs_it` gives the session back as it found it.

The surface tests run in one process in CI (`script/cibuild`), so a test
in `./stack`'s interpreter (`conftest.production_interpreter`) must leave
the stacklet modules of the session exactly as they were. A hook imports
its stacklet's modules relatively (`from .. import homebrew_omlx`), and
Python answers that from the package's attribute before `sys.modules`. A
test that patches the module it finds in `sys.modules` then patches the
copy the hook uses only while both name the same module.
"""

from __future__ import annotations

import importlib
import sys

import pytest

from .conftest import production_interpreter

MODULE = "stacklets.ai.homebrew_omlx"


def test_a_stacklet_module_and_its_packages_attribute_agree_afterwards():
    """Given a stacklet module the session had imported, when a block in
    `./stack`'s interpreter imports its own copy, then afterwards the
    package names the session's copy again, the one in `sys.modules`."""
    session_copy = importlib.import_module(MODULE)

    with pytest.MonkeyPatch.context() as monkeypatch:
        with production_interpreter(monkeypatch):
            assert importlib.import_module(MODULE) is not session_copy

    assert sys.modules[MODULE] is session_copy
    assert sys.modules["stacklets.ai"].homebrew_omlx is session_copy


def test_a_module_first_imported_in_the_block_is_forgotten_by_its_package():
    """Given a stacklet module the session had not imported, when a block
    in `./stack`'s interpreter imports it, then afterwards neither
    `sys.modules` nor the package names that copy."""
    sys.modules.pop(MODULE, None)
    package = importlib.import_module("stacklets.ai")
    if hasattr(package, "homebrew_omlx"):
        delattr(package, "homebrew_omlx")

    with pytest.MonkeyPatch.context() as monkeypatch:
        with production_interpreter(monkeypatch):
            block_copy = importlib.import_module(MODULE)

    assert MODULE not in sys.modules
    assert getattr(package, "homebrew_omlx", None) is not block_copy
