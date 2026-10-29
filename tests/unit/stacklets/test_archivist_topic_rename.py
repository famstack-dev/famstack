"""A topic room's captures after `stack memory topic rename`, and during it.

The Simpsons' sewing room is "Thema: Nähprojekte", bound by the archivist
to `family/nahprojekte`. The admin renames the topic to `naehprojekte`.
Every capture sent to the room afterwards, and one the archivist is
classifying while the rename is committed, must land in the new folder:
a file under the old one would make the old topic again, with a second
topic card, and split it for good.

The rename runs as `./stack memory topic rename` runs it, through
`Stack.run_cli_command` on memory's test instance (`running_memory`). The
archivist reads the memory from that instance's clone, as the bot runner
mounts it (`MEMORY_VAULT_DIR`), resolves the room's binding
(`_topic_binding`, its Matrix client a stand-in that holds the binding
event), and files through its real capture pipeline and mirror into the
curator. The classifier is a stand-in: what it says is not under test.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from stack.frontmatter import parse
from stacklets.docs.bot.archivist import ArchivistBot
from stacklets.docs.bot.capture_pipeline import CapturePipeline
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.memory_client import MemoryClient
from stacklets.docs.bot.vault_context import VaultContext
from tests.memory.unit.instance import git, running_memory

BOT_ID = "@archivist-bot:simpson"
MEMBERS = [BOT_ID, "@homer:simpson", "@marge:simpson"]
OLD, NEW = "family/nahprojekte", "family/naehprojekte"
BOUND = {"kind": "topic", "bucket": OLD, "slug": "nahprojekte", "display_name": "Nähprojekte",
         "default_topics": ["nahprojekte"], "scope": "shared", "extract_knowledge": True}

FILES = {
    **{f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
       for pid, name in (("homer", "Homer Simpson"), ("marge", "Marge Simpson"))},
    f"{OLD}/topic.md": "---\ntype: topic\ncard_id: topic-nahprojekte\ntitle: Nähprojekte\n---\n",
    f"{OLD}/notes/2026/10/schnittmuster.md": (
        "---\ntype: note\ncard_id: note-1a1a1a1a1a\ntitle: Schnittmuster\ndate: \"2026-10-12\"\n"
        "tags:\n  - nahprojekte\n---\n\n# Schnittmuster\n"),
}


class Classifier:
    """The classifier's answer for a note; `meanwhile` runs while it thinks."""

    def __init__(self, meanwhile=None):
        self.meanwhile = meanwhile

    async def classify_capture(self, **_):
        if self.meanwhile:
            self.meanwhile()
        return {"title": "Knopfleiste", "tags": [], "persons": ["Marge"], "summary": "s"}


class Words:
    async def extract(self, text):
        return SimpleNamespace(text=text, source_uri=None, title_hint=None)


class Tags:
    def top(self, n):
        return []

    def record(self, tags, when):
        pass

    def save(self):
        pass


class Room:
    """The room's Matrix side: its binding event, and the bindings posted to it."""

    def __init__(self, bound: dict | None):
        self.bound, self.posted, self.rooms = bound, [], {}

    async def room_messages(self, room_id, **_):
        return SimpleNamespace(chunk=[SimpleNamespace(source={"content": self.bound})]
                               if self.bound else [])

    async def room_send(self, room_id, message_type, content, **_):
        self.posted.append(content)
        self.bound = content


@pytest.fixture
def memory(tmp_path, monkeypatch):
    with running_memory(tmp_path / "data", FILES, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "de")
        yield memory


@pytest.fixture
def archivist(memory, tmp_path):
    """The archivist as the bot runner builds it, over the instance's clone."""
    vault = VaultContext(language="de", shared_bucket="family")
    mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                       memory=MemoryClient.from_environ(memory.environ))
    bot = ArchivistBot(homeserver="http://h", user_id=BOT_ID, password="x",
                       session_dir=tmp_path / "session", services=SimpleNamespace(vault=vault))
    bot._client = Room(BOUND)
    return SimpleNamespace(bot=bot, mirror=mirror, vault=vault)


def _pipeline(archivist, classifier: Classifier) -> CapturePipeline:
    return CapturePipeline(
        url_extractor=None, text_extractor=Words(), classifier=classifier,
        mirror=archivist.mirror, capture_tags=Tags(), paperless=None,
        bot_name="archivist-bot", classify_max_chars=10000, capture_keep_body=False,
        capture_tag_prompt_size=50, vault=archivist.vault)


def _rename(memory) -> None:
    answer = memory.person("admin").runs(
        "memory", "topic", ["rename", "nahprojekte", "naehprojekte", "Nähprojekte"])
    assert answer.get("committed") is True, answer


def _forgejo(memory, pattern: str) -> list[str]:
    listed = git(memory.forgejo.repo, "ls-tree", "-r", "--name-only", "main").splitlines()
    return [p for p in listed if re.search(pattern, p)]


async def _sent_to_the_room(archivist, words: str, *, classifier: Classifier | None = None,
                            binding=None):
    """`words` sent by Marge to the sewing room, filed as the archivist files a note."""
    room = SimpleNamespace(room_id="!naehen:simpson", name="Thema: Nähprojekte",
                           display_name="Thema: Nähprojekte",
                           users={uid: object() for uid in MEMBERS})
    binding = binding or await archivist.bot._topic_binding(room, "@marge:simpson")
    return await _pipeline(archivist, classifier or Classifier()).capture_text(
        text=words, sender_mxid="@marge:simpson", bucket=binding.bucket,
        seed_topics=binding.seed_topics)


async def test_a_capture_after_the_rename_lands_in_the_new_folder(memory, archivist):
    """Given the room bound to the old id, when the admin renames the topic
    and Marge then sends a note, then it is filed in the new folder with
    the new id, the archivist posts the new binding, and the old folder
    is neither in Forgejo nor left empty in the curator's clone."""
    _rename(memory)

    outcome = await _sent_to_the_room(archivist, "Knopfleiste annähen")

    assert outcome.vault_path.startswith(f"{NEW}/notes/")
    assert "naehprojekte" in parse(memory.text(outcome.vault_path))["tags"]
    assert _forgejo(memory, r"/topic\.md$") == [f"{NEW}/topic.md"]
    assert _forgejo(memory, rf"^{OLD}/") == []
    assert not (memory.vault / OLD).exists()
    assert archivist.bot._client.posted[-1]["bucket"] == NEW


async def test_a_capture_classified_while_the_rename_commits_lands_in_the_new_folder(
        memory, archivist):
    """Given the binding resolved to the old folder, when the rename is
    committed while the note is classified, then the curator refuses the
    old folder and the note is filed in the new one, with the new id."""
    room_binding = await archivist.bot._topic_binding(
        SimpleNamespace(room_id="!naehen:simpson", name="Thema: Nähprojekte",
                        users={u: object() for u in MEMBERS}), "@marge:simpson")
    assert room_binding.bucket == OLD

    outcome = await _sent_to_the_room(archivist, "Knopfleiste annähen", binding=room_binding,
                                      classifier=Classifier(meanwhile=lambda: _rename(memory)))

    assert outcome.vault_path.startswith(f"{NEW}/notes/")
    tags = parse(memory.text(outcome.vault_path))["tags"]
    assert "naehprojekte" in tags and "nahprojekte" not in tags
    assert _forgejo(memory, rf"^{OLD}/") == []
    assert _forgejo(memory, r"/topic\.md$") == [f"{NEW}/topic.md"]
