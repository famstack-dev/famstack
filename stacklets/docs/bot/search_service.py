"""SearchService — recall across Paperless + the memory vault.

Lifted out of ArchivistBot so the recall flow is a coherent, Matrix-free
unit: it resolves the query (LLM keyword extraction for questions),
searches both backends, optionally synthesises a natural-language answer
with a bounded deep-dive, and returns the final reply text.

The one piece of mid-flow chatter — the "looking deeper" status before
a deep-dive — is sent through an injected Notifier so the
service stays ignorant of Matrix. The bot owns `t` and passes it in,
so the service renders the household language without holding the
message catalogue.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable

from loguru import logger

from memory.lib import (
    refresh_vault_if_stale as _refresh_memory_vault,
    search_memory as _search_memory,
)
from nl_query import (
    build_evidence,
    expand_to_full_content,
    extract_citations,
    format_evidence_item,
    is_deferral,
    select_evidence_for_display,
)
from notifier import Notifier
from recall import resolve_search_query as _resolve_search_query
from search_format import (
    format_memory_hit as _format_memory_hit,
    format_paperless_hit as _format_paperless_hit,
)

Translator = Callable[..., str]


class SearchService:
    """Searches Paperless + the memory vault and renders the reply."""

    def __init__(
        self, *,
        classifier,
        paperless,
        t: Translator,
        language: str,
        code_public_url: str,
        mirror_org: str,
        paperless_public_url: str,
        shared_bucket: str,
        vault,
    ):
        self._classifier = classifier
        self._paperless = paperless
        self._t = t
        self.language = language
        self.code_public_url = code_public_url
        self.mirror_org = mirror_org
        self.paperless_public_url = paperless_public_url
        self.shared_bucket = shared_bucket
        self._vault = vault

    def scopes_for_sender(
        self, sender: str | None, *, topic_bucket: str | None = None,
    ) -> list[str]:
        """Vault-path prefixes the asker is allowed to see.

        Shared/family docs live under `<shared_bucket>/`; each person's
        private notes under their own slug (the Matrix localpart, which
        is the canonical entity slug). Default closed: an unknown sender
        gets the shared bucket only — personal notes never leak.

        ``topic_bucket`` overrides the sender-derived defaults: a `?`
        query asked in a topic room scopes to that topic's bucket only.
        The asker is who they always were (frontmatter and bot replies
        still respect identity), but the room context narrows the
        haystack. Wider search is reached by asking outside the topic
        room or via the CLI's `--global` flag.
        """
        if topic_bucket:
            return [f"{topic_bucket}/"]
        scopes = [f"{self.shared_bucket}/"]
        if sender:
            entity = sender.lstrip("@").split(":", 1)[0]
            if entity and entity != self.shared_bucket:
                scopes.append(f"{entity}/")
        return scopes

    async def run(
        self, *, query: str, sender: str | None, notifier: Notifier,
        topic_bucket: str | None = None,
    ) -> str:
        """Resolve, search, optionally synthesise, and return the reply.

        `notifier` is used only for the mid-flow deep-dive status;
        everything else is returned as a single block of text for the
        caller to send.
        """
        logger.info("[archivist] search: sender={} query={!r}", sender, query[:80])

        memory_regex, paperless_query, keywords = await _resolve_search_query(
            query,
            classifier=self._classifier,
            ontology_section=self._vault.ontology_section(),
            language=self.language,
        )
        if keywords:
            logger.info("[archivist] search rewritten: {!r} -> {}", query[:60], keywords)

        paperless_results = await self._paperless.search(paperless_query)
        logger.info("[archivist] paperless: {} hit(s)", len(paperless_results))

        memory_results = await self._search_memory_scoped(
            memory_regex, sender, topic_bucket=topic_bucket,
        )

        if not memory_results and not paperless_results:
            return self._t("search_no_results", query=query)

        # Each block is one paragraph in the rendered reply; they join
        # with a blank line so markdown keeps them distinct.
        blocks: list[str] = []
        if keywords:
            blocks.append(self._t(
                "search_rewritten", query=query, keywords=", ".join(keywords),
            ))

        synthesized, evidence = await self._synthesize(query, keywords, memory_results, paperless_results)
        if synthesized:
            return await self._render_synthesis(
                query, synthesized, evidence, memory_results, paperless_results,
                blocks, notifier,
            )

        # Literal mode (or synthesis unavailable/failed): the two-section
        # layout, one bare numbered hit per line.
        if memory_results:
            blocks.append(self._t("search_memory_results", query=query))
            for n, r in enumerate(memory_results, start=1):
                blocks.append(_format_memory_hit(
                    r, n, code_public_url=self.code_public_url, mirror_org=self.mirror_org,
                ))
        if paperless_results:
            blocks.append(self._t("search_paperless_results", query=query))
            for n, doc in enumerate(paperless_results, start=1):
                blocks.append(_format_paperless_hit(
                    doc, n, public_url=self.paperless_public_url,
                ))
        return "\n\n".join(blocks)

    async def _search_memory_scoped(
        self, memory_regex, sender, *, topic_bucket: str | None = None,
    ) -> list[dict]:
        """Search the memory vault, scoped to what `sender` may see.

        Runs in a thread (file I/O + regex) and best-effort refreshes the
        local checkout first. Empty when MEMORY_VAULT_DIR is unset.
        ``topic_bucket`` narrows scope to a single topic folder when
        the query came from a topic room.
        """
        memory_dir = os.environ.get("MEMORY_VAULT_DIR", "")
        if not memory_dir:
            logger.info("[archivist] memory: skipped (MEMORY_VAULT_DIR unset)")
            return []
        memory_path = Path(memory_dir)
        await asyncio.to_thread(_refresh_memory_vault, memory_path)
        scopes = self.scopes_for_sender(sender, topic_bucket=topic_bucket)
        results = await asyncio.to_thread(
            _search_memory, memory_regex, memory_path, None, None, scopes, 10,
        )
        logger.info("[archivist] memory: {} hit(s) scopes={}", len(results), scopes)
        return results

    async def _synthesize(self, query, keywords, memory_results, paperless_results):
        """First synthesis pass — only in question mode with a classifier.

        Returns (answer, evidence). Empty answer means literal mode.
        """
        if not (keywords and self._classifier):
            return "", []
        evidence = build_evidence(
            memory_results, paperless_results,
            code_public_url=self.code_public_url,
            mirror_org=self.mirror_org,
            paperless_public_url=self.paperless_public_url,
        )
        answer = await self._classifier.synthesize_answer(query, evidence, lang=self.language)
        logger.info(
            "[archivist] synthesis: {} evidence item(s), answer={} chars",
            len(evidence), len(answer),
        )
        return answer, evidence

    async def _render_synthesis(
        self, query, synthesized, evidence, memory_results, paperless_results,
        blocks, notifier,
    ) -> str:
        """Render the synthesised answer + cited evidence, with one bounded
        deep-dive when the first pass deferred ("I'd need to read [N]")."""
        citations = extract_citations(synthesized)
        selected = select_evidence_for_display(evidence, citations)
        logger.info(
            "[archivist] synthesis citations={} → showing {} of {} rows",
            citations, len(selected), len(evidence),
        )

        if is_deferral(synthesized) and selected:
            titles = ", ".join(
                (ev.get("title") or "the document").strip() for _, ev in selected
            )
            await notifier.status("search_looking_deeper", titles=titles)
            expanded = expand_to_full_content(selected, memory_results, paperless_results)
            deeper = await self._classifier.synthesize_answer(query, expanded, lang=self.language)
            logger.info("[archivist] deep-dive: {} doc(s), answer={} chars", len(expanded), len(deeper))
            if deeper and not is_deferral(deeper):
                deeper_cites = extract_citations(deeper)
                deeper_selected = select_evidence_for_display(
                    [ev for _n, ev in selected], deeper_cites,
                )
                blocks.append(self._t("search_answer", answer=deeper))
                blocks.append(self._t("search_evidence_header"))
                for idx, ev in deeper_selected:
                    blocks.append(format_evidence_item(ev, idx))
                return "\n\n".join(blocks)

        blocks.append(self._t("search_answer", answer=synthesized))
        blocks.append(self._t("search_evidence_header"))
        for idx, ev in selected:
            blocks.append(format_evidence_item(ev, idx))
        return "\n\n".join(blocks)
