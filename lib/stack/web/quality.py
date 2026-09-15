"""The quality gate — what may become a vault entry, and what may not.

The archivist's URL capture used to succeed whenever the extractor
returned a non-empty string. Three different pages return a non-empty
string without being articles:

    a Cloudflare interstitial   403, "Just a moment...", a spinner
    a login wall                200, 320 KB, titled "Welcome to Reddit"
    a JavaScript shell          200, a full document, no prose

All three were filed, summarised by the classifier, and given a title.
The family ended up with wiki entries about cookie policies. This
module is the fix: nothing reaches the vault without being named first.

The verdict is a value rather than a boolean because two readers need
it. The chat reply says something different for "this site wants you
logged in" than for "this site is checking your browser", and whoever
reads the logs later needs to know which sites fail which way.

    ok          there is a body worth filing
    challenge   bot protection is between us and the page
    login       the site redirected us to a sign-in
    consent     a cookie or consent wall is being served instead
    paywall     the content exists but is not ours to read
    empty       we got a page and it had nothing on it

Only `challenge` is worth escalating to a browser tier. The rest are
terminal no matter how expensively we fetch them, which is what keeps
tier 3 from being a retry loop.

Detection keys on structure, never on prose. An article *about*
Cloudflare contains every word its challenge page does; what it does not
contain is Cloudflare's DOM ids, its challenge host, or that exact
`<title>`. Where a structural signal exists off-page -- the URL a
redirect chain ended on -- it is preferred, because it is the one part
of a JavaScript shell that cannot be styled away.

Stdlib only: the host CLI runs this without a virtualenv.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# A body shorter than this is furniture — a nav rail, a cookie notice,
# a subreddit sidebar. Measured: trafilatura on a blocked Reddit page
# returned 821 characters of sidebar and read as content to everything
# downstream. Callers may lower it per profile; see `assess`.
DEFAULT_MIN_CHARS = 250


@dataclass(frozen=True)
class Page:
    """One fetched page, as the gate sees it.

    `url` is the URL the fetch *ended* on, not the one it started
    with. A login wall is a redirect, so the requested URL says nothing
    and the landing URL says everything.

    `text` is the extracted body when tier 2 has run, and empty before
    that. `html` is always the raw document: the structural markers the
    gate reads (a `<title>`, a challenge script host) are stripped out
    by extraction, so both are needed.
    """

    url: str
    status: int = 200
    html: str = ""
    text: str = ""
    content_type: str = "text/html"


@dataclass(frozen=True)
class Verdict:
    """A named outcome plus the sentence that explains it."""

    name: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.name == "ok"

    @property
    def escalate(self) -> bool:
        """Whether a browser tier could plausibly do better.

        Only bot protection qualifies. A paywall, a login wall and an
        empty page cost the same in a headless browser as they do over
        plain HTTP, so escalating them would buy a slower failure.
        """
        return self.name == "challenge"


# ── Challenge ─────────────────────────────────────────────────────────
#
# Cloudflare's interstitial is the one we measured, and it identifies
# itself three ways that survive a localisation change: the element id
# it puts its error text in, the host it loads the widget from, and the
# managed-challenge title. Any one is conclusive; prose is not used.

_CHALLENGE_MARKERS = (
    "challenges.cloudflare.com",
    "challenge-platform",
    "cf-browser-verification",
    "challenge-error-text",
    "_cf_chl_opt",
)

# Exact titles served by challenge pages. Compared whole, so a post
# titled "How Cloudflare's Just a moment page works" does not match.
_CHALLENGE_TITLES = {
    "just a moment...",
    "just a moment",
    "attention required! | cloudflare",
    "access denied",
    "checking your browser before accessing",
    "one more step",
}

_TITLE_RE = re.compile(r"<title[^>]*>([^<]*)</title>", re.IGNORECASE | re.DOTALL)


def page_title(html: str) -> str | None:
    """The document's `<title>`, trimmed. None when absent or blank."""
    match = _TITLE_RE.search(html or "")
    if not match:
        return None
    return match.group(1).strip() or None


def _is_challenge(page: Page) -> str | None:
    title = (page_title(page.html) or "").strip().lower()
    if title in _CHALLENGE_TITLES:
        return f"bot protection served {title!r} instead of the page"
    for marker in _CHALLENGE_MARKERS:
        if marker in page.html:
            return f"bot protection detected ({marker})"
    return None


# ── Login ─────────────────────────────────────────────────────────────
#
# Detected from the landing URL, because that is where the signal
# actually is. Reddit's wall is the worked example: HTTP 200, a large
# document, a friendly title, and the only honest thing about it is
# that the redirect chain ended on `/login`.

_LOGIN_PATHS = ("/login", "/signin", "/sign-in", "/sign_in", "/auth/login", "/accounts/login")


def _is_login(page: Page) -> str | None:
    split = urlsplit(page.url or "")
    path = split.path.rstrip("/").lower()
    if any(path == p or path.endswith(p) for p in _LOGIN_PATHS):
        return f"the site redirected to a sign-in page ({split.netloc}{split.path})"
    if page.status in (401, 403) and "login" in split.query.lower():
        return "the site requires a sign-in"
    return None


# ── Consent ───────────────────────────────────────────────────────────
#
# A consent *wall* is a different host, not a banner. Nearly every
# European site carries a banner in the same document as its content;
# treating those as blocks would reject most of the web. So the signal
# is the dedicated consent host a site bounces to.

_CONSENT_HOSTS = ("consent.google.com", "consent.youtube.com", "consent.yahoo.com")


def _is_consent(page: Page) -> str | None:
    host = urlsplit(page.url or "").netloc.lower()
    if host in _CONSENT_HOSTS:
        return f"the site served a consent wall at {host}"
    return None


# ── Paywall ───────────────────────────────────────────────────────────
#
# 402 is the unambiguous case and the only one detected here. Prose
# detection ("Subscribe to continue reading") is deliberately absent:
# most paywalled pages carry that string *alongside* a readable
# excerpt, and an excerpt is still worth filing.

def _is_paywall(page: Page) -> str | None:
    if page.status == 402:
        return "the site returned 402 Payment Required"
    return None


# ── The gate ──────────────────────────────────────────────────────────

def assess(page: Page, *, min_chars: int = DEFAULT_MIN_CHARS) -> Verdict:
    """Classify a fetched page. Never raises.

    Order is deliberate. A challenge page is also short, and a login
    wall is also nearly empty, so `empty` has to be the last thing
    checked -- otherwise every block would be reported as "the page was
    blank", which is true and useless.

    `min_chars` is the floor below which a body is assumed to be
    furniture rather than an article. It is an argument so a site
    profile can lower it for a domain that legitimately publishes short
    pages, instead of that domain having to skip the gate.
    """
    for check in (_is_challenge, _is_login, _is_consent, _is_paywall):
        detail = check(page)
        if detail:
            return Verdict(check.__name__.removeprefix("_is_"), detail)

    body = (page.text or "").strip()
    if len(body) < min_chars:
        got = f"{len(body)} characters" if body else "nothing"
        return Verdict("empty", f"the page yielded {got}, below the {min_chars}-character floor")

    return Verdict("ok", f"extracted {len(body)} characters")
