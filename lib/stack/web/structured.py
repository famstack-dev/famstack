"""JSON-LD — the content a site already handed us, structured.

Recipe sites publish schema.org markup because Google asks them to.
That markup is better than anything an extractor can recover from the
rendered page: the ingredients are a list rather than a paragraph, the
quantities are attached to them, and the steps are in order. It is also
free, deterministic, and needs no model.

Measured on two German recipe sites: both served a complete `Recipe`
object to a plain fetch with no bot wall — yield, total time, 11 to 14
ingredients with quantities, the instruction steps, and nutrition.

So this runs before general extraction, and when it hits, the page is
never parsed as prose at all.

The awkward part is that a listing page carries `Recipe` objects too:
"our 30 best apple cakes" embeds thirty of them, and filing that as a
recipe would produce an entry with the ingredients of whichever one
happened to be first. So a candidate is only accepted when it looks
like the page's *subject* — it must carry both ingredients and steps.

Stdlib only: the host CLI runs this without a virtualenv.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from stack.web.content import SourceContent

_LD_BLOCK_RE = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _blocks(html: str) -> Iterator[Any]:
    """Every parseable ld+json payload in the document.

    Sites ship malformed JSON more often than you would hope (trailing
    commas, unescaped newlines in a description). One bad block must not
    cost us a good one, so parse failures are skipped silently.
    """
    for raw in _LD_BLOCK_RE.findall(html or ""):
        try:
            yield json.loads(raw)
        except (ValueError, TypeError):
            continue


def _objects(node: Any) -> Iterator[dict]:
    """Walk a payload yielding every object in it.

    JSON-LD nests three ways in the wild — a bare object, a top-level
    array, and a `@graph` list — and sites mix them. Walking everything
    is shorter than handling each shape and cannot miss one.
    """
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _objects(item)


def _has_type(obj: dict, wanted: str) -> bool:
    raw = obj.get("@type")
    types = raw if isinstance(raw, list) else [raw]
    return any(isinstance(t, str) and t.lower() == wanted.lower() for t in types)


def _text(value: Any) -> str:
    """Flatten a schema.org value into a string.

    Handles the shapes sites actually publish: a plain string, a bare
    number (`"recipeYield": 4` is common — the field is typed as text
    in the spec and half the web ignores that), an object carrying a
    `name`/`text`, or a list of any of those.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "name", "description"):
            got = value.get(key)
            if isinstance(got, str) and got.strip():
                return got.strip()
        return ""
    if isinstance(value, list):
        return "\n".join(filter(None, (_text(v) for v in value)))
    return ""


def _steps(value: Any) -> list[str]:
    """Instruction steps, flattened out of however they were nested.

    `recipeInstructions` is a list of strings, a list of `HowToStep`
    objects, a list of `HowToSection` objects each holding steps, or a
    single paragraph. Sections are flattened rather than preserved: the
    vault entry is prose, not a structured recipe format.
    """
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, dict):
        if _has_type(value, "HowToSection"):
            return _steps(value.get("itemListElement") or [])
        got = _text(value)
        return [got] if got else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_steps(item))
        return out
    return []


def _duration(iso: Any) -> str | None:
    """`PT1H15M` as `1 h 15 min`. Returns None for anything else.

    Deliberately narrow: ISO 8601 durations have a full grammar, and
    recipes only ever use hours and minutes. Anything unrecognised is
    dropped rather than guessed at.
    """
    if not isinstance(iso, str):
        return None
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso.strip(), re.IGNORECASE)
    if not match or not any(match.groups()):
        return None
    hours, minutes = match.groups()
    parts = []
    if hours:
        parts.append(f"{int(hours)} h")
    if minutes:
        parts.append(f"{int(minutes)} min")
    return " ".join(parts)


# ── Recipe ────────────────────────────────────────────────────────────

def _recipe_markdown(obj: dict) -> str | None:
    """Render a `Recipe` object as the Markdown that goes in the vault.

    None when the object is a mention rather than the page's subject —
    a listing page's thumbnails carry a name and an image but no
    ingredients, and filing one of those would produce an entry named
    after a recipe it does not contain.
    """
    ingredients = [_text(i) for i in (obj.get("recipeIngredient") or [])]
    ingredients = [i for i in ingredients if i]
    steps = [s for s in _steps(obj.get("recipeInstructions") or []) if s]
    if not ingredients or not steps:
        return None

    lines: list[str] = []
    description = _text(obj.get("description"))
    if description:
        lines += [description, ""]

    facts = []
    servings = _text(obj.get("recipeYield"))
    if servings:
        facts.append(f"**Servings:** {servings}")
    total = _duration(obj.get("totalTime")) or _duration(obj.get("cookTime"))
    if total:
        facts.append(f"**Total time:** {total}")
    if facts:
        lines += [" · ".join(facts), ""]

    lines += ["## Ingredients", ""]
    lines += [f"- {i}" for i in ingredients]
    lines += ["", "## Instructions", ""]
    lines += [f"{n}. {s}" for n, s in enumerate(steps, 1)]

    return "\n".join(lines).strip()


# ── Article ───────────────────────────────────────────────────────────

def _article_markdown(obj: dict) -> str | None:
    """A `NewsArticle`/`Article` body, when the site publishes one.

    Most do not — `articleBody` is optional and usually omitted, which
    is why this returns None far more often than the recipe path and
    why general extraction still exists. When it is present it is the
    cleanest possible body: exactly what the publisher considers the
    article, with no navigation to strip.
    """
    body = _text(obj.get("articleBody"))
    if not body:
        return None
    lines = []
    description = _text(obj.get("description"))
    if description and description not in body:
        lines += [description, ""]
    lines.append(body)
    return "\n".join(lines).strip()


# ── Entry point ───────────────────────────────────────────────────────

_READERS = (
    ("Recipe", _recipe_markdown),
    ("Article", _article_markdown),
    ("NewsArticle", _article_markdown),
    ("BlogPosting", _article_markdown),
)


def read_structured(html: str, *, url: str | None = None) -> SourceContent | None:
    """The page's own structured data as a `SourceContent`, if usable.

    Returns None whenever the markup is absent, unparseable, or present
    but not about this page — the caller falls through to general
    extraction, which is the common case.
    """
    for payload in _blocks(html):
        for obj in _objects(payload):
            for wanted, render in _READERS:
                if not _has_type(obj, wanted):
                    continue
                body = render(obj)
                if not body:
                    continue
                return SourceContent(
                    text=body,
                    mime="text/markdown",
                    title_hint=_text(obj.get("headline") or obj.get("name")) or None,
                    source_uri=url,
                )
    return None
