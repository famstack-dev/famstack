"""The fetch ladder — cheap first, expensive only on proven failure.

Four tiers, and the whole design is in which order they run:

    0  canonicalize   strip trackers, rewrite hosts        ~0 ms
    1  structured     the page's own JSON-LD               ~0.5 s
    2  plain HTTP     browser headers, then trafilatura    ~0.5 s
    3  stealth        a real browser, in the web extension 3.4-19.8 s

Tiers 0 to 2 are pure Python over bytes. They carry the common case
and need no service running. Tier 3 is the only step that needs a
browser, it would live in an optional extension (none ships one yet,
so nothing passes `stealth`), and it is reached only when
the gate says bot protection is in the way — never by default, and
never for a failure a browser cannot fix.

The gate runs after every tier that produces content, including tier 3.
An expensive fetch does not get to skip the check: a stealth browser
can be served a challenge page too, and an unjudged 19-second result is
no better than an unjudged instant one.

Transport is injected rather than imported. The bot has aiohttp; the
host CLI has no aiohttp at all. Both hand in a callable, so this module
depends on neither.

trafilatura is imported lazily for the same reason: the gate, the
profiles and the JSON-LD reader are stdlib, and a caller that never
reaches tier 2 never needs the dependency.

A link leads to public addresses only (`public_address`): the link
itself, the address its name resolves to, and every redirect. Link
reading runs in the bot runner, on the stack's network next to the Mac
and the home network, so a page someone shared must not send it to the
stack's services, the Mac or a device at home. Synapse holds its link
previews to the same rule.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

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

# The schemes a link may have. Everything else is refused before any
# transport runs (`fetch_url`), and the stdlib transport cannot open
# anything else either (`urllib_transport`).
WEB_SCHEMES = ("http", "https")


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


# ── Where a link may lead ─────────────────────────────────────────────

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class AddressNotPublic(Exception):
    """A link, the address its name resolves to, or a redirect leads to
    an address that is not public. Raised by a transport, before it
    connects; the ladder turns it into a verdict."""


def public_address(ip: IPAddress) -> bool:
    """Whether link reading may connect to `ip`: a public internet
    address. Loopback, the private ranges (the stack's containers, the
    home network), link-local, the shared range Tailscale uses
    (100.64.0.0/10), the Mac as Docker names it and multicast are not.
    An IPv4 address written as IPv6 (`::ffff:192.168.1.1`) counts as
    the IPv4 address."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


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

    # A link is a web page. A `file:` address would read the disk of the
    # machine that runs the ladder, which holds the family's data and the
    # stack's settings, so nothing but http and https reaches a transport.
    scheme = urlsplit(target).scheme.lower()
    if scheme not in WEB_SCHEMES:
        why = (f"only http and https addresses are read, not {scheme}:" if scheme
               else "the address does not start with http:// or https://")
        return FetchOutcome(verdict=Verdict("empty", why), url=target, tier="0",
                            profile=profile.name)

    # Tier 0.5 — a shell page whose subject is in the path. Google Maps
    # is the case: fetching it is pointless, the document is an empty
    # application and the place name is already in the URL we were
    # handed. Answering from the URL beats answering from the page.
    shell_title = title_from_url(target)

    try:
        response = await _safe(transport, target)
    except AddressNotPublic:
        return FetchOutcome(
            verdict=Verdict("empty", "the link leads into the home network; "
                                     "only public pages are read"),
            url=target, tier="2", profile=profile.name,
        )
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
    try:
        stealthed = await _safe(stealth, response.url or target)
    except AddressNotPublic:
        return outcome
    if stealthed is None:
        return outcome
    escalated = _judge(stealthed, profile, tier="3")
    return escalated if escalated.ok else outcome


async def _safe(transport: Transport, url: str) -> Response | None:
    """Run a transport, turning any transport-level failure into None.

    The ladder's contract is that it never raises; a caller rendering a
    chat reply has nothing useful to do with a socket error. A refused
    address is the one failure the ladder reports as such, so it passes.
    """
    try:
        return await transport(url, dict(BROWSER_HEADERS))
    except AddressNotPublic:
        raise
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
# Two, because the ladder has two kinds of caller. The bot runs inside a
# container that already has aiohttp and an open session. A one-shot
# read (`python -m stack.web`) has no session to reuse, so it gets the
# standard library. Neither is imported at module level.

def urllib_transport(*, timeout: int = 30,
                     reachable: Callable[[IPAddress], bool] = public_address) -> Transport:
    """A transport over the standard library, for a one-shot read.

    It needs no session, so a caller that reads one page and exits
    (`python -m stack.web`, which `stack core web fetch` runs) uses it;
    the archivist uses aiohttp (`aiohttp_transport`). urllib is
    synchronous, so the request goes to a worker thread and the ladder
    stays async for both callers.

    Every connection, the first and each redirect's, resolves its host
    and connects only to an address `reachable` allows, the one it
    checked. The default is public addresses only; a test passes a rule
    that lets its own server on this machine through.

    An HTTP error is returned rather than raised. A 403 carrying a
    Cloudflare challenge is not a transport failure — it is a page, and
    it is exactly the page the gate needs to look at.
    """
    def _blocking(url: str, headers: dict) -> Response | None:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url, headers=headers)
        try:
            with _web_only_opener(reachable).open(request, timeout=timeout) as resp:
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


