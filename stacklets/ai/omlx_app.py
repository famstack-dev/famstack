"""The oMLX app this release is tested with, and where its disk image is.

    from ..omlx_app import TESTED_OMLX, app_download, this_macos

    app_download("15.5")
    # {"version": "0.7.3", "disk_image": True,
    #  "url": "https://github.com/jundot/omlx/releases/download/v0.7.3/oMLX-0.7.3-macos15-sequoia.dmg"}
    app_download(this_macos())        # the same for the Mac this runs on

The setup page's AI step offers the oMLX app with a "Download oMLX"
button. That button downloads one known release, the one this release of
the stack was tested with, never "the newest oMLX": oMLX publishes its
dev and rc builds as normal GitHub releases (`v0.7.4.dev1`, `v0.7.3rc1`),
so the newest release can be a build nobody tested with the stack.

Each oMLX release carries one disk image per macOS family, at
`https://github.com/jundot/omlx/releases/download/v<version>/<file>`:

    macOS     disk image
    15        oMLX-<version>-macos15-sequoia.dmg
    26, 27    oMLX-<version>-macos26-27.dmg
    other     none: the link is the release's page, where the admin chooses

The macOS version is read on the host (`this_macos`), because the AI
step's page is drawn in a container, which cannot see it. The plan of
`recommended.py` carries the answer to the page as `app`.

What it does not do: install or update the app, or check which oMLX runs.
`voices.SPEECH_OMLX` is the oldest oMLX that speaks with the stack's
voices; this is the one release the stack was tested with.
"""

from __future__ import annotations

import platform

__all__ = ["TESTED_OMLX", "app_download", "this_macos"]

# The oMLX release this release of the stack was tested with, on the e2e
# laptop's install pass. Raise it only after a release test with the new one.
TESTED_OMLX = (0, 7, 3)

# Where oMLX publishes its releases.
RELEASES = "https://github.com/jundot/omlx/releases"
# The disk image's name for each macOS major version that has one.
DISK_IMAGES = {15: "macos15-sequoia", 26: "macos26-27", 27: "macos26-27"}


def app_download(macos: str, version: tuple[int, ...] = TESTED_OMLX) -> dict:
    """Where to download oMLX `version` for a Mac on `macos` ("15.5").

    Returns `version` as a string, `url` and `disk_image`: True when `url`
    is the disk image built for that macOS, False when it is the
    release's page, because the release has no disk image for that macOS
    or `macos` is "" (unknown).

        app_download("26.1")["url"]
        # ".../releases/download/v0.7.3/oMLX-0.7.3-macos26-27.dmg"
        app_download("14.7")
        # {"version": "0.7.3", "disk_image": False,
        #  "url": "https://github.com/jundot/omlx/releases/tag/v0.7.3"}
    """
    name = ".".join(map(str, version))
    family = DISK_IMAGES.get(_major(macos))
    if family is None:
        return {"version": name, "url": f"{RELEASES}/tag/v{name}", "disk_image": False}
    return {"version": name, "disk_image": True,
            "url": f"{RELEASES}/download/v{name}/oMLX-{name}-{family}.dmg"}


def this_macos() -> str:
    """The macOS version of the Mac this runs on ("15.5"), "" when the
    system does not say (inside a container it is not macOS)."""
    return platform.mac_ver()[0]


# ── Reading a version ────────────────────────────────────────────────────

def _major(macos: str) -> int:
    """The major version of "15.5" (15), 0 for "" or anything else."""
    head = macos.split(".", 1)[0]
    return int(head) if head.isdigit() else 0
