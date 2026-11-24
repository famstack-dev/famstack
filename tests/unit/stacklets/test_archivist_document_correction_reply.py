"""The answer to a correction of a filed document: a short card, folded.

A person replies in a document's thread ("this is Marge's"). The archivist
classifies the document again and answers with one line that names what
changed, the title being the link to the document's card, then the card as
it is now. The HTML folds that card away for clients that show HTML; the
plain body keeps it open.

The tests run the archivist's real reprocess handler against a stub
pipeline and read the content of the message it sends.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stack.cards.events import Correction  # noqa: E402
from stacklets.docs.bot.archivist import ArchivistBot  # noqa: E402
from stack.filing.correction.chain import CorrectionChain  # noqa: E402

BOT_ID = "@archivist-bot:server"
DOC_ID = 42
CARD_LINK = "https://home.example/go/card/document-1"


@pytest.fixture
def pipeline():
    """The document pipeline stub; each test gives it a `reprocess`."""
    return SimpleNamespace()


@pytest.fixture
def bot(tmp_path, pipeline):
    """An archivist with the real reprocess handler, recording each send."""
    bot = ArchivistBot(
        homeserver="http://homeserver", user_id=BOT_ID, password="x",
        session_dir=tmp_path, services=SimpleNamespace(pipeline=pipeline),
    )
    bot._client = SimpleNamespace()
    bot.sent: list[dict] = []

    async def _room_send(room_id, message_type, content, **kw):
        bot.sent.append(content)
        return SimpleNamespace(event_id="$answer")

    bot._client.room_send = _room_send
    return bot


# The document as the person saw it when they corrected it.
SEEN = {"title": "Auto Insurance Policy 2026", "persons": ["Homer"], "topics": ["insurance"]}


def _outcome():
    return SimpleNamespace(
        status="reclassified", doc_id=DOC_ID,
        title="Auto Insurance Policy 2026", resolved_topics=["insurance"],
        resolved_persons=["Marge"], resolved_type="contract",
        resolved_correspondent="Duff Insurance",
        classification={"summary": "Renewal."}, date="2025-03-15",
        link=CARD_LINK,
        envelope={"source": "docs", "type": "document.reclassified",
                  "data": {"paperless_id": DOC_ID, "persons": ["Marge"]}},
        unknown_persons=(),
    )


@pytest.mark.asyncio
async def test_a_corrected_document_answers_as_a_short_card_with_the_card_folded(
        bot, pipeline):
    """One line of changes with the title as the link, then the document's
    card as it is now; the HTML folds the card away."""
    asked = {}

    async def _reprocess(**kw):
        asked.update(kw)
        return _outcome()

    pipeline.reprocess = _reprocess
    await bot._corrections.reprocess_document(
        "!docs:server", DOC_ID, CorrectionChain(
            "this is Marge's", SEEN, SEEN, (Correction("marge", "", "this is Marge's"),)),
        "@marge:server", "$upload",
    )

    assert asked["changed_by"] == "@marge:server", "the correction is the replier's change"
    content = bot.sent[-1]
    assert content["body"].startswith(
        f"\U0001F501 Updated [Auto Insurance Policy 2026]({CARD_LINK}): Marge added")
    assert f"\U0001F4C4 [Auto Insurance Policy 2026]({CARD_LINK})" in content["body"]
    assert "\U0001F3E2 Duff Insurance" in content["body"]
    assert "<details>" in content["formatted_body"]
