"""install_release: the offer of a release before a first install from `main`.

A person who clones the project and runs `./stack` is on `main`. `main` is
the work in progress, not a tested release. The installer calls this
module once, after its welcome and before it asks anything else:

    from .install_release import offer_a_release

    if not offer_a_release(REPO_ROOT, sys.argv[1:]):
        return None          # the person ended the question: stop

On a fresh clone of `main` with commits after the newest release tag, the
person sees the newest releases and picks one:

    You are on main. main is the work in progress, not a tested release.
    A release is tested. ./stack update moves an install from release to release.

    Which version do you want to install?
      1) v0.5.0-beta.1, the newest pre-release (recommended) (default)
      2) v0.4.4, the newest final release
      3) main, the work in progress
    › Number [1]

A pick of a release checks out its tag and starts the `./stack` of that
tag with the same arguments, in place of this process. Thus the
installer of the release runs, and the installer of `main` stops. The
reason is that the code on disk and the code that runs must be the same.
`stack update` then moves the install from release to release.

Why this order of offers: during the pre-releases, a new install starts on
the newest pre-release. A final release that is newer than every
pre-release is the only offer, because an older beta is never the right
start. The order of tags comes from `stack.updater`
(`version_key`), the same order that `stack update` uses.

When this module asks nothing:

    the checkout has a stack.toml   a set-up install, or one that stopped:
                                    its code changes only with `stack update`
    a branch other than main        a branch under test (the e2e runs)
    a tag (a detached HEAD)         already a release
    main exactly on the newest tag  already a release
    main with local edits           a switch would carry them into another
                                    release, or stop halfway: it says so
    main without release tags       a shallow clone: it says so

The script install (`./stack install --family <file> --yes`) never calls
this module, because the e2e runs install a branch under test that way.
This module fetches nothing: a clone has its tags, and the installer works
offline until the person agrees to start.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .prompt import BOLD, RESET, TEAL, choose, dim, nl, out, warn
from .updater import Checkout, is_prerelease, is_release, latest_tag, version_key

__all__ = ["Offer", "offer_for", "offer_a_release"]

# The branch of a fresh clone. Every other branch is somebody's work.
MAIN = "main"


# ── The offer ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Offer:
    """The releases that a fresh clone of `main` is offered, newest first.

    `prerelease` is the newest pre-release, only when it is newer than
    every final release. `final` is the newest final release. At least one
    of the two is set. The first one set is the recommended one.

        Offer(prerelease="v0.5.0-beta.1", final="v0.4.4").recommended
        # "v0.5.0-beta.1"
    """

    prerelease: str | None
    final: str | None

    @property
    def recommended(self) -> str:
        """The release that Enter picks, which is also the newest release."""
        return self.prerelease or self.final or ""

    @property
    def tags(self) -> list[str]:
        """The offered tags, the recommended one first."""
        return [tag for tag in (self.prerelease, self.final) if tag]


def offer_for(tags) -> Offer | None:
    """The releases to offer among `tags`, or None when no tag is a release.

    A pre-release is offered only when it is newer than the newest final
    release. Tags that are not versions (`nightly`) are ignored.

    >>> offer_for(["v0.4.4", "v0.5.0-beta.1", "v0.5.0-beta.2"])
    Offer(prerelease='v0.5.0-beta.2', final='v0.4.4')
    >>> offer_for(["v0.5.0-beta.1", "v0.4.4", "v0.5.0"])
    Offer(prerelease=None, final='v0.5.0')
    >>> offer_for(["v0.5.0-beta.1"])
    Offer(prerelease='v0.5.0-beta.1', final=None)
    >>> offer_for(["nightly"]) is None
    True
    """
    releases = [tag for tag in tags if is_release(tag)]
    if not releases:
        return None
    final = latest_tag(tag for tag in releases if not is_prerelease(tag))
    prerelease = latest_tag(tag for tag in releases if is_prerelease(tag))
    if prerelease and final and version_key(prerelease) < version_key(final):
        prerelease = None
    return Offer(prerelease=prerelease, final=final)


def offer_a_release(root: Path, argv: list[str]) -> bool:
    """Offer a release before a first install from `main`, at `root`.

    Returns True when the installer of this checkout goes on: nothing to
    offer, a notice only, or the person picked `main`. Returns False when
    the installer must stop: the input ended at the question, or git
    could not check out the picked tag. Never returns after a pick that
    git checked out: it starts `<root>/stack <argv>` of the tag in place
    of this process.

    It changes the checkout only after a pick, and only when the checkout
    has no local edits. It asks nothing when `root` has a stack.toml,
    because then its code changes only through `stack update`.

        offer_a_release(Path("~/famstack"), ["install"])
        # prints the offer. "2" checks out v0.4.4 and runs its ./stack install
    """
    # Safety: a set-up install, or one that stopped halfway, never jumps
    # to another release here. `stack update` has the checks for that.
    if (root / "stack.toml").exists():
        return True
    checkout = Checkout(root)
    if not checkout.is_git() or checkout.branch() != MAIN:
        return True
    offer = offer_for(checkout.tags())
    if offer is None:
        _say_no_release_found()
        return True
    if checkout.commits_ahead_of(offer.recommended) == 0:
        return True
    if checkout.is_dirty():
        _say_local_edits_stay(checkout.dirty_paths())
        return True
    return _ask_and_switch(checkout, offer, argv)


# ── Asking ───────────────────────────────────────────────────────────────────

def _ask_and_switch(checkout: Checkout, offer: Offer, argv: list[str]) -> bool:
    """Ask which version to install, then switch to it or stay on `main`."""
    picked = _ask(offer)
    if picked is None:
        nl()
        out("Nothing was changed. Run ./stack again when you are ready.")
        return False
    if picked == MAIN:
        _say_main_stays()
        return True
    return _switch(checkout, picked, argv)


def _ask(offer: Offer) -> str | None:
    """The picked tag, `MAIN`, or None when the input ended."""
    nl()
    out(f"{BOLD}You are on {MAIN}.{RESET} {MAIN} is the work in progress, not a tested release.")
    out("A release is tested. ./stack update moves an install from release to release.")
    labels = []
    if offer.prerelease:
        labels.append(f"{offer.prerelease}, the newest pre-release (recommended)")
    if offer.final:
        recommended = "" if offer.prerelease else " (recommended)"
        labels.append(f"{offer.final}, the newest final release{recommended}")
    labels.append(f"{MAIN}, the work in progress")
    index = choose("Which version do you want to install?", labels, default=0)
    if index is None:
        return None
    return [*offer.tags, MAIN][index]


def _say_main_stays() -> None:
    nl()
    dim(f"This install stays on {MAIN}. ./stack update offers releases only: it does not")
    dim(f"move the checkout along {MAIN}. To get new work on {MAIN}, use git.")
    nl()


def _say_no_release_found() -> None:
    nl()
    dim("No release was found in this clone, for example because it is shallow.")
    dim(f"The installer installs the checkout as it is: {MAIN}, the work in progress.")
    nl()


def _say_local_edits_stay(paths: list[str]) -> None:
    nl()
    warn(f"This clone of {MAIN} has local edits, so the installer does not offer a release:")
    for path in paths:
        out(f"     {path}")
    out(f"   The installer installs the checkout as it is: {MAIN}, with these edits.")
    nl()


# ── Switching ────────────────────────────────────────────────────────────────

def _switch(checkout: Checkout, tag: str, argv: list[str]) -> bool:
    """Check out `tag` and start its `./stack` in place of this process.

    Returns False only when git refuses the checkout, after it says why.
    The process of `main` must not go on after the switch: its code is no
    longer the code on disk. When the `./stack` of the tag cannot start,
    it names the tag of the checkout and the command back to `main`. Then
    it ends this process with exit code 1.
    """
    nl()
    out(f"Switching to {TEAL}{tag}{RESET}.")
    ok, detail = checkout.checkout(tag)
    if not ok:
        warn(f"git could not check out {tag}. Nothing was installed.")
        dim(f"   {detail}")
        return False
    nl()
    stack = str(checkout.root / "stack")
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execv(stack, [stack, *argv])
    except OSError as problem:
        _say_the_release_did_not_start(tag, problem)
        raise SystemExit(1) from None


def _say_the_release_did_not_start(tag: str, problem: OSError) -> None:
    """The checkout moved, but the installer of `tag` did not start."""
    nl()
    warn(f"The checkout is now on {tag}, but its ./stack did not start: {problem.strerror}.")
    out("   Nothing was installed. To go back to the work in progress, run:")
    out(f"     git checkout {MAIN}")
    nl()
