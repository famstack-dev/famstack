"""Every container image must name an explicit version.

`:latest` plus watchtower is a scheduled outage. Paperless-ngx rolled from
the 2.x line to 3.0.2 unattended and broke document filing across the whole
e2e suite; we found out from a red test run, not from a decision.

This is the audit that would have caught it, as a test instead of a one-time
grep — it costs milliseconds and cannot silently stop being true.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# `image: repo/name:tag` in compose, ignoring commented-out lines. The tag is
# optional in the grammar precisely because omitting it means `:latest`, which
# is the case this test exists to reject.
_IMAGE_RE = re.compile(r"^\s*image:\s*[\"']?([^\s\"'#]+)", re.MULTILINE)

# Known-unpinned images, as of 2026-07-31. This is a ratchet, not an allowlist:
# the test blocks any NEW floating tag immediately, while these existing ones
# get pinned deliberately, one at a time, each verified against a running rig.
# Picking a Synapse or Element version is a real decision with a real blast
# radius; making that call to turn a test green would be the wrong order.
#
# Delete entries as they are pinned. An empty set is the goal state.
KNOWN_UNPINNED: set[str] = {
    "ghcr.io/matatonic/openedai-speech-min",  # no tag at all
    "nickfedor/watchtower:latest",
    "apache/tika:latest",
    "adguard/adguardhome:latest",
    "matrixdotorg/synapse:latest",
    "vectorim/element-web:latest",
}


def _compose_files() -> list[Path]:
    return sorted(REPO_ROOT.glob("stacklets/*/docker-compose*.yml"))


def _unpinned(text: str) -> list[str]:
    """Return image refs carrying no tag, or an explicitly floating one."""
    found = []
    for ref in _IMAGE_RE.findall(text):
        # Environment-substituted refs (${FOO}) are resolved at render time;
        # the pin lives wherever that variable is defined, not here.
        if ref.startswith("${"):
            continue
        # A digest pin (repo@sha256:...) is stricter than a tag. Accept it.
        if "@sha256:" in ref:
            continue
        # Strip the registry host before looking for the tag separator, so
        # `registry:5000/img` is not mistaken for an `img:5000` tag.
        last_segment = ref.rsplit("/", 1)[-1]
        tag = last_segment.split(":", 1)[1] if ":" in last_segment else "latest"
        if tag == "latest" and ref not in KNOWN_UNPINNED:
            found.append(ref)
    return found


def test_compose_files_exist():
    # Guard the guard: a glob that silently matches nothing would make every
    # assertion below vacuously true.
    assert _compose_files(), "no stacklet compose files found - glob is wrong"


def test_ratchet_has_no_stale_entries():
    # If an image gets pinned but its entry stays behind, the exemption sits
    # there covering a name nothing uses - and quietly re-exempts that image
    # the day someone reintroduces it. Shrinking the list must be mandatory.
    all_refs = {
        ref
        for path in _compose_files()
        for ref in _IMAGE_RE.findall(path.read_text(encoding="utf-8"))
    }
    stale = KNOWN_UNPINNED - all_refs
    assert not stale, (
        "KNOWN_UNPINNED lists images no compose file uses - pinned already? "
        f"remove them: {', '.join(sorted(stale))}"
    )


def test_no_floating_image_tags():
    offenders: dict[str, list[str]] = {}
    for path in _compose_files():
        unpinned = _unpinned(path.read_text(encoding="utf-8"))
        if unpinned:
            offenders[str(path.relative_to(REPO_ROOT))] = unpinned

    assert not offenders, (
        "unpinned container images (an unpinned image is a scheduled outage):\n"
        + "\n".join(f"  {p}: {', '.join(refs)}" for p, refs in offenders.items())
    )