def _web_only_opener(reachable: Callable[[IPAddress], bool]):
    """urllib's opener for public web pages only.

    It has no handlers for `file:`, `ftp:` and `data:`: `urlopen` reads
    all of them, and follows a redirect to `ftp:`. Here any other scheme,
    also at the end of a redirect, meets `UnknownHandler`, which raises
    `URLError`: the ladder reports that as a site that could not be
    reached. Its HTTP and HTTPS connections connect to `reachable`
    addresses only (`_checked_handlers`).
    """
    import urllib.request

    http_handler, https_handler = _checked_handlers(reachable)
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(), urllib.request.UnknownHandler(),
                    http_handler, https_handler,
                    urllib.request.HTTPDefaultErrorHandler(),
                    urllib.request.HTTPRedirectHandler(), urllib.request.HTTPErrorProcessor()):
        opener.add_handler(handler)
    return opener


def _checked_handlers(reachable: Callable[[IPAddress], bool]):
    """urllib's HTTP and HTTPS handlers, with connections that refuse an
    address `reachable` refuses.

    `http.client` opens each connection through the connection's
    `_create_connection`, so that is replaced: it resolves the host,
    refuses it when any address it resolves to is refused, and connects
    to an address it checked, so a name cannot resolve to another one in
    between. An address written as numbers resolves to itself and is
    checked the same way.
    """
    import http.client
    import urllib.request

    def connect(address, timeout, source_address=None):
        host, port = address
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        for *_, sockaddr in found:
            # A link-local IPv6 address carries its interface (`%en0`).
            if not reachable(ipaddress.ip_address(sockaddr[0].split("%")[0])):
                raise AddressNotPublic(f"{host} resolves to {sockaddr[0]}")
        error: OSError | None = None
        for *_, sockaddr in found:
            try:
                return socket.create_connection((sockaddr[0], port), timeout, source_address)
            except OSError as err:
                error = err
        raise error or OSError(f"{host} resolves to no address")

    class CheckedHTTPConnection(http.client.HTTPConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = connect

    class CheckedHTTPSConnection(http.client.HTTPSConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = connect

    class CheckedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(CheckedHTTPConnection, req)

    class CheckedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(CheckedHTTPSConnection, req, context=self._context)

    return CheckedHTTPHandler(), CheckedHTTPSHandler()


# ── aiohttp transport ─────────────────────────────────────────────────

# The statuses that send a client on, and how many hops a read follows:
# aiohttp's own limit, which the transport now enforces itself.
_REDIRECTS = (301, 302, 303, 307, 308)
_MAX_REDIRECTS = 10


def aiohttp_transport(*, timeout: int = 30,
                      reachable: Callable[[IPAddress], bool] = public_address) -> Transport:
    """A transport over aiohttp, for the bot, which has aiohttp installed.

    Each read opens a session of its own, whose resolver refuses a name
    that resolves to an address `reachable` refuses: aiohttp connects to
    the address it checked, so a name cannot resolve to another one in
    between. It follows redirects itself, one hop at a time, because
    aiohttp connects to an address written as numbers (`http://10.0.0.1/`)
    without asking the resolver: each hop's host is checked first.
    `timeout` covers the whole read, redirects included.

    `reachable` decides which addresses a read may connect to. The bot
    uses the default, public addresses only; a test passes one that lets
    its own server on 127.0.0.1 through.
    """
    async def _fetch(url: str, headers: dict) -> Response | None:
        import aiohttp

        connector = aiohttp.TCPConnector(resolver=_checked_resolver(reachable))
        async with aiohttp.ClientSession(connector=connector) as session, \
                asyncio.timeout(timeout):
            for _ in range(_MAX_REDIRECTS + 1):
                _refuse_unreachable_literal(url, reachable)
                async with session.get(url, allow_redirects=False, headers=headers) as resp:
                    location = resp.headers.get("Location")
                    if resp.status in _REDIRECTS and location:
                        url = urljoin(str(resp.url), location)
                        continue
                    return Response(
                        url=str(resp.url),
                        status=resp.status,
                        html=await resp.text(errors="replace"),
                        content_type=resp.content_type or "",
                    )
        return None

    return _fetch


def _refuse_unreachable_literal(url: str, reachable: Callable[[IPAddress], bool]) -> None:
    """Refuse a URL whose host is an address `reachable` refuses. A
    name is left to the resolver, which sees what it resolves to."""
    host = urlsplit(url).hostname or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if not reachable(ip):
        raise AddressNotPublic(f"{host} is not a public address")


def _checked_resolver(reachable: Callable[[IPAddress], bool]):
    """An aiohttp resolver that refuses a name when any address it
    resolves to is refused by `reachable`. Any, not all: a name with one
    public and one private address could be answered with the private
    one when the connection is made."""
    import aiohttp
    from aiohttp.abc import AbstractResolver

    class _CheckedResolver(AbstractResolver):
        def __init__(self) -> None:
            self._resolver = aiohttp.ThreadedResolver()

        async def resolve(self, host: str, port: int = 0,
                          family: socket.AddressFamily = socket.AF_INET):
            hosts = await self._resolver.resolve(host, port, family)
            for found in hosts:
                # A link-local IPv6 address carries its interface (`%en0`).
                if not reachable(ipaddress.ip_address(found["host"].split("%")[0])):
                    raise AddressNotPublic(f"{host} resolves to {found['host']}")
            return hosts

        async def close(self) -> None:
            await self._resolver.close()

    return _CheckedResolver()
