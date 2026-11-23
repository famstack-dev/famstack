"""Which people and tags a capture puts on its card, as the family finds them in the memory.

A capture is a note someone sent to a room or typed into `stack memory
capture`. The archivist asks a model who it is about and what it is
about, and the mirror writes the answer through the memory's curator: the
people the household declares become ids in `persons`, any other name stays
a plain name in the About line, and each tag becomes a topic id where the
ontology (or a topic card) knows it, else a slugged keyword.

Each test states the wanted behavior. Where the code does it today, the
test passes and protects it. Where it does not, the test is a strict
expected failure with a marker of `tests.household` that says what is
wanted, and it fails for the reason its docstring gives. They drive the capture pipeline as the
archivist builds it (`CapturePipeline.capture_text`, its real classifier,
the mirror, the real curator over a bare repository: `running_memory`), and
`./stack memory capture` as a person runs it. One model stands in, over
HTTP, for the capture and the diary alike (`tests.household`). What a test
asserts is what the memory then holds: the card's `persons`, `tags` and
About line. The household is the Simpsons.

"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

import stack.ai.models as ai_models
from stack.frontmatter import parse
from stack.memory import MemoryAPI
from stacklets.docs.bot import topic_rooms
from stacklets.docs.bot.capture_pipeline import CapturePipeline
from stacklets.docs.bot.extractors import TextExtractor
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.pipeline import Classifier
from stacklets.docs.bot.vault_context import VaultContext
from stack.topics import TopicCard, topic_card_titles, topic_cards
from tests.household import HOUSEHOLD_FILES, PEOPLE, Household
from tests.memory.unit.instance import running_memory

CAMPING = "Homer and Lisa and Mr. Burns in the camper van"
# Names no one: the people on a card are the ones the model names.
MEETING = "We met Mr. Burns at the lake."
LAKE = "We drove to the lake and stayed three days."
ABOUT = re.compile(r"^> \*\*About\*\* (.*)$", re.M)


@pytest.fixture
def household(httpserver) -> Household:
    """The model; a test fails when an engine asks it something it does not know,
    because it would then answer `{}` and only the positive tests would notice."""
    model = Household(httpserver)
    yield model
    assert model.unknown == []


@pytest.fixture
def memory(request, tmp_path, household, monkeypatch):
    """The Simpsons' memory with their people, topics and one topic card of their own.

    A test that needs another vault passes its files indirectly:
    `@pytest.mark.parametrize("memory", [SCHOOL], indirect=True)`."""
    files = getattr(request, "param", HOUSEHOLD_FILES)
    with running_memory(tmp_path / "data", files, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons", model=household.url) as running:
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(running.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        # The classifier resolves its model name from the bot's environment.
        monkeypatch.setitem(ai_models._MODELS, "archivist-bot", "stand-in")
        yield running


@pytest.fixture
def vault() -> VaultContext:
    return VaultContext(language="en", shared_bucket="family")


@pytest.fixture
def mirror(memory, vault, tmp_path) -> GitMirror:
    return GitMirror(data_dir=tmp_path / "archivist", vault=vault,
                     memory=MemoryAPI.from_container(memory.environ))


@pytest.fixture
def archivist(mirror, vault, household) -> CapturePipeline:
    """The capture pipeline as the archivist builds it, with the household's model."""
    return pipeline(mirror, vault, Classifier(household.llm(), language="en"))


def pipeline(mirror, vault, classifier, paperless=None) -> CapturePipeline:
    return CapturePipeline(
        url_extractor=None, text_extractor=TextExtractor(), classifier=classifier,
        mirror=mirror, paperless=paperless, bot_name="archivist-bot",
        classify_max_chars=100_000, capture_keep_body=False,
        vault=vault)


