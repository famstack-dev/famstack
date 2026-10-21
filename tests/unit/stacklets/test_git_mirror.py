"""Unit tests for the archivist's git mirror.

Covers the pure methods — filename generation, slug normalization,
frontmatter shape, commit trailer format, markdown assembly. Forgejo
HTTP interactions are exercised live in integration tests, not
stubbed here.
"""

from __future__ import annotations

import json
import re
import sys
import yaml
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))
# `memory.lib`, for the ontology a capture's tags are read through.
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))

from stack.people import Person  # noqa: E402
from memory.note_card import Source  # noqa: E402
from git_mirror import (  # noqa: E402
    BOT_EMAIL,
    BOT_USERNAME,
    GitMirror,
    _commit_author,
    _filer_localpart,
)


class TestCommitAuthor:
    """Captures are attributed to the family member who filed them, derived
    from their Matrix id; a missing/malformed id falls back to the bot."""

    def test_derives_name_and_email_from_mxid(self):
        assert _commit_author("@marge:home.local") == ("marge", "marge@home.local")

    def test_localpart_only_id_falls_back_to_local_domain(self):
        assert _commit_author("@homer") == ("homer", "homer@local")

    def test_no_submitter_is_the_bot(self):
        assert _commit_author(None) == (BOT_USERNAME, BOT_EMAIL)

    def test_filer_localpart(self):
        assert _filer_localpart("@Marge:home.local") == "marge"
        assert _filer_localpart(None) is None


@pytest.fixture
def mirror(tmp_path):
    """GitMirror wired enough to exercise its pure methods."""
    return GitMirror(
        code_url="http://stack-code:3000",
        admin_user="stackadmin",
        admin_password="secret",
        admin_usernames=["homer"],
        data_dir=tmp_path,
        paperless_version="2.14.5",
    )


# ── Slug normalization ─────────────────────────────────────────────────────

class TestSlug:
    def test_ascii(self, mirror):
        assert mirror._slug("Duff Insurance Rechnung Marz 2026") == "duff-insurance-rechnung-marz-2026"

    def test_umlauts_normalize(self, mirror):
        # Non-ASCII (ü, ä, ö) becomes their base letters after NFKD decompose
        assert mirror._slug("Müller Straße") == "muller-strasse" or mirror._slug("Müller Straße") == "muller-strae"

    def test_punctuation_collapses(self, mirror):
        assert mirror._slug("Kwik-E-Mart, Inc.") == "kwik-e-mart-inc"

    def test_empty_fallback(self, mirror):
        assert mirror._slug("") == "document"

    def test_length_cap(self, mirror):
        long_title = "a" * 200
        assert len(mirror._slug(long_title)) == 60


# ── Filepath construction ──────────────────────────────────────────────────

class TestFilepath:
    """Documents land under `<shared_bucket>/documents/` — the shared
    institutional bucket inside the vault. Undated docs go to
    `<shared_bucket>/documents/_unfiled/`. The default mirror fixture
    uses `shared_bucket="family"`."""

    def test_with_title_and_date(self, mirror):
        path = mirror._filepath(
            date="2026-03-15", paperless_id=247,
            title="Duff Insurance Rechnung", has_title=True,
        )
        assert path == "family/documents/2026/03/2026-03-15-duff-insurance-rechnung-p247.md"

    def test_with_title_without_date_goes_to_unfiled(self, mirror):
        path = mirror._filepath(
            date=None, paperless_id=247,
            title="Duff Insurance Rechnung", has_title=True,
        )
        assert path == "family/documents/_unfiled/duff-insurance-rechnung-p247.md"

    def test_with_title_invalid_date_falls_through(self, mirror):
        path = mirror._filepath(
            date="not-a-date", paperless_id=42,
            title="A", has_title=True,
        )
        assert path == "family/documents/_unfiled/a-p42.md"

    def test_no_title_with_date(self, mirror):
        path = mirror._filepath(
            date="2026-03-15", paperless_id=42, title=None, has_title=False,
        )
        assert path == "family/documents/2026/03/2026-03-15-p42.md"

    def test_no_title_without_date(self, mirror):
        path = mirror._filepath(
            date=None, paperless_id=42, title=None, has_title=False,
        )
        assert path == "family/documents/_unfiled/p42.md"


# ── Frontmatter ────────────────────────────────────────────────────────────

class TestFrontmatter:
    def test_ai_full(self, mirror):
        fm = mirror._frontmatter(
            title="Duff Insurance Rechnung März 2026",
            date="2026-03-15",
            correspondent="Duff Insurance",
            document_type="Invoice",
            category="Insurance",
            persons=["Homer"],
            tags=["Insurance", "Person: Homer"],
            paperless_id=247,
            paperless_url="http://docs.home.local",
            processing="ai_formatted",
            model="qwen2.5:14b",
        )
        assert fm["type"] == "document"
        assert fm["resource"] == "http://docs.home.local/documents/247/details"
        assert fm["title"] == "Duff Insurance Rechnung März 2026"
        assert fm["paperless_id"] == 247
        assert fm["processing"] == "ai_formatted"
        assert fm["model"] == "qwen2.5:14b"
        assert fm["paperless_version"] == "2.14.5"
        assert fm["source"] == "paperless"
        assert fm["timestamp"].endswith("Z")
        # key order: type first (OKF convention), timestamp last; reflects insertion
        assert list(fm.keys())[0] == "type"
        assert list(fm.keys())[-1] == "timestamp"

    def test_builder_output_is_schema_valid(self, mirror):
        """The builder is the enforcement point: its output must satisfy the
        vault-format schema so a live filing never logs a violation. This is
        why the write path only warns — the bug is caught here, deterministically."""
        from stack.frontmatter import validate as fm_validate
        fm = mirror._frontmatter(
            title="Duff Insurance Rechnung", date="2026-03-15",
            correspondent="Duff Insurance", document_type="Invoice",
            category="Insurance", persons=["Homer"], tags=["Insurance"],
            paperless_id=247, paperless_url="http://docs.home.local",
            processing="ai_formatted", model="qwen2.5:14b",
        )
        assert fm_validate(fm) == []

    def test_ocr_omits_model(self, mirror):
        fm = mirror._frontmatter(
            title="Untitled", date=None,
            correspondent=None, document_type=None, category=None,
            persons=[], tags=[], paperless_id=99,
            paperless_url="", processing="ocr", model=None,
        )
        assert fm["processing"] == "ocr"
        assert "model" not in fm
        assert "correspondent" not in fm
        assert "persons" not in fm

    def test_original_processing_is_text_file_provenance(self, mirror):
        """`original` is the provenance for text-like files (.md, .json…)
        whose body is the source bytes, not any LLM/OCR output."""
        fm = mirror._frontmatter(
            title="recipes", date=None,
            correspondent=None, document_type=None, category=None,
            persons=[], tags=[], paperless_id=5,
            paperless_url="", processing="original", model=None,
        )
        assert fm["processing"] == "original"
        assert "model" not in fm

    def test_no_paperless_version_when_unset(self, tmp_path):
        m = GitMirror(
            code_url="", admin_user="", admin_password="",
            admin_usernames=[], data_dir=tmp_path,
        )
        fm = m._frontmatter(
            title="t", date=None,
            correspondent=None, document_type=None, category=None,
            persons=[], tags=[], paperless_id=1,
            paperless_url="", processing="ai_formatted", model="x",
        )
        assert "paperless_version" not in fm


