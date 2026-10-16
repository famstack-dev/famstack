"""How famstack reads the web.

A pasted link becomes a good vault entry or an honest link card, and
never a cookie policy. The ladder that decides which is in `fetch`; the
rule that nothing unjudged reaches the vault is in `quality`.

    content     SourceContent, the shape every capture source produces
    quality     the gate: ok / challenge / login / consent / paywall / empty
    profiles    per-domain rules — canonical URL, extraction tuning
    structured  JSON-LD Recipe and Article, read without an LLM
    fetch       the ladder that runs them in order

See `docs/design/web/plan.md` for the measurements behind each tier.
"""

from stack.web.content import SourceContent
from stack.web.fetch import FetchOutcome, fetch_url, urllib_transport
from stack.web.profiles import canonicalize, profile_for
from stack.web.quality import Page, Verdict, assess

__all__ = [
    "FetchOutcome",
    "Page",
    "SourceContent",
    "Verdict",
    "assess",
    "canonicalize",
    "fetch_url",
    "profile_for",
    "urllib_transport",
]