def files(archivist, memory, words: str = LAKE, by: str = "marge", **kwargs) -> dict:
    """`by` (Marge) sends `words` to a room; returns the card the memory then holds:
    its frontmatter, its About line and its path."""
    outcome = asyncio.run(archivist.capture_text(text=words, sender_mxid=f"@{by}:simpson", **kwargs))
    assert outcome.status == "captured", outcome
    page = memory.page(outcome.vault_path)
    about = ABOUT.search(page)
    return {**parse(page), "path": outcome.vault_path, "about": about.group(1) if about else ""}


# ── People ───────────────────────────────────────────────────────────────

def test_a_capture_names_the_people_the_household_declares(archivist, memory, household):
    """Given a note naming Homer and Lisa, when the archivist files it, then
    `persons` holds their ids and the About line links both."""
    household.says(persons=["Homer", "Lisa"])

    card = files(archivist, memory, "Homer and Lisa went camping")

    assert card["persons"] == ["homer", "lisa"]
    assert card["about"].count("](") == 2
    assert "people/homer.md" in card["about"] and "people/lisa.md" in card["about"]


def test_a_capture_leaves_a_name_nobody_goes_by_off_the_card(archivist, memory, household):
    """Given the model names "Mr. Burns", whom the household does not know,
    then the name is on neither `persons` nor the About line, and
    the note's words still hold it. The curator accepts the card."""
    household.says(persons=["Homer", "Mr. Burns"])

    card = files(archivist, memory, MEETING)

    assert card["persons"] == ["homer"]
    assert "Mr. Burns" not in card["about"]
    assert MEETING in memory.page(card["path"])


def test_a_capture_with_no_person_named_is_about_its_sender(archivist, memory, household):
    """Given the model names nobody, then the card is about the one who sent it."""
    household.says(persons=[], tags=["Camper Van"])

    assert files(archivist, memory, "Fabric for the quilt")["persons"] == ["marge"]


# ── Tags ─────────────────────────────────────────────────────────────────

def test_a_capture_tags_a_topic_by_its_id_and_drops_what_is_no_tag(archivist, memory, household):
    """Given the model answers "Camper Van", a topic card of the household,
    "Health", a topic of the ontology, and "Person: Homer" and "2026", then the
    card's tags are the two topics' ids: a `Person:` tag is no tag
    (`persons` says who), and a number has no letter to make a keyword of."""
    household.says(persons=["Homer"], tags=["Camper Van", "Health", "Person: Homer", "2026"])

    assert files(archivist, memory)["tags"] == ["van", "health"]


def test_a_capture_keeps_the_keywords_the_model_gave_as_they_are_spelled(archivist, memory, household):
    """Given the model answers "Camper Van", "LLMs" and "llm", then the card
    has the id of the topic card "Camper Van" and the two keywords as
    spelled (`llms`, `llm`): variants of a word are not folded."""
    household.says(persons=["Homer"], tags=["Camper Van", "LLMs", "llm"])

    assert files(archivist, memory)["tags"] == ["van", "llms", "llm"]


def test_a_topic_room_capture_has_the_rooms_topic_as_its_first_tag(archivist, memory, household):
    """Given a note in the room "Topic: Quilt for Lisa", then it is filed in
    the topic's folder and its first tag is the topic's id, the model's tags
    after it."""
    household.says(persons=["Lisa"], tags=["Camper Van"])
    parsed = topic_rooms.parse_topic_name("Topic: Quilt for Lisa")
    state = topic_rooms.make_room_state(
        parsed=parsed, scope="shared", bootstrapped_by="@marge:simpson", sender_localpart="marge",
        shared_bucket="family", bootstrapped_at="2026-11-22T10:00:00Z")
    binding = topic_rooms.binding_from_state(state)

    card = files(archivist, memory, "Fabric for the quilt",
                 seed_topics=binding.seed_topics, bucket=binding.bucket)

    assert card["path"].startswith("family/quilt-for-lisa/notes/")
    assert card["tags"][0] == "quilt-for-lisa"


# ── What the model is shown ──────────────────────────────────────────────