# ── Commit message ─────────────────────────────────────────────────────────

class TestCommitMessage:
    def test_learn_with_model(self, mirror):
        msg = mirror._commit_message(
            verb="learn", title="Duff Insurance Rechnung",
            paperless_id=247, processing="ai_formatted", model="qwen2.5:14b",
        )
        lines = msg.split("\n")
        assert lines[0] == "learn: Duff Insurance Rechnung"
        assert lines[1] == ""
        assert "Paperless-Id: 247" in lines
        assert "Processing: ai_formatted" in lines
        assert "Model: qwen2.5:14b" in lines

    def test_update_without_model(self, mirror):
        msg = mirror._commit_message(
            verb="update", title="x", paperless_id=1,
            processing="ocr", model=None,
        )
        assert msg.startswith("update: x\n\n")
        assert "Paperless-Id: 1" in msg
        assert "Processing: ocr" in msg
        assert "Model:" not in msg

    def test_summary_rides_between_subject_and_trailers(self, mirror):
        # The body sits in its own paragraph so `git log` renders it as a
        # readable summary and trailers stay parseable as trailers.
        summary = "## Summary\nDuff Insurance car insurance EUR 340/year.\n\n## Parties\nDuff Insurance → Homer"
        msg = mirror._commit_message(
            verb="learn", title="Duff Insurance", paperless_id=42,
            processing="ai_formatted", model="qwen2.5:14b",
            summary=summary,
        )
        lines = msg.split("\n")
        assert lines[0] == "learn: Duff Insurance"
        assert lines[1] == ""
        # Summary block follows, ends with a blank line, then trailers.
        body_start = 2
        assert lines[body_start] == "## Summary"
        trailer_idx = lines.index("Paperless-Id: 42")
        assert lines[trailer_idx - 1] == ""
        assert "## Parties" in lines[body_start:trailer_idx]
        # Trailers still present and parseable.
        assert "Processing: ai_formatted" in lines
        assert "Model: qwen2.5:14b" in lines

    def test_no_summary_keeps_old_layout(self, mirror):
        msg = mirror._commit_message(
            verb="learn", title="t", paperless_id=1,
            processing="ocr", model=None, summary=None,
        )
        # Subject, blank, trailers — no extra blank lines from a missing body.
        assert msg == "learn: t\n\nPaperless-Id: 1\nProcessing: ocr"


# ── Render (full markdown) ─────────────────────────────────────────────────

class TestRender:
    def test_full_document(self, mirror):
        fm = {
            "title": "Duff Insurance Rechnung", "paperless_id": 247,
            "processing": "ai_formatted", "source": "paperless",
        }
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter=fm,
            body="Policy number: KFZ-2024-XXX\n\nAmount: EUR 340.",
            correspondent="Duff Insurance",
            persons=["Homer"], people=[Person("homer", "Homer Simpson")],
        )
        # Frontmatter fenced with ---
        assert out.startswith("---\n")
        # Parseable YAML block
        fm_block = out.split("---", 2)[1]
        parsed = yaml.safe_load(fm_block)
        assert parsed["paperless_id"] == 247

        assert "# Duff Insurance Rechnung" in out
        assert "**From:** [Duff Insurance](" in out
        assert "correspondents/duff-insurance.md)" in out
        assert "**About:** [Homer](../../../people/homer.md)" in out
        assert "Policy number: KFZ-2024-XXX" in out

    def test_no_wiki_header_when_no_entities(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
        )
        assert "[[" not in out
        assert "# t" in out
        assert "body" in out


class TestBriefingBlock:
    """The classifier's briefing — prose summary, optional source link,
    facts, and action items — is wrapped in a `> [!summary]` callout so
    it reads visually distinct from the OCR-cleaned body that follows."""

    def test_full_briefing_in_rendered_output(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "Duff Insurance Rechnung"},
            body="(OCR body)",
            correspondent="Duff Insurance", persons=["Homer"],
            summary="Annual renewal of comprehensive auto insurance.",
            facts=["Total: EUR 340.00", "Policy: KH-2026-987"],
            action_items=[
                {"action": "Confirm SEPA balance", "due": "2026-03-14"},
                {"action": "File for tax", "due": None},
            ],
        )

        assert "> [!summary]" in out
        assert "> Annual renewal of comprehensive auto insurance." in out
        assert "> **Facts**" in out
        assert "> - Total: EUR 340.00" in out
        assert "> - Policy: KH-2026-987" in out
        assert "> **Action items**" in out
        assert "> - Confirm SEPA balance 📅 2026-03-14" in out
        assert "> - File for tax" in out

    def test_briefing_appears_before_body(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="OCR PAYLOAD",
            correspondent=None, persons=[],
            summary="One liner.",
            facts=[], action_items=[],
        )
        assert out.index("> [!summary]") < out.index("OCR PAYLOAD")

    def test_omitted_entirely_when_classifier_returned_nothing(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary=None, facts=None, action_items=None,
        )
        assert "> [!summary]" not in out
        assert "**Facts**" not in out
        assert "**Action items**" not in out

    def test_individual_sections_drop_when_empty(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary="Just a summary.", facts=[], action_items=[],
        )
        assert "> [!summary]" in out
        assert "> Just a summary." in out
        assert "**Facts**" not in out
        assert "**Action items**" not in out

    def test_string_action_item_renders_as_task(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary=None, facts=None,
            action_items=["Just a string item"],
        )
        assert "> - Just a string item" in out

    def test_null_string_due_is_treated_as_no_due(self, mirror):
        # LLMs sometimes return the string "null" instead of true null.
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary=None, facts=None,
            action_items=[{"action": "Pay bill", "due": "null"}],
        )
        # No trailing "— null"
        assert "> - Pay bill" in out
        assert "null" not in out.split("**Action items**")[1]

    def test_skips_blank_facts_and_empty_actions(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary=None,
            facts=["", "  ", "Real fact"],
            action_items=[{"action": "", "due": "2026-01-01"}, {"action": "Real action"}],
        )
        # Only the real fact + real action survive.
        assert "> - Real fact" in out
        assert "> - Real action" in out
        # No empty bullets / checkboxes.
        assert "- \n" not in out
        assert "- [ ] \n" not in out

    def test_source_link_appears_inside_callout(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="(body)",
            correspondent=None, persons=[],
            summary="Prose.",
            facts=[], action_items=[],
            source_link=("Show Document", "https://paperless.local/documents/42/details"),
        )
        # Link sits inside the callout (prefixed with `> `) and points
        # at the Paperless doc details page.
        assert "> [Show Document](https://paperless.local/documents/42/details)" in out

    def test_source_link_dropped_when_url_missing(self, mirror):
        # Half-supplied source_link tuple (label without url, or vice versa)
        # must not produce a malformed `[label]()` link.
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="(body)",
            correspondent=None, persons=[],
            summary="Prose.",
            facts=[], action_items=[],
            source_link=("Show Document", ""),
        )
        assert "Show Document" not in out


