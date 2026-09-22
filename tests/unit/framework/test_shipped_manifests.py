"""The shipped stacklet.toml files have the shape the runtime reads.

TOML closes a table only when the next one opens, so a key written below
`[ports]` lands inside it, and nothing complains. The runtime turns each
`[ports]` entry into a `{<id>_<name>_url}` template variable and never
looks for the key where the author meant it, so the mistake shows up
only as something quietly missing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _manifests() -> dict[str, dict]:
    return {p.parent.name: tomllib.loads(p.read_text())
            for p in sorted(REPO_ROOT.glob("stacklets/*/stacklet.toml"))}


def test_manifests_are_found():
    # Guard the guard: an empty glob would make the check below pass.
    assert len(_manifests()) > 5


def test_every_ports_entry_is_a_port_number():
    misplaced = {sid: [k for k, v in m.get("ports", {}).items() if not isinstance(v, int)]
                 for sid, m in _manifests().items()}
    misplaced = {sid: keys for sid, keys in misplaced.items() if keys}
    assert not misplaced, f"keys inside [ports] that are not ports: {misplaced}"