def test_the_model_is_shown_the_households_people(archivist, memory, household):
    """Given a household of five declared people, then the prompt lists their
    first names."""
    files(archivist, memory)

    assert 'Family members: ["Abe", "Bart", "Homer", "Lisa", "Marge"]' in household.prompts[-1]


def test_the_model_is_shown_the_topics_with_their_names(archivist, memory, household):
    """Given the household's topics (Health of the ontology, Camper Van and
    Quilt for Lisa as topic cards), then the prompt of a capture lists them
    by name, the topic cards first, so the model can pick a topic. Marge's
    personal topic is not among them: Marge's topic is hers alone."""
    files(archivist, memory, by="homer", bucket="family")

    assert shown_topics(household.prompts[-1]) == ["Quilt for Lisa", "Camper Van", "Health"]


def shown_topics(prompt: str) -> list[str]:
    """The topics a capture prompt lists: the one JSON list of strings in it
    that is not the family's names; fails when there is none."""
    lists = [json.loads(m) for m in re.findall(r'^[^"\n]*: (\[".*\])$', prompt, re.M)
             if not m.startswith('["Abe"')]
    assert len(lists) == 1, f"one list of topics expected in the prompt, found {lists}"
    return lists[0]


# ── The topics the model chose ───────────────────────────────────────────
#
# A capture's answer has keywords (`tags`) and the topics the model chose
# from its list (`topics`). A synonym names a topic only in `topics`: on a
# keyword it would make a camping-gas note a utilities note.

SCHOOL_ONTOLOGY = """\
[topic.health]
names = { en = "Health", de = "Gesundheit" }

[topic.utility]
names = { en = "Utilities", de = "Versorger" }
synonyms = { de = ["Gas", "Wasser"], en = ["gas", "water"] }

[topic.education]
names = { en = "Education", de = "Bildung" }
synonyms = { de = ["Schule"], en = ["school", "training"] }

[topic.travel]
names = { en = "Travel", de = "Reise" }
synonyms = { de = ["Urlaub"], en = ["holiday"] }

[topic.vehicle]
names = { en = "Vehicle", de = "Fahrzeug" }
synonyms = { de = ["Auto"], en = ["car"] }
"""

# The household calls its kindergarten "Schule": a topic card `kita` with that title.
SCHOOL = {**PEOPLE, "ontology.toml": SCHOOL_ONTOLOGY,
          "family/kita/topic.md": "---\ntype: topic\ncard_id: topic-kita1\ntitle: Schule\n---\n\n# Schule\n"}

in_the_school_household = pytest.mark.parametrize("memory", [SCHOOL], indirect=True, ids=["school"])


@in_the_school_household
def test_a_topic_the_model_chose_gets_its_id_by_synonym_name_or_id(archivist, memory, household):
    """Given the model chose "holiday" (a synonym of Travel), "Bildung" (the
    German name of Education) and "kita" (a topic card's id), then the card
    has the three topics' ids."""
    household.says(persons=["Lisa"], tags=[], topics=["holiday", "Bildung", "kita"])

    assert files(archivist, memory)["tags"] == ["travel", "education", "kita"]


@in_the_school_household
def test_a_topic_cards_title_wins_over_a_synonym(archivist, memory, household):
    """Given the topic card `kita` titled "Schule", and "Schule" a synonym of
    Education, when the model chooses "Schule", then the card has `kita`:
    the household's own topic is what it means."""
    household.says(persons=["Lisa"], tags=[], topics=["Schule"])

    assert files(archivist, memory)["tags"] == ["kita"]


@in_the_school_household
def test_a_keyword_is_never_read_as_a_synonym(archivist, memory, household):
    """Given the keywords "Gas", "Wasser" and "Auto", each a synonym of a
    topic, and no topic chosen, then they stay keywords: a note about
    camping gas is not a utilities note."""
    household.says(persons=["Homer"], tags=["Gas", "Wasser", "Auto"], topics=[])

    assert files(archivist, memory)["tags"] == ["gas", "wasser", "auto"]