class _FakeTodoClient:
    """Minimal Forgejo file API for todo-fold unit tests."""

    def __init__(self, files: dict[str, dict] | None = None):
        self.files = dict(files or {})
        self.puts: list[dict] = []

    def get_file(self, owner, repo, path):
        return self.files.get(path)

    def put_file(
        self, owner, repo, path, *,
        content, message, sha=None, author_name=None, author_email=None,
    ):
        self.files[path] = {"content": content, "sha": f"sha-{len(self.puts) + 1}"}
        self.puts.append({
            "path": path,
            "content": content,
            "message": message,
            "sha": sha,
            "author_name": author_name,
            "author_email": author_email,
        })

    def change_files(self, owner, repo, changes, *, message, author_name=None, author_email=None):
        """Several files in one commit, all or nothing, as Forgejo does."""
        for change in changes:
            held = self.files.get(change.path)
            if (change.operation == "create") == (held is not None) or (
                    held and change.sha != held["sha"]):
                raise AssertionError(f"Forgejo would refuse {change.operation} {change.path}")
        self.commits = [*getattr(self, "commits", []), [c.path for c in changes]]
        for change in changes:
            if change.operation == "delete":
                self.files.pop(change.path, None)
            else:
                self.put_file(owner, repo, change.path, content=change.content, message=message,
                              sha=change.sha or None, author_name=author_name,
                              author_email=author_email)
        return {"commit": {"sha": f"c{len(self.commits)}"}}


class TestActionItemsOnTheTopicsList:
    """A record's action items land on its topic's list, linked back to the record."""

    @pytest.mark.asyncio
    async def test_a_document_starts_the_topics_list(self, mirror):
        client = _FakeTodoClient()
        await mirror._fold_document_todos(
            client,
            classification={
                "topics": ["Camping"],
                "action_items": [
                    {"action": "book pitch", "due": "2026-04-01"},
                    "pack stove",
                ],
            },
            record_path="family/documents/2026/03/2026-03-02-campsite-p12.md",
            record_title="Campsite booking",
            today="2026-03-02",
        )

        written = client.files["family/lists/camping.md"]["content"]
        assert written.startswith("---\ntype: list\ncard_id: list-camping\ntitle: Camping\n"
                                  "description: What is still to do for Camping\n"
                                  "topics:\n  - camping\n")
        assert ("- [ ] book pitch [Campsite booking]"
                "(../documents/2026/03/2026-03-02-campsite-p12.md) 📅 2026-04-01") in written
        assert "- [ ] pack stove [Campsite booking]" in written
        assert client.puts[0]["author_name"] == BOT_USERNAME

    @pytest.mark.asyncio
    async def test_a_ticked_item_is_never_reopened_by_a_refiling(self, mirror):
        client = _FakeTodoClient({
            "family/lists/camping.md": {
                "content": "---\ntype: list\ntitle: Camping\n---\n\n- [x] book pitch ✅ 2026-03-03\n",
                "sha": "old-sha",
            },
        })
        await mirror._fold_capture_todos(
            client,
            target_path="family/camping/notes/2026/07/packing-a1b2c3.md",
            classification={"title": "Packing", "action_items": ["pack stove", "Book pitch"]},
            author_name="homer",
            author_email="homer@simpson",
        )

        written = client.files["family/lists/camping.md"]["content"]
        assert "- [x] book pitch ✅ 2026-03-03" in written
        assert "- [ ] pack stove [Packing](../camping/notes/2026/07/packing-a1b2c3.md)" in written
        assert written.lower().count("book pitch") == 1
        assert client.puts[0]["sha"] == "old-sha"
        assert client.puts[0]["author_name"] == "homer"

    @pytest.mark.asyncio
    async def test_a_refiled_record_adds_nothing_and_its_items_follow_a_rename(self, mirror):
        """The model words a refile's items differently; the list keeps the family's."""
        old = "family/documents/2026/10/2026-10-09-permission-slip-p72.md"
        new = "family/documents/2026/10/2026-10-09-school-notice-p72.md"
        client = _FakeTodoClient()
        await mirror._fold_document_todos(
            client, classification={"topics": ["Education"],
                                    "action_items": [{"action": "Sign the slip", "due": "2026-10-23"}]},
            record_path=old, record_title="Permission slip", today="2026-10-09")
        await mirror._fold_document_todos(
            client, classification={"topics": ["Education"],
                                    "action_items": [{"action": "Sign and return the slip",
                                                      "due": "2026-10-23"}]},
            record_path=new, record_title="School notice", previous_path=old, today="2026-10-09")

        written = client.files["family/lists/education.md"]["content"]
        assert "Sign and return" not in written
        assert ("- [ ] Sign the slip [Permission slip]"
                "(../documents/2026/10/2026-10-09-school-notice-p72.md) 📅 2026-10-23") in written

    @pytest.mark.asyncio
    async def test_an_item_already_past_its_due_date_stays_off_the_list(self, mirror):
        """An old letter filed late, or last year's bill refiled, has nothing left to do.
        The record's summary still names the item; only the list leaves it out."""
        client = _FakeTodoClient()
        await mirror._fold_document_todos(
            client,
            classification={"topics": ["Fahrzeug"], "action_items": [
                {"action": "Rechnung überweisen", "due": "2026-08-14"},
                {"action": "Unterlagen abholen", "due": "2026-10-13"},
                "Reifen einlagern",
            ]},
            record_path="family/documents/2026/08/werkstatt-p40.md",
            record_title="Werkstatt Rechnung",
            today="2026-10-13",
        )

        written = client.files["family/lists/fahrzeug.md"]["content"]
        assert "Rechnung überweisen" not in written
        assert "Unterlagen abholen" in written, "due today is still ahead of the family"
        assert "Reifen einlagern" in written, "an item without a date never runs out"

    @pytest.mark.asyncio
    async def test_a_record_with_only_past_items_starts_no_list(self, mirror):
        client = _FakeTodoClient()
        await mirror._fold_document_todos(
            client,
            classification={"topics": ["Fahrzeug"],
                            "action_items": [{"action": "Rechnung überweisen", "due": "2026-08-14"}]},
            record_path="family/documents/2026/08/werkstatt-p40.md",
            record_title="Werkstatt Rechnung",
            today="2026-10-13",
        )

        assert client.puts == []

    @pytest.mark.asyncio
    async def test_a_new_list_is_titled_with_the_topics_name(self, mirror):
        client = _FakeTodoClient()
        await mirror._fold_document_todos(
            client, classification={"topics": ["Haus & Garten"], "action_items": ["Hecke schneiden"]},
            record_path="family/documents/2026/10/hecke-p9.md", record_title="Hecke")
        written = client.files["family/lists/haus-garten.md"]["content"]
        assert "title: Haus & Garten\n" in written

    @pytest.mark.asyncio
    async def test_a_personal_capture_adds_to_no_list(self, mirror):
        client = _FakeTodoClient()
        await mirror._fold_capture_todos(
            client,
            target_path="homer/gravel/notes/2026/07/training-a1b2c3.md",
            classification={"action_items": ["pump tires"]},
            author_name="homer",
            author_email="homer@simpson",
        )

        assert client.puts == []


