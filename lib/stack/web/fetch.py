"""The fetch ladder — cheap first, expensive only on proven failure.

Four tiers, and the whole design is in which order they run:

    0  canonicalize   strip trackers, rewrite hosts        ~0 ms
    1  structured     the page's own JSON-LD               ~0.5 s
    2  plain HTTP     browser headers, then trafilatura    ~0.5 s
    3  stealth        a real browser, in the web stacklet  3.4-19.8 s

Tiers 0 to 2 are pure Python over bytes. They carry the common case,
they need nothing running, and they are why `stack web fetch` works on
a machine with no containers up. Tier 3 is the only step that needs a
browser, it lives in an optional stacklet, and it is reached only when
the gate says bot protection is in the way — never by default, and
never for a failure a browser cannot fix.

The gate runs after every tier that produces content, including tier 3.
An expensive fetch does not get to skip the check: a stealth browser
can be served a challenge page too, and an unjudged 19-second result is
no better than an unjudged instant one.

Transport is injected rather than imported. The bot has an aiohttp
session already open and should reuse it; the host CLI has no aiohttp
at all. Both hand in a callable, so this module depends on neither.

trafilatura is imported lazily for the same reason: the gate, the
profiles and the JSON-LD reader are stdlib, and a caller that never
reaches tier 2 never needs the dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from stack.web.content import SourceContent
from stack.web.profiles import Profile, canonicalize, profile_for, title_from_url
from stack.web.quality import Page, Verdict, assess, page_title

# The headers a browser sends. Not a disguise — plenty of CDNs answer a
# bare library user-agent with a 403 out of habit, and this is the
# difference between reading a public page and not. Sites that actually
# check (decathlon) are unmoved by it; that is what tier 3 is for.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


class ExtractorUnavailable(RuntimeError):
    """Tier 2 cannot run because its dependency is missing.

    Deliberately not a `Verdict`. The gate judges pages, and this says
    nothing about the page — it says our install is wrong. Collapsing
    the two is what let a missing dependency masquerade as an empty
    article.
    """


@dataclass
class Response:
    """What a transport hands back. Deliberately minimal — the ladder
    needs the landing URL (a login redirect is only visible there), the
    status, the body and the content type, and nothing else."""

    url: str
    status: int
    html: str
    content_type: str = "text/html"


# A transport takes a URL plus headers and returns a Response, or None
# when the request could not be made at all (DNS, timeout, refused).
Transport = Callable[[str, dict], Awaitable["Response | None"]]


@dataclass
class FetchOutcome:
    """The result of running the ladder.

    `content` is present only when the gate passed. `verdict` is always
    present and always explains itself, so a caller rendering a link
    card has something true to say about why there is no entry.

    `tier` records which rung produced the outcome, for the logs: a
    site that starts needing tier 3 is worth noticing.
    """

    verdict: Verdict
    content: SourceContent | None = None
    url: str = ""
    tier: str = ""
    profile: str = "default"

    @property
    def ok(self) -> bool:
        return self.content is not None and self.verdict.ok


# ── Tier 2 extraction ─────────────────────────────────────────────────

def extract_body(
    html: str, *, favor_recall: bool = False, include_comments: bool = False,
) -> str | None:
    """HTML to Markdown via trafilatura, tuned by profile.

    Precision is the default because most pages are articles surrounded
    by furniture. Recall is for pages whose content *is* the discussion
    under them — measured on a Reddit post, precision returned 821
    characters of sidebar and recall returned the 5513-character post.
    The per-domain profile decides; this function only applies it.

    Returns None when there is no body to find. Raises
    `ExtractorUnavailable` when trafilatura is not installed, which is a
    different thing and must not be confused with it.

    An earlier version returned None for both. The gate then reported a
    perfectly good page as "empty — the page yielded nothing", because
    from its side an absent extractor and an absent article look
    identical. That shipped, and `stack web fetch` silently answered
    "empty" for every page on the host, where trafilatura is not
    installed; the only URLs that appeared to work were the ones served
    by tier 0 and tier 1, which need no extractor. A broken install must
    not be able to impersonate a verdict about somebody's web page.
    """
    try:
        import trafilatura
    except ImportError as e:
        raise ExtractorUnavailable(
            "trafilatura is not installed, so page text cannot be extracted"
        ) from e

    kwargs = {
        "output_format": "markdown",
        "include_links": True,
        "include_images": False,
        "include_tables": True,
        "include_comments": include_comments,
    }
    # trafilatura rejects both favour flags at once; recall wins where a
    # profile asked for it, precision is the default everywhere else.
    if favor_recall:
        kwargs["favor_recall"] = True
    else:
        kwargs["favor_precision"] = True

    body = trafilatura.extract(html, **kwargs)
    return body.strip() if body and body.strip() else None


def _is_html(content_type: str) -> bool:
    ctype = (content_type or "").lower()
    return ctype.startswith("text/html") or ctype.startswith("application/xhtml")


# ── The ladder ────────────────────────────────────────────────────────

async def fetch_url(
    url: str,
    *,
    transport: Transport,
    stealth: Transport | None = None,
) -> FetchOutcome:
    """Run the ladder and return a judged outcome. Never raises.

    `stealth` is tier 3. Passing None — which is what happens when the
    `web` stacklet is not installed — means a challenge is terminal and
    the caller renders a link card saying so. That is the honest
    degradation: the family is told the site blocked us, rather than
    getting a silent failure or an entry built from the challenge page.
    """
    target = canonicalize(url)
    profile = profile_for(target)

    # Tier 0.5 — a shell page whose subject is in the path. Google Maps
    # is the case: fetching it is pointless, the document is an empty
    # application and the place name is already in the URL we were
    # handed. Answering from the URL beats answering from the page.
    shell_title = title_from_url(target)

    response = await _safe(transport, target)
    if response is None:
        if shell_title:
            return _from_url_only(target, shell_title, profile)
        return FetchOutcome(
            verdict=Verdict("empty", "the site could not be reached"),
            url=target, tier="2", profile=profile.name,
        )

    outcome = _judge(response, profile)
    if outcome.ok or not outcome.verdict.escalate or stealth is None:
        if not outcome.ok and shell_title:
            return _from_url_only(target, shell_title, profile)
        return outcome

    # Tier 3 — only for a challenge, only once. There is no loop back to
    # tier 2: if the browser is also served a challenge, the answer is
    # that we cannot read this page.
    stealthed = await _safe(stealth, response.url or target)
    if stealthed is None:
        return outcome
    escalated = _judge(stealthed, profile, tier="3")
    return escalated if escalated.ok else outcome


async def _safe(transport: Transport, url: str) -> Response | None:
    """Run a transport, turning any transport-level failure into None.

    The ladder's contract is that it never raises; a caller rendering a
    chat reply has nothing useful to do with a socket error.
    """
    try:
        return await transport(url, dict(BROWSER_HEADERS))
    except Exception:  # noqa: BLE001 — transports raise library-specific errors
        return None


def _judge(response: Response, profile: Profile, *, tier: str = "2") -> FetchOutcome:
    """Tiers 1 and 2 over one response, then the gate.

    Structured data is tried first and short-circuits: when a site hands
    us its own `Recipe` object there is no reason to guess at the
    rendered page, and no reason to ask the gate whether the guess was
    any good.
    """
    if not _is_html(response.content_type):
        return FetchOutcome(
            verdict=Verdict("empty", f"the URL served {response.content_type}, not a web page"),
            url=response.url, tier=tier, profile=profile.name,
        )

    structured = _structured(response)
    if structured is not None:
        return FetchOutcome(
            verdict=Verdict("ok", "the page published its own structured data"),
            content=structured, url=response.url, tier="1", profile=profile.name,
        )

    # Deliberately not caught. A missing extractor is an install fault,
    # and the honest response is to say so rather than to publish a
    # judgement about the page that we are not equipped to make.
    body = extract_body(
        response.html,
        favor_recall=profile.favor_recall,
        include_comments=profile.include_comments,
    )
    page = Page(
        url=response.url, status=response.status, html=response.html,
        text=body or "", content_type=response.content_type,
    )
    verdict = assess(page, min_chars=profile.min_chars)
    if not verdict.ok:
        detail = verdict.detail
        if profile.blocked_note:
            detail = f"{detail} — {profile.blocked_note}"
        return FetchOutcome(
            verdict=Verdict(verdict.name, detail),
            url=response.url, tier=tier, profile=profile.name,
        )

    return FetchOutcome(
        verdict=verdict,
        content=SourceContent(
            text=body or "",
            mime="text/html",
            title_hint=page_title(response.html),
            source_uri=response.url,
        ),
        url=response.url, tier=tier, profile=profile.name,
    )


def _structured(response: Response) -> SourceContent | None:
    from stack.web.structured import read_structured

    return read_structured(response.html, url=response.url)


def _from_url_only(url: str, title: str, profile: Profile) -> FetchOutcome:
    """An entry built from the URL, for pages that have no text to read.

    The body is the one true sentence we can say about the place: its
    name and where the link points. The classifier gets a real title
    instead of "Google Maps", and the family gets an entry they can
    find again.
    """
    return FetchOutcome(
        verdict=Verdict("ok", "the page has no text; its subject came from the URL"),
        content=SourceContent(
            text=f"{title}\n\n{url}",
            mime="text/markdown",
            title_hint=title,
            source_uri=url,
        ),
        url=url, tier="0", profile=profile.name,
    )


# ── Transports ────────────────────────────────────────────────────────
#
# Two, because the ladder has two callers with incompatible
# environments. The bot runs inside a container that already has
# aiohttp and an open session. The host CLI runs on a Mac with no
# virtualenv and possibly nothing up at all, so it gets the standard
# library. Neither is imported at module level.

def urllib_transport(*, timeout: int = 30) -> Transport:
    """A transport over the standard library, for the host CLI.

    `stack web fetch` has to work with no containers running and no
    third-party packages installed, which rules out aiohttp. urllib is
    synchronous, so the request goes to a worker thread and the ladder
    stays async for both callers.

    An HTTP error is returned rather than raised. A 403 carrying a
    Cloudflare challenge is not a transport failure — it is a page, and
    it is exactly the page the gate needs to look at.
    """
    def _blocking(url: str, headers: dict) -> Response | None:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return Response(
                    url=resp.url,
                    status=resp.status,
                    html=resp.read().decode("utf-8", errors="replace"),
                    content_type=resp.headers.get_content_type(),
                )
        except urllib.error.HTTPError as err:
            return Response(
                url=err.url,
                status=err.code,
                html=err.read().decode("utf-8", errors="replace"),
                content_type=err.headers.get_content_type() if err.headers else "text/html",
            )

    async def _fetch(url: str, headers: dict) -> Response | None:
        import asyncio

        return await asyncio.to_thread(_blocking, url, headers)

    return _fetch


# ── aiohttp transport ─────────────────────────────────────────────────

def aiohttp_transport(session, *, timeout: int = 30) -> Transport:
    """A transport over an already-open aiohttp session.

    The bot keeps one session for its lifetime; handing it in rather
    than opening a new one per fetch keeps connection reuse and, more
    importantly, keeps this module free of an aiohttp import at module
    level so the host CLI can import the ladder without it.
    """
    async def _fetch(url: str, headers: dict) -> Response | None:
        import aiohttp

        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
            headers=headers,
        ) as resp:
            return Response(
                url=str(resp.url),
                status=resp.status,
                html=await resp.text(errors="replace"),
                content_type=resp.content_type or "",
            )

    return _fetch
