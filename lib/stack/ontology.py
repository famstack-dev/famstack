"""Generic ontology for stacks that classify and search.

An ontology is the shared vocabulary a stack uses to talk about its
content. It defines what kinds of *topics* exist (Insurance, Medical,
Tax), what *document types* exist (Invoice, Contract, Letter), with
localized names and synonyms for each.

This module is product-agnostic by design. It defines the dataclasses
and the loader; the *content* — the actual list of topics and types —
lives outside the framework, in a seed file shipped by whichever
stacklet owns the vocabulary (in famstack: `stacklets/memory/`).
A different product (deskstack, studio, freelance) supplies different
seeds against the same machinery.

Two readers care about an ontology:

  - **classifiers**, which need a prompt section listing the available
    topics and types together with synonyms so the LLM picks from the
    list and consistently recognises near-equivalents,

  - **retrievers**, which need to expand a free-form user phrase
    ("car insurance") into the canonical ids the storage backend
    actually indexes.

Both come from the same source of truth and are exposed by the
`Ontology` class below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

from ._compat import tomllib


# ─── Language helpers ────────────────────────────────────────────────────
#
# Languages are stored as two-letter ISO codes ("de", "en"). Callers
# may pass full locales ("de-DE", "en_US") — we normalise to the first
# two letters, lowercased. Anything we don't have falls back to "en";
# anything we still don't have falls back to the entity's id.

def _lang_key(lang: str) -> str:
    return (lang or "en")[:2].lower()


def _localized(values: Dict[str, str], lang: str, default: str) -> str:
    return values.get(_lang_key(lang), values.get("en", default))


# ─── Entities ────────────────────────────────────────────────────────────

@dataclass
class Topic:
    """A subject area documents and facts can be tagged with.

    `names` carries the canonical label per language. `synonyms` lists
    alternative phrasings the LLM or the user might emit. `keywords`
    are body-text signals (less canonical than synonyms; e.g. "claim"
    suggests Insurance but isn't a name for it). `types` is a soft
    cross-reference into doctypes that commonly co-occur with this
    topic — useful for retrievers narrowing a search.
    """

    id: str
    names: Dict[str, str]
    synonyms: Dict[str, List[str]] = field(default_factory=dict)
    keywords: Dict[str, List[str]] = field(default_factory=dict)
    types: List[str] = field(default_factory=list)

    def name(self, lang: str) -> str:
        return _localized(self.names, lang, self.id)

    def synonyms_for(self, lang: str) -> List[str]:
        return self.synonyms.get(_lang_key(lang), [])


@dataclass
class DocType:
    """A document shape ("Invoice", "Contract"). Unlike a topic, a
    doctype describes the form, not the subject — an Invoice can be
    about Insurance, Telecom, or Groceries.
    """

    id: str
    names: Dict[str, str]
    synonyms: Dict[str, List[str]] = field(default_factory=dict)

    def name(self, lang: str) -> str:
        return _localized(self.names, lang, self.id)

    def synonyms_for(self, lang: str) -> List[str]:
        return self.synonyms.get(_lang_key(lang), [])


# ─── Ontology container ──────────────────────────────────────────────────

@dataclass
class Ontology:
    """A loaded ontology — topics and doctypes keyed by id.

    Construction goes through `Ontology.load(path)`. The TOML schema is
    documented in the seed file shipped by the consuming stacklet.
    """

    topics: Dict[str, Topic] = field(default_factory=dict)
    doctypes: Dict[str, DocType] = field(default_factory=dict)

    # ─── Loading ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Ontology":
        """Read a TOML file and return an Ontology.

        Empty or missing sections are tolerated — a freshly-seeded
        ontology may have only topics defined, or only doctypes.
        """
        with open(path, "rb") as f:
            return cls._from_data(tomllib.load(f))

    @classmethod
    def loads(cls, text: str) -> "Ontology":
        """Parse TOML text and return an Ontology.

        Used by the live loader after fetching `ontology.toml` from
        Forgejo (where the content comes back as a base64-decoded
        string, not a path on disk).
        """
        return cls._from_data(tomllib.loads(text))

    @classmethod
    def _from_data(cls, data: dict) -> "Ontology":
        topics = {
            tid: _parse_topic(tid, raw)
            for tid, raw in (data.get("topic") or {}).items()
        }
        doctypes = {
            did: _parse_doctype(did, raw)
            for did, raw in (data.get("doctype") or {}).items()
        }
        return cls(topics=topics, doctypes=doctypes)

    # ─── Resolution ───────────────────────────────────────────────────
    #
    # Resolution is case-insensitive exact-match against the canonical
    # name and the language's synonym list. We don't do fuzzy matching
    # here — that's a retriever concern with a different policy.

    def resolve_topic(self, text: str, lang: str = "en") -> Optional[Topic]:
        norm = (text or "").strip().lower()
        if not norm:
            return None
        for topic in self.topics.values():
            if topic.name(lang).lower() == norm:
                return topic
            if any(syn.lower() == norm for syn in topic.synonyms_for(lang)):
                return topic
        return None

    def resolve_doctype(self, text: str, lang: str = "en") -> Optional[DocType]:
        norm = (text or "").strip().lower()
        if not norm:
            return None
        for dt in self.doctypes.values():
            if dt.name(lang).lower() == norm:
                return dt
            if any(syn.lower() == norm for syn in dt.synonyms_for(lang)):
                return dt
        return None

    # ─── Prompt rendering ─────────────────────────────────────────────

    def classifier_prompt_section(self, lang: str = "en") -> str:
        """Render the topic + doctype lists as a prompt fragment.

        Output shape (illustrative, lang='en'):

            Topics (pick the best match; synonyms in parentheses):
              - Insurance (policy, coverage)
              - Tax (taxation)
              - Medical

            Document types:
              - Invoice (bill)
              - Contract
              - Policy

        The LLM is asked to return topic ids (or names — both resolve
        through `resolve_topic`) and doctype ids the same way.
        """
        return _render_classifier_section(self.topics, self.doctypes, lang)


# ─── Parsing helpers ─────────────────────────────────────────────────────

def _parse_topic(tid: str, raw: dict) -> Topic:
    return Topic(
        id=tid,
        names=dict(raw.get("names") or {}),
        synonyms=_as_lang_lists(raw.get("synonyms")),
        keywords=_as_lang_lists(raw.get("keywords")),
        types=list(raw.get("types") or []),
    )


def _parse_doctype(did: str, raw: dict) -> DocType:
    return DocType(
        id=did,
        names=dict(raw.get("names") or {}),
        synonyms=_as_lang_lists(raw.get("synonyms")),
    )


def _as_lang_lists(raw: Optional[dict]) -> Dict[str, List[str]]:
    """Normalise `{ "en": [...], "de": [...] }` while tolerating absence."""
    if not raw:
        return {}
    return {lang: list(items or []) for lang, items in raw.items()}


# ─── Rendering ───────────────────────────────────────────────────────────

def _render_classifier_section(
    topics: Dict[str, Topic],
    doctypes: Dict[str, DocType],
    lang: str,
) -> str:
    """Compose the prompt fragment from in-memory entities.

    Kept as a free function so it can be unit-tested against a hand-
    rolled mapping without going through TOML.
    """
    blocks: List[str] = []

    if topics:
        lines = ["Topics (pick the best match; synonyms in parentheses):"]
        for t in topics.values():
            label = t.name(lang)
            syns = t.synonyms_for(lang)
            lines.append(f"  - {label}" + (f" ({', '.join(syns)})" if syns else ""))
        blocks.append("\n".join(lines))

    if doctypes:
        lines = ["Document types:"]
        for dt in doctypes.values():
            label = dt.name(lang)
            syns = dt.synonyms_for(lang)
            lines.append(f"  - {label}" + (f" ({', '.join(syns)})" if syns else ""))
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)