class TestDatesOnTheFamilysCalendar:
    """A letter's dates go on the calendar: in date order, for the child, linked back."""

    LETTER = "family/documents/2026/10/2026-10-07-elternbrief-p80.md"

    @pytest.fixture
    def family(self, tmp_path, monkeypatch, mirror):
        """The Simpsons' family memory on disk, as the archivist's copy holds it."""
        sys.path.insert(0, str(_REPO_ROOT / "stacklets"))  # memory.lib, for VaultContext
        from vault_context import VaultContext

        vault = tmp_path / "vault"
        (vault / "family" / "people").mkdir(parents=True)
        (vault / "family" / "lists").mkdir()
        (vault / "family" / "people" / "lisa.md").write_text(
            "---\ntype: person\ntitle: Lisa Simpson\n---\n")
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(vault))
        monkeypatch.setenv("LANGUAGE", "de")
        mirror.vault = VaultContext(language="de", shared_bucket="family")
        return vault

    async def _file(self, mirror, client, dates, *, persons=("Lisa",), path=LETTER):
        await mirror._fold_document_dates(
            client, classification={"persons": list(persons), "dates": dates},
            record_path=path, record_title="Elternbrief Grundschule", today="2026-10-13")

    @pytest.mark.asyncio
    async def test_a_school_letter_starts_the_calendar_with_its_dates(self, mirror, family):
        client = _FakeTodoClient()
        await self._file(mirror, client, [
            {"what": "Elternabend", "date": "2026-11-17"},
            {"what": "Theater", "date": "2026-11-03"},
            {"what": "Interkulturelles Familienfest", "date": "2026-10-10"},
        ])

        written = client.files["family/lists/termine.md"]["content"]
        assert "title: Termine\n" in written and "kind: calendar\n" in written
        assert written.index("Theater") < written.index("Elternabend"), "next date on top"
        assert ("- [ ] Theater [Lisa](../people/lisa.md) [Elternbrief Grundschule]"
                "(../documents/2026/10/2026-10-07-elternbrief-p80.md) 📅 2026-11-03") in written
        assert "Familienfest" not in written, "a date already past stays off"

    @pytest.mark.asyncio
    async def test_dates_join_the_calendar_the_family_already_has(self, mirror, family):
        own = "---\ntype: list\ntitle: Kalender\nkind: calendar\n---\n\n- [ ] Zahnarzt 📅 2026-11-05\n"
        (family / "family" / "lists" / "kalender.md").write_text(own)
        client = _FakeTodoClient({"family/lists/kalender.md": {"content": own, "sha": "k1"}})

        await self._file(mirror, client, [{"what": "Theater", "date": "2026-11-03"}])

        assert "family/lists/termine.md" not in client.files
        written = client.files["family/lists/kalender.md"]["content"]
        assert written.index("Theater") < written.index("Zahnarzt")
        assert client.puts[0]["sha"] == "k1"

    @pytest.mark.asyncio
    async def test_someone_the_family_has_not_declared_is_left_off_the_entry(self, mirror, family):
        client = _FakeTodoClient()
        await self._file(mirror, client, [{"what": "Theater", "date": "2026-11-03"}],
                         persons=("Lisa", "Frau Krabappel"))

        written = client.files["family/lists/termine.md"]["content"]
        assert "[Lisa](../people/lisa.md)" in written and "Krabappel" not in written

    @pytest.mark.asyncio
    async def test_the_same_event_next_year_is_a_new_entry(self, mirror, family):
        """Unlike a to-do, an appointment is told apart by its date as much as its name."""
        client = _FakeTodoClient()
        await self._file(mirror, client, [{"what": "Elternabend", "date": "2026-11-17"}])
        await self._file(mirror, client, [{"what": "Elternabend", "date": "2027-03-09"}],
                         path="family/documents/2027/02/2027-02-20-elternbrief-p91.md")

        assert client.files["family/lists/termine.md"]["content"].count("Elternabend") == 2

    @pytest.mark.asyncio
    async def test_a_letter_without_dates_touches_no_list(self, mirror, family):
        client = _FakeTodoClient()
        await self._file(mirror, client, [])
        await self._file(mirror, client, [{"what": "Theater", "date": "im November"}])

        assert client.puts == []


# ── Captures ─────────────────────────────────────────────────────────────
#
# A capture is a non-Paperless source (today: a pasted URL processed by
# trafilatura, or pasted text the user typed). It routes under the
# sender's own entity bucket — `<entity>/notes/...` for kind=note,
# `<entity>/bookmarks/...` for kind=bookmark — so per-entity wiki
# compilation is a single recursive glob. Cross-mentions stay with the
# author; the frontmatter `persons:` field indexes them for other
# entities' wiki compiles. The hash suffix disambiguates re-pastes of
# different sources resolving to the same slug, and makes a re-paste
# of the same source idempotent (same path → update, not duplicate).