@in_the_school_household
def test_a_word_on_no_list_is_no_topic(archivist, memory, household):
    """Given the model chose "Mars", which names no topic, then the card has
    no topic for it, and its keywords stay."""
    household.says(persons=["Homer"], tags=["telescope"], topics=["Mars"])

    assert files(archivist, memory)["tags"] == ["telescope"]


# ── A topic room ─────────────────────────────────────────────────────────

def in_topic_room(archivist, memory, name: str, words: str = "Packing list") -> dict:
    """Marge's note in the room `name` ("Topic: Schule"); returns the card."""
    parsed = topic_rooms.parse_topic_name(name)
    state = topic_rooms.make_room_state(
        parsed=parsed, scope="shared", bootstrapped_by="@marge:simpson", sender_localpart="marge",
        shared_bucket="family", bootstrapped_at="2026-11-22T10:00:00Z")
    binding = topic_rooms.binding_from_state(state)
    return files(archivist, memory, words, seed_topics=binding.seed_topics, bucket=binding.bucket)


# "Schule" only as a synonym of Education: no topic card of that title.
SYNONYM_ONLY = {**PEOPLE, "ontology.toml": SCHOOL_ONTOLOGY}


@pytest.mark.parametrize("memory", [SYNONYM_ONLY], indirect=True, ids=["synonym only"])
def test_a_topic_rooms_id_is_kept_where_a_synonym_would_take_it(archivist, memory, household):
    """Given the room "Topic: Schule", whose topic card was never written,
    and "Schule" a synonym of Education, then the note's first tag is the
    room's id `schule`, and the topic the model chose by that synonym
    follows it as `education`."""
    household.says(persons=["Lisa"], tags=[], topics=["Schule"])

    card = in_topic_room(archivist, memory, "Topic: Schule")

    assert card["path"].startswith("family/schule/")
    assert card["tags"] == ["schule", "education"]


@in_the_school_household
def test_a_topic_room_named_like_a_topic_gets_that_topics_id(archivist, memory, household):
    """Given the room "Topic: Reise", and "Reise" the German name of Travel,
    then its id is no tag of its own: the note's first tag is `travel`, as
    the tag rule writes the word."""
    household.says(persons=["Lisa"], tags=["tent"], topics=[])

    assert in_topic_room(archivist, memory, "Topic: Reise")["tags"] == ["travel", "tent"]


@pytest.mark.parametrize("memory", [SYNONYM_ONLY], indirect=True, ids=["synonym only"])
def test_a_correction_in_a_topic_room_keeps_the_rooms_id(archivist, memory, household):
    """Given a note in "Topic: Schule", when Marge corrects it in its
    thread, then the corrected card's first tag is still `schule`."""
    household.says(persons=["Lisa"], tags=["lunchbox"], topics=["Schule"])
    note = in_topic_room(archivist, memory, "Topic: Schule")

    asyncio.run(archivist.reprocess(
        vault_path=note["path"], user_hint="it is for Lisa", sender_mxid="@marge:simpson",
        reply_text="it is for Lisa", reply_id="$reply:simpson", reply_at="2026-11-22T11:00:00+00:00"))

    assert parse(memory.page(note["path"]))["tags"][0] == "schule"


# ── How many topics a prompt lists ───────────────────────────────────────

def _many_topics(count: int) -> str:
    return "".join(f'[topic.t{n:02}]\nnames = {{ en = "Topic {n:02}" }}\n\n' for n in range(count))


LONG_TITLE = "The big trip " + "and more " * 20

# Seventy topics of the ontology, two shared topic cards (one with a very
# long title) and one of Marge's.
MANY = {**PEOPLE, "ontology.toml": _many_topics(70),
        "family/van/topic.md": "---\ntype: topic\ncard_id: topic-van1\ntitle: Camper Van\n---\n",
        "family/trip/topic.md": f"---\ntype: topic\ncard_id: topic-trip1\ntitle: {LONG_TITLE.strip()}\n---\n",
        "marge/party/topic.md": "---\ntype: topic\ncard_id: topic-party1\ntitle: Homers 40th\n---\n"}


