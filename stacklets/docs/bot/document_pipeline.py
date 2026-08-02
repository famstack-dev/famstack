"""DocumentPipeline — upload → OCR → classify → reformat → mirror → outcome.

The core filing flow, lifted out of ArchivistBot so it is a coherent,
Matrix-free unit: it talks to Paperless, the classifier, and the Forgejo
mirror, and hands back a `FilingOutcome` the orchestrator renders into a
chat reply. No `self.t`, no `self._send` — the bot owns i18n and
transport; this owns the pipeline.

The split with the orchestrator is deliberate: the pipeline computes a
single coarse `status` plus all the enrichment data, and the bot keeps
the reply-priority chain (llm-error > no-text > classify-disabled >
no-classification > filed). That chain depends on chat-only inputs
(openai_url, the translator), so it stays in the orchestrator while the
work lives here.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from loguru import logger

from matching import build_document_event
from pdf_analysis import (
    has_ocr_text_layer,
    has_text_layer,
    pdf_page_count,
    should_attach_vision,
    should_reformat_pdf,
)
from pdf_render import render_pages
from pipeline import (
    EnrichResult,
    ImageAttachment,
    PaperlessDuplicateError,
    enrich_document,
    reformat_document,
)
from stack import resolve_model
from stack.links import go_docs, public

# Text-like extensions skip reformat (the content is already clean) but
# still classify + mirror. Paperless only parses text/plain and text/csv,
# so everything else here is renamed to .txt at upload time.
TEXT_LIKE = ("md", "txt", "csv", "json", "yaml", "yml", "toml")

# Image extensions take the multimodal classify path when the model has
# vision; the binary rides alongside the OCR text as supplementary
# context. Scanned PDFs go a separate route (page render).
IMAGE_MIMES = {
    "png": "image/png",
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def utc_now_isoformat() -> str:
    """Current UTC time as an ISO-8601 string with second precision.

    The `ts` on emitted `dev.famstack.event` envelopes — the wall-clock
    moment the event entered the ledger. Distinct from a document's own
    date used for date-resolution.
    """
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass
class FilingOutcome:
    """What the pipeline produces; the orchestrator turns it into a reply.

    `status` is coarse:
      upload_failed / duplicate / ocr_failed / filed_no_details — terminal,
        the doc never reached enrichment.
      enriched — the doc is filed; the orchestrator applies its reply
        priority over the remaining fields (llm_error, has_text,
        classify_enabled, classification, …).
    """

    status: str
    display_name: str
    doc_id: int | None = None
    link: str = ""
    duplicate: PaperlessDuplicateError | None = None
    has_text: bool = False
    classify_enabled: bool = False
    classification: dict = field(default_factory=dict)
    resolved_topics: list[str] = field(default_factory=list)
    resolved_persons: list[str] = field(default_factory=list)
    resolved_type: str | None = None
    resolved_correspondent: str | None = None
    created_new: list[str] = field(default_factory=list)
    date_applied: str | None = None
    reformat_failed: bool = False
    llm_error: tuple[str, str] | None = None
    envelope: dict | None = None


@dataclass
class ReprocessOutcome:
    """The result of re-enriching a filed doc with a user correction.

    status: doc_missing (target gone) | llm_error | reclassified.
    """

    status: str
    doc_id: int
    llm_error: tuple[str, str] | None = None
    title: str = ""
    resolved_topics: list[str] = field(default_factory=list)
    resolved_persons: list[str] = field(default_factory=list)
    resolved_type: str | None = None
    resolved_correspondent: str | None = None
    envelope: dict | None = None


class DocumentPipeline:
    """Files a document into Paperless, enriches it, mirrors it to Forgejo.

    Dependencies are injected so the pipeline never reaches back into the
    bot. `load_ontology` / `correspondents_section` are callables because
    the archivist reads them fresh from the memory vault per call.
    """

    def __init__(
        self, *,
        paperless,
        classifier,
        mirror,
        bot_name: str,
        language: str,
        classify_enabled: bool,
        reformat_enabled: bool,
        classify_max_chars: int,
        vision_max_pdf_pages: int,
        reformat_max_pdf_pages: int,
        paperless_public_url: str,
        link_base_url: str,
        actor: str,
        vault,
    ):
        self._paperless = paperless
        self._classifier = classifier
        self._mirror = mirror
        self._name = bot_name
        self.language = language
        self.classify_enabled = classify_enabled
        self.reformat_enabled = reformat_enabled
        self.classify_max_chars = classify_max_chars
        self.vision_max_pdf_pages = vision_max_pdf_pages
        self.reformat_max_pdf_pages = reformat_max_pdf_pages
        self.paperless_public_url = paperless_public_url
        self.link_base_url = link_base_url
        self.actor = actor
        self._vault = vault

    async def process(
        self, *,
        filename: str,
        display_name: str,
        file_data: bytes,
        date_filed: str | None = None,
        submitter_mxid: str | None = None,
        user_hint: str | None = None,
    ) -> FilingOutcome:
        """Run the full pipeline and return a FilingOutcome.

        Each step can fail independently; classification and reformat are
        *enrichment*, not gates — a filed doc is always mirrored before we
        return, so a flaky LLM reduces the richness of the entry rather
        than dropping it.
        """
        logger.info("[archivist] Processing: {} ({} bytes)", display_name, len(file_data))

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        is_text = ext in TEXT_LIKE
        is_image = ext in IMAGE_MIMES
        # A native-text PDF reads fine without OCR; reformat would only
        # risk degrading clean content. A machine-OCR'd text layer
        # (OCRmyPDF/Tesseract) still gets vision so the model can override
        # jumbled text.
        is_pdf_with_text = ext == "pdf" and has_text_layer(file_data)
        is_pdf_ocr_layer = is_pdf_with_text and has_ocr_text_layer(file_data)

        if is_text:
            if ext == "csv":
                upload_filename, upload_type = filename, "text/csv"
            elif ext == "txt":
                upload_filename, upload_type = filename, "text/plain"
            else:
                base = filename.rsplit(".", 1)[0] or "document"
                upload_filename, upload_type = f"{base}.txt", "text/plain"
        else:
            upload_filename, upload_type = filename, None

        task_id = await self._paperless.upload(
            upload_filename, file_data, content_type=upload_type,
        )
        if not task_id:
            return FilingOutcome(status="upload_failed", display_name=display_name)

        try:
            doc_id = await self._paperless.wait_task(task_id)
        except PaperlessDuplicateError as e:
            return FilingOutcome(
                status="duplicate", display_name=display_name, duplicate=e,
            )
        if not doc_id:
            return FilingOutcome(status="ocr_failed", display_name=display_name)

        link = public(go_docs(doc_id), self.link_base_url)
        doc = await self._paperless.get_doc(doc_id)
        if not doc:
            # Filed but unreadable — still mirror a minimal entry so
            # Paperless ⇄ mirror stay 1:1.
            await self.safe_mirror(
                doc_id=doc_id, classification={}, body_text="",
                processing="ocr", model=None,
                fallback_title=display_name, paperless_tags=[],
            )
            return FilingOutcome(
                status="filed_no_details", display_name=display_name,
                doc_id=doc_id, link=link,
            )

        ocr_text = doc.get("content", "") or ""
        has_text = len(ocr_text.strip()) >= 10

        if self.classify_enabled and has_text:
            result = await self._enrich(
                doc, ext, is_image, is_pdf_with_text, is_pdf_ocr_layer,
                file_data, date_filed, submitter_mxid, user_hint,
            )
        else:
            result = EnrichResult()

        classification = result.classification
        reformat_failed = False
        formatted: str | None = None
        should_reformat = bool(classification) and self.reformat_enabled and not is_text
        if should_reformat and ext == "pdf":
            pages = pdf_page_count(file_data)
            if not should_reformat_pdf(pages, self.reformat_max_pdf_pages):
                logger.info(
                    "[archivist] reformat skipped for doc #{}: {} pages > {}",
                    doc_id, pages, self.reformat_max_pdf_pages,
                )
                should_reformat = False
        if should_reformat:
            formatted = await reformat_document(
                paperless=self._paperless, classifier=self._classifier,
                doc_id=doc_id, ocr_text=ocr_text,
            )
            if not formatted:
                reformat_failed = True

        body_text, processing, model = self._mirror_body(
            is_text, file_data, formatted, ocr_text, reformatted=bool(formatted),
        )
        enriched = dict(classification) if classification else {}
        enriched["topics"] = result.resolved_topics
        enriched["persons"] = result.resolved_persons
        enriched["correspondent"] = result.resolved_correspondent
        enriched["document_type"] = result.resolved_type
        paperless_tags = [
            *result.resolved_topics,
            *(f"Person: {p}" for p in result.resolved_persons),
        ]
        await self.safe_mirror(
            doc_id=doc_id, classification=enriched, body_text=body_text,
            processing=processing, model=model, fallback_title=display_name,
            paperless_tags=paperless_tags, summary=result.summary,
        )

        envelope = None
        if classification:
            envelope = build_document_event(
                doc_id, classification,
                resolved_topics=result.resolved_topics,
                resolved_persons=result.resolved_persons,
                resolved_correspondent=result.resolved_correspondent,
                resolved_type=result.resolved_type,
                link_base_url=self.link_base_url,
                actor=self.actor,
                ts=utc_now_isoformat(),
            )

        return FilingOutcome(
            status="enriched", display_name=display_name, doc_id=doc_id, link=link,
            has_text=has_text, classify_enabled=self.classify_enabled,
            classification=classification,
            resolved_topics=result.resolved_topics,
            resolved_persons=result.resolved_persons,
            resolved_type=result.resolved_type,
            resolved_correspondent=result.resolved_correspondent,
            created_new=result.created_new,
            date_applied=result.updates_applied.get("created"),
            reformat_failed=reformat_failed,
            llm_error=result.llm_error,
            envelope=envelope,
        )

    async def _enrich(
        self, doc, ext, is_image, is_pdf_with_text, is_pdf_ocr_layer,
        file_data, date_filed, submitter_mxid, user_hint=None,
    ) -> EnrichResult:
        """Classify the doc, attaching images when vision helps.

        Image upload → one attachment; scanned/short OCR'd PDF → one per
        rendered page; native-text PDF or text → text-only. The classifier
        silently drops images on text-only models, so attaching is safe.

        `user_hint` is the human caption that accompanied the upload --
        Element X attachment text, scan-session opener/closer text, or
        per-page captions concatenated. The classifier reads it as
        high-signal context next to the OCR.
        """
        images: list[ImageAttachment] | None = None
        if is_image:
            images = [ImageAttachment(data=file_data, mime=IMAGE_MIMES[ext])]
        elif ext == "pdf" and should_attach_vision(
            has_text_layer=is_pdf_with_text,
            has_ocr_text_layer=is_pdf_ocr_layer,
            page_count=pdf_page_count(file_data) if is_pdf_with_text else 0,
            vision_max_pages=self.vision_max_pdf_pages,
        ):
            if await self._classifier.has_vision():
                rendered = render_pages(file_data)
                if rendered:
                    images = [
                        ImageAttachment(data=p, mime="image/png") for p in rendered
                    ]
        ontology = self._vault.ontology()
        return await enrich_document(
            paperless=self._paperless,
            classifier=self._classifier,
            doc=doc,
            classify_max_chars=self.classify_max_chars,
            images=images,
            ontology_section=ontology.classifier_prompt_section(self.language),
            correspondents_section=self._vault.correspondents_section(),
            persons_section=self._vault.persons_section(),
            ontology=ontology,
            lang=self.language,
            date_filed=date_filed,
            submitter_mxid=submitter_mxid,
            user_hint=user_hint,
        )

    def _mirror_body(self, is_text, file_data, formatted, ocr_text, *, reformatted):
        """Pick the mirror body, its provenance, and the model that made it.

        Text files keep their original bytes (markdown stays markdown),
        provenance "original", no model. Everything else takes the
        reformatted markdown if reformat ran (provenance "ai_formatted",
        model = the reformat model) else the raw OCR ("ocr", no model).
        """
        if is_text:
            try:
                body_text = file_data.decode("utf-8")
            except UnicodeDecodeError:
                body_text = file_data.decode("utf-8", errors="replace")
            return body_text, "original", None
        body_text = formatted or ocr_text
        if reformatted:
            try:
                model = resolve_model(f"{self._name}/reformat")
            except ValueError:
                model = None
            return body_text, "ai_formatted", model
        return body_text, "ocr", None

    async def reprocess(
        self, *, doc_id: int, user_hint: str, date_filed: str | None = None,
        initial_classification: dict | None = None,
    ) -> ReprocessOutcome:
        """Re-enrich an already-filed doc with the user's reply as a hint.

        The hint becomes a high-priority correction in the classify prompt;
        `is_reprocess=True` preserves the document's filing date. The mirror
        publish is idempotent on the paperless_id, so it overwrites the
        prior entry. Returns a ReprocessOutcome the orchestrator renders.

        ``initial_classification`` is the LLM's original output for this
        document (from the chain walker's `*.filed` boundary). Threading
        it into the prompt anchors each reprocess pass on the same
        starting state -- corrections behave like deltas, and the same
        chain of hints produces the same output regardless of how many
        intermediate reclassifications have already happened.
        """
        doc = await self._paperless.get_doc(doc_id)
        if not doc:
            return ReprocessOutcome(status="doc_missing", doc_id=doc_id)

        ontology = self._vault.ontology()
        result = await enrich_document(
            paperless=self._paperless,
            classifier=self._classifier,
            doc=doc,
            classify_max_chars=self.classify_max_chars,
            ontology_section=ontology.classifier_prompt_section(self.language),
            correspondents_section=self._vault.correspondents_section(),
            persons_section=self._vault.persons_section(),
            ontology=ontology,
            lang=self.language,
            is_reprocess=True,
            date_filed=date_filed,
            user_hint=user_hint,
            initial_classification=initial_classification,
        )
        if result.llm_error:
            return ReprocessOutcome(
                status="llm_error", doc_id=doc_id, llm_error=result.llm_error,
            )

        # Refetch so the mirror sees the post-PATCH title/tags.
        refreshed = await self._paperless.get_doc(doc_id) or doc
        paperless_tags = [
            *result.resolved_topics,
            *(f"Person: {p}" for p in result.resolved_persons),
        ]
        await self.safe_mirror(
            doc_id=doc_id,
            classification=dict(result.classification, **{
                "topics": result.resolved_topics,
                "persons": result.resolved_persons,
                "correspondent": result.resolved_correspondent,
                "document_type": result.resolved_type,
            }),
            body_text=refreshed.get("content", "") or "",
            processing="ai_formatted" if result.classification else "ocr",
            model=None,
            fallback_title=refreshed.get("title") or f"Paperless #{doc_id}",
            paperless_tags=paperless_tags,
            summary=result.summary,
        )

        title = result.classification.get("title") or refreshed.get("title") or f"#{doc_id}"
        # A fresh envelope so the user can chain another correction by
        # replying to THIS message; type `document.reclassified` lets the
        # deriver tell the corrective pass from the original filing.
        envelope = build_document_event(
            doc_id, result.classification,
            resolved_topics=result.resolved_topics,
            resolved_persons=result.resolved_persons,
            resolved_correspondent=result.resolved_correspondent,
            resolved_type=result.resolved_type,
            link_base_url=self.link_base_url,
            actor=self.actor,
            ts=utc_now_isoformat(),
        )
        envelope["type"] = "document.reclassified"
        envelope["summary"] = f"{title} reclassified (#{doc_id})"
        envelope["data"]["user_hint"] = user_hint

        return ReprocessOutcome(
            status="reclassified", doc_id=doc_id, title=title,
            resolved_topics=result.resolved_topics,
            resolved_persons=result.resolved_persons,
            resolved_type=result.resolved_type,
            resolved_correspondent=result.resolved_correspondent,
            envelope=envelope,
        )

    async def safe_mirror(
        self, *, doc_id, classification, body_text, processing, model,
        fallback_title, paperless_tags, summary=None,
    ) -> None:
        """Mirror a filed doc to Forgejo — never raises, never blocks a reply.

        Shared with the reprocess flow so the archive stays 1:1 with
        Paperless regardless of which path filed/updated the doc.
        """
        if not self._mirror:
            return
        try:
            await self._mirror.publish(
                paperless_id=doc_id,
                classification=classification,
                body_text=body_text,
                processing=processing,
                model=model,
                paperless_url=self.paperless_public_url,
                tags=paperless_tags,
                fallback_title=fallback_title,
                summary=summary,
            )
        except Exception as e:
            logger.warning("[archivist] Git mirror failed for doc #{}: {}", doc_id, e)