class TestCapturePath:
    """`<entity>/<kind>s/YYYY/MM/<slug>-<hash>.md` shape — routed by
    the sender's slug and the capture kind.

    `hash_key` is the stable identity string the caller hashes into the
    filename suffix — typically the source URL for URL/paste captures,
    or a content hash when the paste has no source URL. The mirror
    doesn't care what's in the string, only that the same key yields
    the same path on re-publish."""

    def test_path_uses_captured_date_and_entity(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17",
            title="Why local LLMs matter",
            hash_key="https://example.com/llms",
        )
        assert path.startswith("homer/notes/2026/05/")
        assert path.endswith(".md")

    def test_bookmark_kind_uses_bookmarks_folder(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="bookmark",
            captured_at="2026-05-17",
            title="Why local LLMs matter",
            hash_key="https://example.com/llms",
        )
        assert path.startswith("homer/bookmarks/2026/05/")

    def test_slug_in_filename(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17",
            title="Why local LLMs matter",
            hash_key="https://example.com/llms",
        )
        # slug lives between "YYYY/MM/" and the "-<hash>.md" tail.
        assert "why-local-llms-matter" in path

    def test_same_key_yields_same_path(self, mirror):
        a = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="t",
            hash_key="https://example.com/a",
        )
        b = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="t",
            hash_key="https://example.com/a",
        )
        assert a == b

    def test_different_keys_yield_different_paths(self, mirror):
        a = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="Same Title",
            hash_key="https://example.com/article-1",
        )
        b = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="Same Title",
            hash_key="https://example.com/article-2",
        )
        # Same slug, same date — only the hash distinguishes.
        assert a != b

    def test_different_entities_yield_different_paths(self, mirror):
        a = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="t",
            hash_key="https://example.com/x",
        )
        b = mirror._capture_filepath(
            entity="marge", kind="note",
            captured_at="2026-05-17", title="t",
            hash_key="https://example.com/x",
        )
        # Same content, different senders — routed to separate buckets.
        assert a.startswith("homer/notes/")
        assert b.startswith("marge/notes/")

    def test_no_title_falls_back_to_capture(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title=None,
            hash_key="https://example.com/x",
        )
        # No useful title → generic slug. Hash still disambiguates.
        assert "homer/notes/2026/05/" in path
        assert path.endswith(".md")

    def test_invalid_date_lands_in_unfiled(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="not-a-date", title="hi",
            hash_key="https://example.com/x",
        )
        # Undated captures still live under the sender's bucket.
        assert path.startswith("homer/notes/_unfiled/")


class TestCaptureFrontmatter:
    """Captures carry `kind: bookmark|note` + optional `resource`.
    Bookmark = URL pointer; Note = pasted body the user typed.

    Document-shaped fields (correspondent, document_type, category) are
    intentionally absent — captures aren't part of the Paperless
    ontology. Persons + tags are present (load-bearing for interest
    derivation in the dream-cycle rebuild)."""

    def test_bookmark_minimum_shape(self, mirror):
        fm = mirror._capture_frontmatter(
            title="Why local LLMs matter",
            captured_at="2026-05-17",
            kind="bookmark",
            source_uri="https://example.com/llms",
            persons=[], tags=[], model=None,
        )
        assert fm["title"] == "Why local LLMs matter"
        assert fm["type"] == "bookmark"
        assert fm["resource"] == "https://example.com/llms"
        # Document-shaped fields are gone from captures.
        assert "correspondent" not in fm
        assert "document_type" not in fm
        assert "category" not in fm
        assert "paperless_id" not in fm
        assert "paperless_url" not in fm
        assert fm["timestamp"].endswith("Z")

    def test_capture_builder_output_is_schema_valid(self, mirror):
        from stack.frontmatter import validate as fm_validate
        for kind in ("note", "bookmark"):
            fm = mirror._capture_frontmatter(
                title="X", captured_at="2026-05-17", kind=kind,
                source_uri="https://example.com", persons=[], tags=[], model=None,
            )
            assert fm_validate(fm) == [], (kind, fm_validate(fm))

    def test_a_notes_frontmatter_keeps_only_what_it_is_about(self, mirror):
        """Where a note came from and how it was made are in its sidecar
        (ADR-018 §3); the link in its words stays in its words."""
        fm = mirror._capture_frontmatter(
            title="Reddit thread title",
            captured_at="2026-05-17",
            kind="note",
            source_uri="https://reddit.com/r/x/comments/y",
            persons=["Homer"], tags=["LLMs"], model="m", capture_id="$x", filed_by="homer",
        )
        assert fm["type"] == "note"
        assert fm["tags"] == ["LLMs"]
        assert not {"resource", "capture_id", "model", "timestamp", "filed_by"} & set(fm)

    def test_note_without_source_uri(self, mirror):
        # Pure paste — no URL anywhere. Frontmatter omits source_uri so
        # Dataview queries can `where source_uri` filter to "captures
        # that link back to a source" cleanly.
        fm = mirror._capture_frontmatter(
            title="Some pasted thought",
            captured_at="2026-05-17",
            kind="note",
            source_uri=None,
            persons=["Homer"], tags=[], model=None,
        )
        assert fm["type"] == "note"
        assert "resource" not in fm

    def test_full_shape(self, mirror):
        fm = mirror._capture_frontmatter(
            title="Why local LLMs matter",
            captured_at="2026-05-17",
            kind="bookmark",
            source_uri="https://example.com/llms",
            persons=["Homer"],
            tags=["LLMs", "Local Inference", "Person: Homer"],
            model="qwen2.5:14b",
        )
        assert fm["persons"] == ["Homer"]
        assert fm["tags"] == ["LLMs", "Local Inference", "Person: Homer"]
        assert fm["model"] == "qwen2.5:14b"
        # date echoes the capture date, not the article's publish date.
        assert fm["date"] == "2026-05-17"

    def test_no_model_omits_field(self, mirror):
        fm = mirror._capture_frontmatter(
            title="t", captured_at="2026-05-17",
            kind="bookmark", source_uri="https://x/y",
            persons=[], tags=[], model=None,
        )
        assert "model" not in fm


class TestCaptureRender:
    """The captures use the same _render() as Paperless docs — same
    briefing block, same wiki-link header — but no document-shaped
    fields. Bookmarks drop the body section (the LLM summary IS the
    content); notes keep the body."""

    def test_bookmark_renders_without_body(self, mirror):
        # When the caller passes an empty body, the file ends at the
        # briefing block. That's the bookmark shape — the summary is
        # the content, the URL is the source.
        fm = mirror._capture_frontmatter(
            title="Why local LLMs matter",
            captured_at="2026-05-17",
            kind="bookmark",
            source_uri="https://example.com/llms",
            persons=["homer"], tags=["LLMs"], model="qwen2.5:14b",
        )
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter=fm, body="",
            correspondent=None, persons=["Homer"], people=[Person("homer", "Homer Simpson")],
            summary="A 200-word digest of the article's main points.",
            facts=["Mac Mini idles under 10W"],
            action_items=[],
        )
        assert "type: bookmark" in out
        # Resource URL is quoted because it contains ':'
        assert 'resource: "https://example.com/llms"' in out
        assert "# Why local LLMs matter" in out
        assert "**About:** [Homer](../../../people/homer.md)" in out
        assert "> [!summary]" in out
        assert "A 200-word digest" in out
        # No trailing empty body section — file ends after briefing.

    def test_note_renders_with_body(self, mirror):
        # Notes preserve the pasted body the user typed.
        fm = mirror._capture_frontmatter(
            title="Reddit thread on benchmarks",
            captured_at="2026-05-17",
            kind="note",
            source_uri="https://reddit.com/r/x/y",
            persons=["Homer"], tags=["Benchmarks"], model=None,
        )
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter=fm,
            body="Top comment quotes 60 tok/s on M2 Pro.\n\nRest of thread...",
            correspondent=None, persons=["Homer"],
            summary="Comment thread comparing on-device inference speeds.",
            facts=[], action_items=[],
        )
        assert "type: note" in out
        assert "> [!summary]" in out
        assert "Comment thread comparing" in out
        assert "Top comment quotes 60 tok/s" in out