@pytest.mark.parametrize("memory", [MANY], indirect=True, ids=["seventy topics"])
def test_a_long_list_keeps_the_households_topic_cards_first(archivist, memory, household):
    """Given 70 topics of the ontology and three topic cards, when Marge
    files into her bucket, then the prompt lists 60 topics: her own topic
    card first, then the shared ones, then the ontology's. A title is cut
    at 80 characters."""
    files(archivist, memory, by="marge")

    shown = shown_topics(household.prompts[-1])
    assert len(shown) == 60
    assert shown[:3] == ["Homers 40th", LONG_TITLE.strip()[:80].strip(), "Camper Van"]
    assert shown[3:5] == ["Topic 00", "Topic 01"]


# ── The model is down ────────────────────────────────────────────────────

def test_a_capture_the_model_answers_with_nothing_is_about_its_sender_and_has_no_tags(
        archivist, memory, household):
    """Given a model that answers an empty object, when a note is filed, then
    the card holds the words, the sender is its person and it has no tags."""
    household.model.answers({})

    card = files(archivist, memory)

    assert not card.get("tags")
    assert card["persons"] == ["marge"]
    assert LAKE in memory.page(card["path"])


def test_a_capture_the_model_refuses_is_about_its_sender_and_has_no_tags(archivist, memory, household):
    """Given a model that refuses every request, when a note is filed, then
    the card is as with a silent model."""
    household.model.refuses(400)

    card = files(archivist, memory)

    assert not card.get("tags")
    assert card["persons"] == ["marge"]


def test_a_capture_with_no_model_configured_is_about_its_sender(mirror, vault, memory):
    """Given an archivist brought up with no AI, when it files a note, then
    the card is about its sender, as with a model that is down."""
    archivist = pipeline(mirror, vault, None)

    card = files(archivist, memory)

    assert not card.get("tags")
    assert card["persons"] == ["marge"]


# ── `stack memory capture` ───────────────────────────────────────────────

def run_capture_command(memory, words: str = CAMPING) -> int:
    """Homer runs `stack memory capture <words> --by homer`; returns the exit code."""
    try:
        memory.person("homer").runs("memory", "capture", [words, "--by", "homer"])
    except SystemExit as ended:
        return ended.code or 0
    return 0


def capture_by_command(memory, words: str = CAMPING) -> dict:
    """Homer files `words` with the command; returns his new card's frontmatter
    and About line. The exit code is the subject of its own test."""
    run_capture_command(memory, words)
    path = next(iter(memory.pages("homer/notes")))
    page = memory.page(path)
    return {**parse(page), "path": path, "about": ABOUT.search(page).group(1)}


def test_the_capture_command_ends_with_exit_code_0_after_it_filed(memory, household):
    """Given a note the model tags, when Homer files it with `stack memory
    capture`, then the card is filed and the command exits 0. (It once
    saved a tag cache under `/data`, a path of the bot runner's container,
    and exited 1 after it filed.)"""
    household.says(persons=["Homer"], tags=["Camper Van"])

    assert run_capture_command(memory) == 0


def test_the_capture_command_files_the_same_people_and_tags_as_the_archivist(memory, household, archivist):
    """Given the same note and the same model, when Homer files it with
    `stack memory capture` (through `Stack.run_cli_command`), then the card
    has the people and the tags the archivist's filing has."""
    household.says(persons=["Homer", "Lisa"], tags=["Camper Van"])
    by_archivist = files(archivist, memory, CAMPING)

    by_command = capture_by_command(memory)

    assert by_command["persons"] == by_archivist["persons"] == ["homer", "lisa"]
    assert by_command["tags"] == by_archivist["tags"]


def test_the_capture_command_shows_the_model_the_households_people(memory, household):
    """Given a note filed with `stack memory capture`, then the prompt lists
    the household's first names, as the archivist's prompt does."""
    capture_by_command(memory)

    assert "Homer" in household.prompts[-1].split("Family members:")[1].splitlines()[0]


