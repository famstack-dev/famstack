"""Every container image must name a version.

A floating tag plus watchtower is a scheduled outage. Paperless-ngx rolled from
the 2.x line to 3.0.2 unattended and broke document filing across the whole
e2e suite; we found out from a red test run, not from a decision.

Images are pinned to the minor line where upstream publishes one
(`gotenberg:8.37`), otherwise to the exact version (`synapse:v1.161.0`), so
watchtower delivers patch releases and nothing larger. Whether a minor tag
exists is a registry question, so this test checks what it can offline: every
tag is a version, never a name such as `latest`, `main` or `release` that
upstream moves to its next build.

This is the audit that would have caught it, as a test instead of a one-time
grep — it costs milliseconds and cannot silently stop being true.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# `image: repo/name:tag` in compose, ignoring commented-out lines. The tag is
# optional in the grammar precisely because omitting it means `:latest`, which
# is one of the cases this test exists to reject.
_IMAGE_RE = re.compile(r"^\s*image:\s*[\"']?([^\s\"'#]+)", re.MULTILINE)

# A version starts with a digit, optionally after a `v`: `14.0`, `v1.161.0`,
# `16-alpine`, `14-vectorchord0.4.3-pgvectors0.2.0`.
_VERSION_RE = re.compile(r"^v?\d")

# A tag taken from the environment, `${IMMICH_VERSION:-v3.2.2}`.
_VAR_RE = re.compile(r"^\$\{(\w+)(?::-([^}]*))?\}$")


def _compose_files() -> list[Path]:
    return sorted(REPO_ROOT.glob("stacklets/*/docker-compose*.yml"))


def _env_defaults(compose: Path) -> dict:
    manifest = compose.parent / "stacklet.toml"
    if not manifest.exists():
        return {}
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    return data.get("env", {}).get("defaults", {})


def _tag(ref: str, env: dict) -> str:
    """The tag `ref` resolves to once the stacklet's `.env` is rendered."""
    # Strip the registry host before looking for the tag separator, so
    # `registry:5000/img` is not mistaken for an `img:5000` tag.
    last_segment = ref.rsplit("/", 1)[-1]
    tag = last_segment.split(":", 1)[1] if ":" in last_segment else "latest"
    # `.env` always carries the stacklet's `[env.defaults]` value, so that is
    # the pin; the compose fallback only applies to a variable it lacks.
    if var := _VAR_RE.match(tag):
        tag = str(env.get(var.group(1), var.group(2) or ""))
    return tag


def _unpinned(compose: Path) -> list[str]:
    """Return image refs whose tag is not a version."""
    env = _env_defaults(compose)
    found = []
    for ref in _IMAGE_RE.findall(compose.read_text(encoding="utf-8")):
        # Environment-substituted refs (${FOO}) are resolved at render time;
        # the pin lives wherever that variable is defined, not here.
        if ref.startswith("${"):
            continue
        # A digest pin (repo@sha256:...) is stricter than a tag. Accept it.
        if "@sha256:" in ref:
            continue
        tag = _tag(ref, env)
        # Built from the stacklet's Dockerfile, whose FROM line is the pin.
        if tag == "local":
            continue
        if not _VERSION_RE.match(tag):
            found.append(ref)
    return found


def test_compose_files_exist():
    # Guard the guard: a glob that silently matches nothing would make every
    # assertion below vacuously true.
    assert _compose_files(), "no stacklet compose files found - glob is wrong"


def test_no_floating_image_tags():
    offenders: dict[str, list[str]] = {}
    for path in _compose_files():
        unpinned = _unpinned(path)
        if unpinned:
            offenders[str(path.relative_to(REPO_ROOT))] = unpinned

    assert not offenders, (
        "unpinned container images (an unpinned image is a scheduled outage):\n"
        + "\n".join(f"  {p}: {', '.join(refs)}" for p, refs in offenders.items())
    )