class _FakeTreeClient:
    """Stands in for ForgejoClient.list_tree with a fixed file list."""

    def __init__(self, paths):
        self._paths = paths

    def list_tree(self, owner, repo):
        return [{"path": p, "type": "blob"} for p in self._paths]


class TestCaptureIdentityDedup:
    """Captures dedup on their stable hash suffix, not the title slug, so the
    same URL/body re-saved under a different title updates one file."""

    async def test_finds_same_hash_under_different_title(self, mirror):
        client = _FakeTreeClient([
            "homer/bookmarks/2026/06/old-framing-449437.md",
            "homer/notes/2026/06/unrelated-abcdef.md",
        ])
        found = await mirror._lookup_capture_path(
            client, "homer/bookmarks/2026/06/new-framing-449437.md")
        assert found == "homer/bookmarks/2026/06/old-framing-449437.md"

    async def test_prefers_exact_path(self, mirror):
        target = "homer/bookmarks/2026/06/same-449437.md"
        assert await mirror._lookup_capture_path(_FakeTreeClient([target]), target) == target

    async def test_none_when_hash_absent(self, mirror):
        client = _FakeTreeClient(["homer/bookmarks/2026/06/x-zzzzzz.md"])
        assert await mirror._lookup_capture_path(
            client, "homer/bookmarks/2026/06/y-449437.md") is None

    async def test_scoped_to_entity_and_kind(self, mirror):
        # Same hash under a different person or kind folder is a different capture.
        client = _FakeTreeClient([
            "marge/bookmarks/2026/06/x-449437.md",
            "homer/notes/2026/06/x-449437.md",
        ])
        assert await mirror._lookup_capture_path(
            client, "homer/bookmarks/2026/06/y-449437.md") is None


class _FakeVaultClient(_FakeTodoClient):
    """The Forgejo file API a capture write uses, over an in-memory tree."""

    def __init__(self, files=None):
        super().__init__(files)
        self.deleted: list[str] = []

    def list_tree(self, owner, repo):
        return [{"path": p, "type": "blob"} for p in self.files]

    def delete_file(self, owner, repo, path, *, sha, message):
        self.deleted.append(path)
        self.files.pop(path, None)


class TestACaptureKeepsItsPath:
    """A capture's path is its address: the topic page, a list item and
    the link in the chat all point at it. A correction that gives it a new
    title, or comes from someone else in the room, changes what the page
    says, never where it is."""

    PATH = "family/painting/bookmarks/2026/10/bart-s-drawing-454811.md"

    async def _refile(self, mirror, monkeypatch, *, entity, title):
        client = _FakeVaultClient({self.PATH: {"content": "old", "sha": "s1"}})
        monkeypatch.setattr("git_mirror.ForgejoClient", lambda **_: client)
        mirror._setup_done = True
        mirror._creds = SimpleNamespace(token="t")
        filed = await mirror.publish_capture(
            entity=entity, kind="bookmark", source_uri="mxc://home.test/x",
            title_hint=None, body_text="",
            classification={"title": title, "summary": "s"},
            captured_at="2026-10-13", model=None,
            existing_path=self.PATH, capture_id="$x",
        )
        return filed.path, client

    async def test_a_new_title_keeps_the_file(self, mirror, monkeypatch):
        path, client = await self._refile(
            mirror, monkeypatch, entity="family/painting",
            title="Bart's watercolour, wet on wet")
        assert path == self.PATH
        assert client.deleted == []
        assert "# Bart's watercolour, wet on wet" in client.files[self.PATH]["content"]

    async def test_a_reply_from_someone_else_keeps_it_in_its_topic(
        self, mirror, monkeypatch,
    ):
        path, client = await self._refile(
            mirror, monkeypatch, entity="marge", title="Bart's drawing")
        assert path == self.PATH
        assert [p for p in client.files if "/bookmarks/" in p] == [self.PATH]

    async def test_a_new_capture_is_named_after_its_title(self, mirror, monkeypatch):
        client = _FakeVaultClient()
        monkeypatch.setattr("git_mirror.ForgejoClient", lambda **_: client)
        mirror._setup_done = True
        mirror._creds = SimpleNamespace(token="t")
        path, _ = await mirror.publish_capture(
            entity="family/painting", kind="bookmark", source_uri="mxc://home.test/y",
            title_hint=None, body_text="", classification={"title": "Tent", "summary": "s"},
            captured_at="2026-10-13", model=None,
        )
        assert path.startswith("family/painting/bookmarks/2026/10/tent-")


class TestANoteFiledAsItsOwnWords:
    """A note filed as a clean version of its words has no briefing: the
    clean version, then the words. Its to-dos are on the topic's list,
    where they are kept and ticked, and not on the card a second time."""

    async def _file(self, mirror, monkeypatch, *, clean):
        client = _FakeVaultClient()
        monkeypatch.setattr("git_mirror.ForgejoClient", lambda **_: client)
        mirror._setup_done = True
        mirror._creds = SimpleNamespace(token="t")
        filed = await mirror.publish_capture(
            entity="family/camping", kind="note", source_uri=None, title_hint=None,
            body_text="call the campsite b4 fri",
            classification={"title": "Campsite", "summary": "", "facts": [],
                            "action_items": ["Call the campsite"]},
            captured_at="2026-10-13", model=None, capture_id="$x", clean=clean,
            sources=[Source("original", "marge", "2026-10-13T08:00:00+00:00", "chat", "text",
                            "$x", quote="call the campsite b4 fri")],
        )
        return client.files[filed.path]["content"], client

    async def test_the_card_has_the_clean_version_and_the_words(self, mirror, monkeypatch):
        note, _ = await self._file(mirror, monkeypatch, clean="Call the campsite before Friday.")
        clean_at = note.index("Call the campsite before Friday.")
        assert clean_at < note.index("## Sources\n\n> call the campsite b4 fri")
        assert "[!summary]" not in note

    async def test_its_to_dos_are_on_the_list_and_not_on_the_card(self, mirror, monkeypatch):
        note, client = await self._file(mirror, monkeypatch, clean="")
        assert "Action items" not in note
        assert "- [ ] Call the campsite" in client.files["family/lists/camping.md"]["content"]