# ── The tag rule and the vocabulary, module by module ────────────────────

@pytest.mark.parametrize("with_vault, topic_card", [(True, "van"), (False, "camper-van")],
                         ids=["with a vault", "without a vault"])
def test_the_mirror_writes_topics_by_id_and_keywords_as_slugs(with_vault, topic_card, memory, vault, tmp_path):
    """Given the mirror of an archivist that has the vault, or one that has
    none, then a topic of the ontology is written as its id, a topic card's
    title as the card's id (with a vault only: without one, it is a keyword),
    any other word as a slug, and a `Person:` tag is dropped."""
    mirror = GitMirror(data_dir=tmp_path / "archivist", vault=vault if with_vault else None,
                       memory=MemoryAPI.from_container(memory.environ))

    written = mirror._tags(["Quilt for Lisa", "Camper Van", "Health", "Road Trip", "Person: Lisa"],
                           "family/documents/2026/11/trip-p1.md")

    assert written == ["quilt-for-lisa", topic_card, "health", "road-trip"]


def test_the_vocabulary_section_names_the_ontologys_topics_in_the_households_language(memory, vault):
    """Given the household's ontology (Health), then the vocabulary section
    names its topic in the language asked for."""
    assert "Health" in vault.ontology_section("en")
    assert "Gesundheit" in vault.ontology_section("de")


def test_a_topic_card_declares_its_folder_as_a_topic_with_its_title_at_its_path(memory):
    """Given the vault's topic cards, then each declares its folder's name
    as a topic, titled by the card, with the card's path, which says who may
    read it; a person's card is no topic."""
    assert topic_cards(memory.vault) == [
        TopicCard("quilt-for-lisa", "Quilt for Lisa", "family/quilt-for-lisa/topic.md"),
        TopicCard("van", "Camper Van", "family/van/topic.md"),
        TopicCard("surprise-party", "Homers 40th", "marge/surprise-party/topic.md")]


def test_the_shared_buckets_card_titles_a_topic_id_two_buckets_share(tmp_path):
    """Given a topic `garden` in Bart's bucket ("Barts Secret Garden") and in
    the family's ("Garden"), then the id is titled by the family's card, so
    no search label or family card shows Bart's title."""
    for bucket, title in (("bart", "Barts Secret Garden"), ("family", "Garden")):
        (tmp_path / bucket / "garden").mkdir(parents=True)
        (tmp_path / bucket / "garden" / "topic.md").write_text(
            f"---\ntype: topic\ncard_id: topic-garden-{bucket}\ntitle: {title}\n---\n", encoding="utf-8")

    assert topic_card_titles(tmp_path, shared_bucket="family") == {"garden": "Garden"}


# ── The vocabulary of a place ────────────────────────────────────────────

def test_a_family_cards_vocabulary_holds_the_shared_topics_and_no_private_one(memory, vault):
    """Given the shared topic "Camper Van" and Marge's "Homers 40th", then a
    card in the family's bucket gets the shared topic's id, and Marge's
    title stays a plain keyword."""
    family = vault.vocabulary("family/notes")

    assert family.topics(["Camper Van", "Homers 40th", "Person: Homer"]) == ["van", "homers-40th"]


def test_a_card_in_marges_bucket_gets_her_topics_and_the_shared_ones(memory, vault):
    """Given the same topics, then a card in Marge's bucket, named by the
    bucket alone before its path is known, gets both topics' ids."""
    marge = vault.vocabulary("marge")

    assert marge.topics(["Camper Van", "Homers 40th"]) == ["van", "surprise-party"]


def test_a_new_buckets_vocabulary_holds_the_shared_topics(memory, vault):
    """Given a bucket nobody filed into yet (Abe's), then its cards still get
    the shared topics, and none of Marge's."""
    abe = vault.vocabulary("abe/notes")

    assert abe.topics(["Quilt for Lisa", "Homers 40th"]) == ["quilt-for-lisa", "homers-40th"]
