"""Assert the version string agrees across every place that declares it.

v0.3.0-beta.1 shipped with a stale `uv.lock` and a `VERSION` that disagreed
with the tag, because the pre-tag checklist was manual and a human skipped a
line. This is that line, made mechanical.

Two sources must agree: `lib/stack/cli.py`'s VERSION constant and
`pyproject.toml`'s `project.version`. When run inside a tag build,
`GITHUB_REF_NAME` is a third — the tag itself, minus its leading `v`.

Exits non-zero with the mismatch spelled out. Import-free beyond stdlib so it
runs anywhere `stacktests preflight` runs.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VERSION_RE = re.compile(r"^VERSION\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)

# These spellings are equal, and the repo uses both on purpose: tags and the
# CLI banner read `0.3.0-beta.2`, while pyproject stores PEP 440's canonical
# `0.3.0b2`. Comparing raw strings would fail every beta release, so normalise
# to the canonical form before comparing.
_PRE_RE = re.compile(
    r"[-_.]?(?:(?P<a>alpha|a)|(?P<b>beta|b)|(?P<rc>rc|c|pre|preview))[-_.]?(?P<n>\d*)$",
    re.IGNORECASE,
)


def _normalise(version: str) -> str:
    """Reduce a version to its PEP 440 canonical form.

    Deliberately covers only the pre-release suffix - the one place this repo
    actually spells things two ways. Anything more would be reimplementing
    `packaging`, which this script cannot import: it runs on the host
    interpreter, outside the uv-managed test environment.
    """
    version = version.strip().lower()
    match = _PRE_RE.search(version)
    if not match:
        return version
    marker = "a" if match.group("a") else "b" if match.group("b") else "rc"
    number = match.group("n") or "0"
    return f"{version[: match.start()]}{marker}{int(number)}"


def _cli_version() -> str:
    text = (REPO_ROOT / "lib" / "stack" / "cli.py").read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if not match:
        raise SystemExit("no VERSION = '...' assignment found in lib/stack/cli.py")
    return match.group(1)


def _pyproject_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def main() -> int:
    sources = {
        "lib/stack/cli.py": _cli_version(),
        "pyproject.toml": _pyproject_version(),
    }

    # Only present in a tag build; locally there is no tag to agree with.
    ref = os.environ.get("GITHUB_REF_NAME", "")
    if ref.startswith("v"):
        sources["git tag"] = ref[1:]

    distinct = {_normalise(v) for v in sources.values()}
    if len(distinct) > 1:
        print("version mismatch:", file=sys.stderr)
        for origin, value in sources.items():
            print(f"  {origin:<20} {value}  (normalised: {_normalise(value)})", file=sys.stderr)
        return 1

    print(f"    {distinct.pop()} agreed by {', '.join(sources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
