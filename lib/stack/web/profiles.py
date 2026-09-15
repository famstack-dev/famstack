"""Per-domain rules — the part of web reading that is not general.

Two things vary by site and nothing else does: which URL actually
serves the content, and how hard to pull on the extractor. Both are
small, both are data, and both belong somewhere a new site costs one
entry rather than a branch in the fetch ladder.

Tier 0 is the canonical URL. It is free, it runs before any request,
and it is the single highest-yield step in the whole ladder: stripping
a tracker parameter turns two URLs into one cache key, and rewriting a
host can turn a JavaScript shell into a document.

Stdlib only: the host CLI runs this without a virtualenv.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from stack.web.quality import DEFAULT_MIN_CHARS


# ── Tracking parameters ───────────────────────────────────────────────
#
# Campaign and referrer tags identify the person who shared the link,
# not the page. Dropping them is a privacy measure as much as a
# normalisation one: the URL ends up in the vault and in chat, and it
# should not carry "Homer clicked this from a newsletter" with it.

_TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "matomo_", "ga_", "_hs")
_TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "twclid", "igshid", "ttclid",
    "mc_cid", "mc_eid", "ref", "ref_src", "ref_url", "referrer", "source",
    "share_id", "si", "spm", "cmpid", "icid", "trk", "yclid", "vero_id",
})


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PREFIXES)


def strip_tracking(url: str) -> str:
    """Drop campaign/referrer parameters, keep everything else.

    Conservative on purpose. A query string is often load-bearing (an
    article id, a search term, a page number), so only known-tracking
    keys go; anything unrecognised stays.
    """
    split = urlsplit(url)
    if not split.query:
        return url
    kept = [(k, v) for k, v in parse_qsl(split.query, keep_blank_values=True)
            if not _is_tracking(k)]
    return urlunsplit(split._replace(query=urlencode(kept)))


# ── Profiles ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Profile:
    """What we know about reading one family of sites.

    `hosts` are matched as suffixes, so a profile for `reddit.com`
    covers `old.reddit.com` and `www.reddit.com` alike.

    `rewrite_host` swaps the host before fetching. `favor_recall` and
    `include_comments` are handed to trafilatura: precision is the right
    default for an article, recall is the right default for a page whose
    content *is* the discussion under it.

    `min_chars` lowers the gate's empty floor for sites that genuinely
    publish short pages.

    `blocked_note` is prose shown to the family when this site fails the
    gate. Generic advice ("the site blocked us") is worse than naming
    the actual reason where we already know it.
    """

    name: str
    hosts: tuple[str, ...] = ()
    rewrite_host: str | None = None
    favor_recall: bool = False
    include_comments: bool = False
    min_chars: int = DEFAULT_MIN_CHARS
    blocked_note: str | None = None
    # Path prefixes whose content lives in the URL rather than the body.
    # A Google Maps place is the worked example: the document is an
    # empty application shell, and the place name is right there in the
    # path we were given.
    title_from_path: tuple[str, ...] = field(default=())


DEFAULT = Profile(name="default")

PROFILES: tuple[Profile, ...] = (
    # Reddit serves anonymous readers a JavaScript shell on `www` and,
    # since measurement, a 302 to `/login?reason=lor2` on `old`. Neither
    # yields a post. The rewrite is kept anyway, and deliberately: it
    # moves the failure from `empty` ("the page was blank", true but
    # unhelpful) to `login` ("reddit wants you signed in", actionable).
    # If reddit relaxes anonymous access, the rewrite starts working
    # again with no change here. Recall settings are ready for that day
    # — measured at the time: precision returned 821 characters of
    # sidebar, recall returned the 5513-character post.
    Profile(
        name="reddit",
        hosts=("reddit.com",),
        rewrite_host="old.reddit.com",
        favor_recall=True,
        include_comments=True,
        blocked_note="reddit no longer serves posts to readers who are not signed in",
    ),
    # Google Maps place URLs are an application shell: HTTP 200, a full
    # document, and no prose but the site-wide meta description. The
    # place name is in the path, so the URL is the content.
    Profile(
        name="google-maps",
        hosts=("google.com", "google.de", "maps.app.goo.gl"),
        title_from_path=("/maps/place/",),
        blocked_note="Google Maps renders in the browser, so there is no page text to file",
    ),
)


def profile_for(url: str) -> Profile:
    """The profile governing this URL, or the default.

    Host suffix match, so `old.reddit.com` and `www.reddit.com` both
    resolve to the reddit profile.
    """
    host = urlsplit(url).netloc.lower().split(":")[0]
    for profile in PROFILES:
        if any(host == h or host.endswith("." + h) for h in profile.hosts):
            return profile
    return DEFAULT


# ── Tier 0 ────────────────────────────────────────────────────────────

def canonicalize(url: str) -> str:
    """The URL we should actually fetch. Costs nothing, runs first.

    Strips tracking parameters and applies the profile's host rewrite.
    Shortener resolution is *not* done here: it needs a request, so it
    belongs to the fetch step, and this function stays pure.
    """
    cleaned = strip_tracking(url.strip())
    profile = profile_for(cleaned)
    if profile.rewrite_host:
        split = urlsplit(cleaned)
        if split.netloc.lower() != profile.rewrite_host:
            cleaned = urlunsplit(split._replace(netloc=profile.rewrite_host))
    return cleaned


def title_from_url(url: str) -> str | None:
    """A human title recovered from the path, for shell pages.

    Returns None unless the URL matches a profile's `title_from_path`,
    so this never guesses at an ordinary article URL — a slug makes a
    poor title when the document has a real one.
    """
    profile = profile_for(url)
    if not profile.title_from_path:
        return None
    path = urlsplit(url).path
    for prefix in profile.title_from_path:
        if prefix not in path:
            continue
        tail = path.split(prefix, 1)[1].split("/")[0]
        name = unquote(tail).replace("+", " ").strip()
        if name:
            return name
    return None
