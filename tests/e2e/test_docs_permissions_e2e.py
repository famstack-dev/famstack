"""The archive vocabulary is shared with every family member.

Paperless checks permissions per object as well as per model. The
``all_documents`` group grants a member the right to view tags,
correspondents and document types, but Paperless still hides an entry
the member does not own and was not granted. An entry with no owner is
the one kind every member with the model right can see.

The archivist and the seed create the vocabulary with the admin's token,
and Paperless makes the token's user the owner of what it creates. So a
member saw every tag on a document as "Private" (issue #176). These
tests sign in as a plain member and ask Paperless what that account
sees, which is the only answer that counts.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import pytest

from tests.e2e.paperless import PaperlessAPI, login

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from permissions import ensure_group, share_vocabulary  # noqa: E402
from pipeline import PaperlessAPI as BotPaperlessAPI  # noqa: E402


@dataclass
class Member:
    id: int
    api: PaperlessAPI


@pytest.fixture
def member(paperless, paperless_scope):
    """A family member as single sign-on creates one: not a superuser,
    in ``all_documents``, with a client signed in as that account."""
    ensure_group(paperless.url, paperless.token)
    name, password = f"{paperless_scope.uid}-marge", "marge-test-pw"
    user = paperless.create_user(name, password)
    paperless_scope.on_cleanup.append(lambda _: paperless.delete_user(user["id"]))
    # ensure_group puts every account that is not a superuser in the group
    ensure_group(paperless.url, paperless.token)
    return Member(user["id"], login(paperless.url, name, password))


def _names(entries: list[dict]) -> set[str]:
    return {e["name"] for e in entries}


class TestNewVocabulary:
    """What the archivist creates while filing is visible to members."""

    async def test_a_member_sees_what_the_archivist_creates_by_name(
        self, paperless, member, paperless_scope, bdd,
    ):
        bdd.given("the archivist creates a tag, a document type and a correspondent")
        tag, doc_type, corr = (paperless_scope.tag(b) for b in ("Tag", "Type", "Sender"))
        async with aiohttp.ClientSession() as session:
            bot = BotPaperlessAPI(session, paperless.url, paperless.token)
            assert await bot.create_tag(tag)
            assert await bot.create_doc_type(doc_type)
            assert await bot.create_correspondent(corr)

        bdd.then("a member of all_documents lists all three by name")
        assert tag in _names(member.api.list_tags())
        assert doc_type in _names(member.api.list_document_types())
        assert corr in _names(member.api.list_correspondents())


class TestExistingVocabulary:
    """Entries the admin owns from before are shared on the next start."""

    def test_admin_owned_entries_become_visible_and_a_members_own_tag_stays_theirs(
        self, paperless, member, paperless_scope, bdd,
    ):
        bdd.given("a tag, a document type and a correspondent the admin owns")
        tag = paperless.create_tag(paperless_scope.tag("OldTag"))["name"]
        doc_type = paperless.create_document_type(paperless_scope.tag("OldType"))["name"]
        corr = paperless.create_correspondent(paperless_scope.tag("OldSender"))["name"]
        assert tag not in _names(member.api.list_tags()), "precondition: hidden from the member"

        bdd.and_("a tag the member created, and so owns")
        own = member.api.create_tag(paperless_scope.tag("MargesTag"))

        bdd.when("the docs stacklet starts")
        share_vocabulary(paperless.url, paperless.token)

        bdd.then("the member sees the admin's entries by name")
        assert tag in _names(member.api.list_tags())
        assert doc_type in _names(member.api.list_document_types())
        assert corr in _names(member.api.list_correspondents())

        bdd.and_("the member's own tag keeps its owner")
        mine = next(t for t in paperless.list_tags() if t["id"] == own["id"])
        assert mine["owner"] == member.id

    def test_a_second_start_changes_nothing(self, paperless, member, paperless_scope, bdd):
        bdd.given("a start that has already shared the vocabulary")
        paperless.create_tag(paperless_scope.tag("Tag"))
        share_vocabulary(paperless.url, paperless.token)
        before = {t["id"]: t["owner"] for t in paperless.list_tags()}

        bdd.when("the docs stacklet starts again")
        said: list[str] = []
        share_vocabulary(paperless.url, paperless.token, step=said.append)

        bdd.then("it reports nothing and no owner moves")
        assert said == []
        assert {t["id"]: t["owner"] for t in paperless.list_tags()} == before