class TestANoteIsItsCardAndItsSidecar:
    """A note is written as two files in one commit (ADR-018): the card
    with what it is about, and the sidecar with where its sources came
    from and how the card was made, the code commit and the prompt's
    template among it."""

    PATH = "family/camping/notes/2026/10/campsite-3a338e.md"

    async def _file(self, mirror, monkeypatch, client=None):
        client = client or _FakeVaultClient()
        monkeypatch.setattr("git_mirror.ForgejoClient", lambda **_: client)
        mirror._setup_done = True
        mirror._creds = SimpleNamespace(token="t")
        filed = await mirror.publish_capture(
            entity="family/camping", kind="note", source_uri=None, title_hint=None,
            body_text="call the campsite", classification={"title": "Campsite", "summary": ""},
            captured_at="2026-10-13", model="m", capture_id="$x", existing_path=self.PATH,
            submitter="@marge:simpson", clean="Call the campsite.",
            sources=[Source("original", "marge", "2026-10-13T08:00:00+00:00", "chat", "text",
                            "$x", quote="call the campsite")],
            made={"at": "2026-10-13T08:00:05+00:00", "model": "m", "code_commit": "9a5bdc9",
                  "prompt_template": "3f9a1c2b7d4e", "requested_by": "marge"},
        )
        return filed, client

    async def test_card_and_sidecar_land_in_one_commit(self, mirror, monkeypatch):
        filed, client = await self._file(mirror, monkeypatch)
        assert client.commits == [[self.PATH, self.PATH.removesuffix(".md") + ".json"]]
        sidecar = json.loads(client.files[self.PATH.removesuffix(".md") + ".json"]["content"])
        assert sidecar["card_id"] == filed.card_id
        assert sidecar["filed_by"] == "marge"
        assert sidecar["sources"] == [{"role": "original", "person": "marge",
                                       "time": "2026-10-13T08:00:00+00:00", "channel": "chat",
                                       "kind": "text", "id": "$x"}]
        assert sidecar["made"]["code_commit"] == "9a5bdc9"
        assert sidecar["made"]["prompt_template"] == "3f9a1c2b7d4e"

    async def test_the_card_has_no_machine_fields(self, mirror, monkeypatch):
        from stack.frontmatter import parse
        _, client = await self._file(mirror, monkeypatch)
        fields = parse(client.files[self.PATH]["content"])
        assert not {"capture_id", "model", "timestamp", "filed_by", "code_commit",
                    "prompt_template"} & set(fields)

    async def test_a_note_in_the_old_format_gets_its_sidecar_and_keeps_its_id(
            self, mirror, monkeypatch):
        old = ("---\ntype: note\ncard_id: note-0b6e2d91aa\ntitle: Campsite\n"
               "capture_id: \"$x\"\n---\n\n# Campsite\n")
        client = _FakeVaultClient({self.PATH: {"content": old, "sha": "s1"}})
        filed, client = await self._file(mirror, monkeypatch, client)
        assert filed.card_id == "note-0b6e2d91aa"
        assert client.commits == [[self.PATH, self.PATH.removesuffix(".md") + ".json"]]


class TestACardKeepsItsId:
    """Every card the archivist files carries `card_id`, made when it is
    first filed (ADR-016). Rebuilding the card from its source, a
    reprocess, a correction or a reply in an email thread, keeps it: links
    and the chat name the card by it."""

    DOC = "family/documents/2026/10/2026-10-13-duff-beer-invoice-p247.md"

    def _wire(self, mirror, monkeypatch, files=None) -> _FakeVaultClient:
        client = _FakeVaultClient(files)
        monkeypatch.setattr("git_mirror.ForgejoClient", lambda **_: client)
        mirror._setup_done = True
        mirror._creds = SimpleNamespace(token="t")
        return client

    @staticmethod
    def _card_id(client, path) -> str:
        from stack.frontmatter import parse
        return str(parse(client.files[path]["content"]).get("card_id") or "")

    async def _document(self, mirror, title):
        return await mirror.publish(
            paperless_id=247, classification={"title": title, "date": "2026-10-13"},
            body_text="Two cases of Duff.", processing="ai_formatted", model=None,
            paperless_url="http://docs.home.test",
        )

    async def test_a_new_document_gets_an_id_and_a_reprocess_keeps_it(self, mirror, monkeypatch):
        client = self._wire(mirror, monkeypatch)
        await self._document(mirror, "Duff Beer invoice")
        card_id = self._card_id(client, self.DOC)
        assert re.fullmatch(r"document-[0-9a-f]{10}", card_id)

        await self._document(mirror, "Duff Beer invoice, October")
        [moved] = list(client.files)
        assert moved != self.DOC and self._card_id(client, moved) == card_id

    async def test_a_capture_rebuilt_from_its_source_keeps_its_id(self, mirror, monkeypatch):
        client = self._wire(mirror, monkeypatch)
        capture = dict(entity="marge", kind="note", source_uri=None, title_hint=None,
                       body_text="Brushes and paper for the next trip.",
                       captured_at="2026-10-13", model=None, capture_id="$r-Plk")
        path, card_id = await mirror.publish_capture(classification={"title": "Art supplies"},
                                                     **capture)
        assert re.fullmatch(r"note-[0-9a-f]{10}", card_id)
        assert self._card_id(client, path) == card_id

        again = await mirror.publish_capture(classification={"title": "Art supplies for Lisa"},
                                             existing_path=path, **capture)
        assert again == (path, card_id) and self._card_id(client, path) == card_id

    async def test_a_note_refiled_from_a_bookmark_keeps_its_former_id(self, mirror, monkeypatch):
        """A correction rebuilds the card from its source; a link to the id it
        had as a bookmark must still find it afterwards."""
        path = "marge/notes/2026/10/bart-s-drawing-454811.md"
        client = self._wire(mirror, monkeypatch, {path: {"sha": "s1", "content": (
            "---\ntype: note\ncard_id: note-0b6e2d91aa\nformer_card_ids:\n"
            "  - bookmark-7f3a9c21e4\ntitle: Bart's drawing\n---\n")}})
        await mirror.publish_capture(
            entity="marge", kind="note", source_uri="mxc://home.test/x", title_hint=None,
            body_text="", classification={"title": "Bart's watercolour"},
            captured_at="2026-10-13", model=None, existing_path=path, capture_id="$x")
        from stack.frontmatter import parse
        fields = parse(client.files[path]["content"])
        assert (fields["card_id"], fields["former_card_ids"]) == (
            "note-0b6e2d91aa", ["bookmark-7f3a9c21e4"])

    async def test_an_email_thread_keeps_its_id_as_replies_arrive(self, mirror, monkeypatch):
        client = self._wire(mirror, monkeypatch)
        message = dict(entity="marge", thread_uri="mid:abc@springfield-elementary.test",
                       from_addr="skinner@springfield-elementary.test", title_hint=None,
                       captured_at="2026-10-13", model=None)
        path, card_id = await mirror.publish_email_message(
            message_id="abc@springfield-elementary.test", body_text="Field trip on Friday.",
            classification={"title": "Field trip"}, **message)
        assert re.fullmatch(r"email-[0-9a-f]{10}", card_id)
        assert self._card_id(client, path) == card_id

        again = await mirror.publish_email_message(
            message_id="def@springfield-elementary.test", body_text="Bring a lunch.",
            classification={"title": "Field trip lunch"}, **message)
        assert again == (path, card_id) and self._card_id(client, path) == card_id
        assert "Bring a lunch." in client.files[path]["content"]


