"""archivist_services: the services the archivist hands its work to, wired once.

    services = build_services(bot)     # in start(), inside the event loop
    await services.pipeline.process(...)
    await services.capture.capture_url(...)

The archivist does no filing itself. Paperless, the classifier, the
memory's writer, the document and capture pipelines and the search
service do the work; `ArchivistServices` holds them, and every
collaborator of the bot reads them from there. A test that drives one
handler hands its own `ArchivistServices` to the bot's constructor and
never starts the bot.

`build_services` reads the bot's settings (rendered from `stack.toml`
and `bot.toml`) and builds the services in dependency order:

    http session ──> Paperless ──┐
    classifier (None without AI) ┼──> DocumentPipeline, SearchService,
    memory writer + vault ───────┘    CapturePipeline

It runs inside the event loop, before the sync loop starts, because the
HTTP session has to be created there. One side effect on the bot: with
classification on and no AI address, it turns classification and the
reformat off and says so in the log, because the pipeline must not call
a model that is not there.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from stack.ai.client import ModelCapabilities

from .capture_pipeline import CapturePipeline
from .capture_tags import CaptureTagCache
from .document_pipeline import DocumentPipeline
from .extractors import TextExtractor, UrlExtractor
from .git_mirror import GitMirror
from .pipeline import Classifier, PaperlessAPI
from .search_service import SearchService
from .vault_context import VaultContext

__all__ = ["ArchivistServices", "build_services"]


@dataclass(frozen=True)
class ArchivistServices:
    """Everything the archivist delegates work to.

    Built once by `ArchivistBot.start()`, or handed to the constructor by
    a caller that never starts the bot (a test driving one handler).
    `classifier` is None without an AI endpoint and `mirror` is None
    without Forgejo; every other service is always present.

        ArchivistBot(..., services=ArchivistServices(paperless=..., ...))
    """

    paperless: PaperlessAPI
    classifier: Classifier | None
    url_extractor: UrlExtractor
    text_extractor: TextExtractor
    capture_tags: CaptureTagCache
    mirror: GitMirror | None
    vault: VaultContext
    pipeline: DocumentPipeline
    search: SearchService
    capture: CapturePipeline


def build_services(bot) -> ArchivistServices:
    """Wire every service from the bot's config.

    Runs inside the event loop, before super().start() blocks in the
    sync loop, because the HTTP session has to be created there. Turns
    the bot's classification off when it has no AI address.
    """
    # Reuse the framework-owned aiohttp session: one pool, closed by
    # MicroBot on shutdown. Paperless / OpenAI / extractors share it.
    paperless = PaperlessAPI(bot._ensure_http(), bot.paperless_url, bot.paperless_token)
    classifier = _build_classifier(bot)
    url_extractor = UrlExtractor()
    text_extractor = TextExtractor()
    capture_tags = _load_capture_tags(bot)
    mirror = _build_mirror(bot)
    vault = VaultContext(language=bot.language, shared_bucket=bot.shared_bucket)
    mirror.vault = vault
    return ArchivistServices(
        paperless=paperless,
        classifier=classifier,
        url_extractor=url_extractor,
        text_extractor=text_extractor,
        capture_tags=capture_tags,
        mirror=mirror,
        vault=vault,
        pipeline=_document_pipeline(bot, paperless, classifier, mirror, vault),
        search=_search_service(bot, paperless, classifier, vault),
        capture=_capture_pipeline(bot, url_extractor, text_extractor, classifier,
                                  mirror, capture_tags, paperless, vault),
    )


# ── The parts ───────────────────────────────────────────────────────────────


def _build_classifier(bot) -> Classifier | None:
    """The classifier at the bot's AI address, or None without one.

    The vision-capability cache lives in the bot's data dir so a probe
    done in one container restart isn't repeated by the next one.
    """
    # When OPENAI_URL is empty, leave the classifier unconfigured: the
    # bot still does filing / search / URL archiving, and the framework
    # refuses to construct against a missing endpoint so we don't
    # silently leak documents to api.openai.com.
    if bot.openai_url:
        return Classifier.from_endpoint(
            bot.openai_url, bot.openai_key, bot_name=bot.name,
            language=bot.language,
            capabilities=ModelCapabilities(
                path=bot._session_dir / "model-capabilities.json",
            ),
        )
    if bot.classify_enabled:
        logger.warning(
            "[archivist] classify=true but OPENAI_URL is empty, "
            "so classification is off. Set up AI with './stack up ai'"
        )
        bot.classify_enabled = False
        bot.reformat_enabled = False
    return None


def _load_capture_tags(bot) -> CaptureTagCache:
    """The capture tag vocabulary, loaded from the bot's data dir."""
    capture_tags = CaptureTagCache(bot._session_dir / "capture-tags.json")
    capture_tags.load()
    logger.info(
        "[archivist] capture tag cache: {} tags (keep_body={})",
        len(capture_tags.top(10_000)),
        bot.capture_keep_body,
    )
    return capture_tags


def _build_mirror(bot) -> GitMirror:
    """The archivist's writer of the family memory, through its curator,
    which answers each filing (`stack.memory`)."""
    # `bot._session_dir` is already the in-container path the bot
    # runner mounts (`/data/<stacklet>/bot`). Don't read DATA_DIR:
    # that env var carries the host path and would dump mirror state
    # outside the container's volume mount.
    mirror = GitMirror(data_dir=bot._session_dir, shared_bucket=bot.shared_bucket)
    logger.info("[archivist] Memory vault writer: through the curator")
    return mirror


def _document_pipeline(bot, paperless, classifier, mirror, vault) -> DocumentPipeline:
    """The pipeline a document takes: upload, OCR, classify, file."""
    return DocumentPipeline(
        paperless=paperless,
        classifier=classifier,
        mirror=mirror,
        bot_name=bot.name,
        language=bot.language,
        classify_enabled=bot.classify_enabled,
        reformat_enabled=bot.reformat_enabled,
        classify_max_chars=bot.classify_max_chars,
        vision_max_pdf_pages=bot.vision_max_pdf_pages,
        reformat_max_pdf_pages=bot.reformat_max_pdf_pages,
        paperless_public_url=bot.paperless_public_url,
        link_base_url=bot.link_base_url,
        actor=bot.user_id,
        vault=vault,
    )


def _search_service(bot, paperless, classifier, vault) -> SearchService:
    """The search over Paperless and the memory, answering in the bot's language."""
    return SearchService(
        classifier=classifier,
        paperless=paperless,
        t=bot.t,
        language=bot.language,
        code_public_url=bot.code_public_url,
        mirror_org=bot.mirror_org,
        link_base_url=bot.link_base_url,
        shared_bucket=bot.shared_bucket,
        vault=vault,
    )


def _capture_pipeline(
    bot, url_extractor, text_extractor, classifier, mirror, capture_tags, paperless, vault,
) -> CapturePipeline:
    """The pipeline a capture takes: extract, classify, write the card."""
    return CapturePipeline(
        url_extractor=url_extractor,
        text_extractor=text_extractor,
        classifier=classifier,
        mirror=mirror,
        capture_tags=capture_tags,
        paperless=paperless,
        bot_name=bot.name,
        classify_max_chars=bot.classify_max_chars,
        capture_keep_body=bot.capture_keep_body,
        capture_tag_prompt_size=bot.capture_tag_prompt_size,
        vision_max_pdf_pages=bot.vision_max_pdf_pages,
        llm=classifier.llm if classifier is not None else None,
        vault=vault,
    )