class TestARecordLinksTheCardsItNames:
    """A record names its people by id and links each one's card; it links
    its correspondent's card and, filed into a topic, sits next to the
    topic's card. The archivist writes a correspondent's or a topic's card
    the first time something names it, and never again (ADR-017 §3, §4, §10)."""

    MOE = "family/correspondents/moe-s-tavern.md"
    TOPIC = "family/itchy-scratchy-land/topic.md"

    @pytest.fixture
    def client(self, tmp_path, monkeypatch, mirror) -> _FakeVaultClient:
        """The Simpsons' memory: declared people on disk, Forgejo in memory."""
        sys.path.insert(0, str(_REPO_ROOT / "stacklets"))  # memory.lib, for VaultContext
        from vault_context import VaultContext

        people = tmp_path / "vault" / "family" / "people"
        people.mkdir(parents=True)
        for pid, name in (("homer", "Homer Simpson"), ("bart", "Bart Simpson")):
            (people / f"{pid}.md").write_text(f"---\ntype: person\ntitle: {name}\n---\n")
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(tmp_path / "vault"))
        mirror.vault = VaultContext(language="en", shared_bucket="family")
        client = _FakeVaultClient()
        monkeypatch.setattr("git_mirror.ForgejoClient", lambda **_: client)
        mirror._setup_done = True
        mirror._creds = SimpleNamespace(token="t")
        return client

    async def _document(self, mirror, client, paperless_id, **classification):
        await mirror.publish(
            paperless_id=paperless_id, body_text="Two Duffs.", processing="ai_formatted",
            model=None, paperless_url="", tags=["Leisure", "Person: Homer"],
            classification={"title": f"Tab {paperless_id}", "date": "2026-10-13",
                            "correspondent": "Moe's Tavern", **classification},
        )
        return next(f["content"] for p, f in client.files.items() if p.endswith(f"-p{paperless_id}.md"))

    async def test_a_document_names_its_people_by_id_and_links_their_cards(self, mirror, client):
        from stack.frontmatter import parse

        text = await self._document(mirror, client, 31, persons=["Homer", "Bart"])
        fields = parse(text)
        assert fields["persons"] == ["homer", "bart"]
        assert fields["tags"] == ["leisure"]
        assert ("**About:** [Homer](../../../people/homer.md), "
                "[Bart](../../../people/bart.md)") in text

    async def test_a_name_nobody_goes_by_stays_a_plain_name(self, mirror, client):
        from stack.frontmatter import parse

        text = await self._document(mirror, client, 32, persons=["Homer", "Mr. Burns"])
        assert parse(text)["persons"] == ["homer"]
        assert "**About:** [Homer](../../../people/homer.md), Mr. Burns" in text
        assert not [p for p in client.files if "/people/" in p]

    async def test_a_new_correspondent_gets_its_card_and_a_known_one_keeps_it(self, mirror, client):
        from stack.frontmatter import parse

        text = await self._document(mirror, client, 33, correspondent_aliases=["Moe's"])
        assert "**From:** [Moe's Tavern](../../../correspondents/moe-s-tavern.md)" in text
        card = client.files[self.MOE]["content"]
        assert parse(card) == {"type": "correspondent", "card_id": "correspondent-moe-s-tavern",
                               "title": "Moe's Tavern", "aliases": ["Moe's"]}

        await self._document(mirror, client, 34, correspondent_aliases=["Moe Szyslak"])
        assert client.files[self.MOE]["content"] == card
        assert [p["path"] for p in client.puts].count(self.MOE) == 1

    async def test_a_note_filed_into_a_topic_gives_the_topic_its_card_once(self, mirror, client):
        from stack.frontmatter import parse

        for text in ("Book the hotel", "Pack sunscreen"):
            await mirror.publish_capture(
                entity="family/itchy-scratchy-land", kind="note", source_uri=None,
                title_hint=None, body_text=text, classification={"title": text, "summary": "s"},
                captured_at="2026-10-13", model=None,
            )
        assert parse(client.files[self.TOPIC]["content"]) == {
            "type": "topic", "card_id": "topic-itchy-scratchy-land", "title": "Itchy Scratchy Land"}
        assert [p["path"] for p in client.puts].count(self.TOPIC) == 1



class TestARecordTagsItsTopicsById:
    """A record's tags hold the ontology's topic ids and the classifier's
    keywords, lowercase with hyphens (ADR-017 §4). On a German instance a
    document's Paperless tags are German labels; the card holds the id, and
    a reader shows the label from the ontology. The vault here has no
    `ontology.toml`, so the shipped seed is the ontology, as on a new
    instance."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch, mirror) -> _FakeVaultClient:
        sys.path.insert(0, str(_REPO_ROOT / "stacklets"))  # memory.lib, for VaultContext
        from vault_context import VaultContext

        (tmp_path / "vault").mkdir()
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(tmp_path / "vault"))
        mirror.vault = VaultContext(language="de", shared_bucket="family")
        client = _FakeVaultClient()
        monkeypatch.setattr("git_mirror.ForgejoClient", lambda **_: client)
        mirror._setup_done = True
        mirror._creds = SimpleNamespace(token="t")
        return client

    async def test_a_document_tags_its_topics_by_id_and_its_keywords_in_lowercase(self, mirror, client):
        from stack.frontmatter import parse

        await mirror.publish(
            paperless_id=41, body_text="Impfung.", processing="ai_formatted", model=None,
            paperless_url="", tags=["Gesundheit", "Cropper Paper", "Medical", "Person: Bart", "Moe's"],
            classification={"title": "Impfpass Bart", "date": "2026-10-13"},
        )
        text = next(f["content"] for p, f in client.files.items() if p.endswith("-p41.md"))
        # `Medical` is the same topic in English: one tag. `Moe's` is no keyword
        # as it stands, so the writer slugs it.
        assert parse(text)["tags"] == ["medical", "cropper-paper", "moe-s"]

    async def test_a_note_keeps_its_keywords_as_keywords(self, mirror, client):
        from stack.frontmatter import parse

        await mirror.publish_capture(
            entity="family/itchy-scratchy-land", kind="note", source_uri=None, title_hint=None,
            body_text="Lisa paints the park.", classification={"title": "Watercolours", "summary": "s"},
            captured_at="2026-10-13", model=None,
            tags=["itchy-scratchy-land", "Watercolour", "Reise", "watercolour"],
        )
        text = next(f["content"] for p, f in client.files.items() if "/notes/" in p)
        assert parse(text)["tags"] == ["itchy-scratchy-land", "watercolour", "travel"]
