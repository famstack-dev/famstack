"""The Simpsons' vocabulary and one model that both engines read: a test helper.

Two engines put people and tags on a card in the family's memory: the
archivist's capture (a note, a link, a file) and the diary's compile. Each
asks a model who a text is about and what it is about, and each then writes
what it got through its own rules. A test of "what people and tags does a
card get today" needs both engines to hear the same answer from one model,
so that a difference in the card is a difference in the engine.

    household = Household(httpserver)             # the model, on localhost
    household.says(persons=["Homer", "Grandpa Abe"], tags=["Camper Van"])
    with running_memory(tmp, HOUSEHOLD_FILES, model=household.url, ...):
        ...                                       # file a capture, compile the diary

`Household` answers the capture prompt with `tags` as its keywords and, unless
`topics` is given, as the topics it chose; the diary's entry prompt with `tags`
as its topics. It answers the diary's room-reading
prompt with a note for each message. The model is `tests.model.Model` (the
chat completions API on a pytest-httpserver) with an answering function, so
the real client sends every request. It keeps every prompt (`prompts`).

The vault is what a household holds: five declared people, a topic of the
ontology and three topic cards of the household's own.
"""

from __future__ import annotations

import json
import re

from openai import AsyncOpenAI

from stack.ai.client import LLM
from tests.model import Model

def _person(pid: str, name: str, aliases: tuple[str, ...] = ()) -> tuple[str, str]:
    said = "".join(f"\n  - {alias}" for alias in aliases)
    return (f"family/people/{pid}.md",
            f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n"
            + (f"aliases:{said}\n" if aliases else "") + f"---\n\n# {name}\n")


# Abe is a person; "Grandpa Abe" and "Mr. Burns" are not names anyone goes by.
PEOPLE = dict([_person("homer", "Homer Simpson", ("Mr. Simpson",)), _person("marge", "Marge Simpson"),
               _person("bart", "Bart Simpson"), _person("lisa", "Lisa Simpson"),
               _person("abe", "Abe Simpson")])

# One topic of the ontology. "LLMs" is no topic: it is a keyword.
ONTOLOGY = """\
[topic.health]
names = { en = "Health", de = "Gesundheit" }
"""


def _topic_card(bucket: str, topic_id: str, title: str) -> tuple[str, str]:
    return (f"{bucket}/{topic_id}/topic.md",
            f"---\ntype: topic\ncard_id: topic-{topic_id[:10].replace('-', '')}1\ntitle: {title}\n---\n\n# {title}\n")


# The household's own topics, as cards in a topic folder: two shared ones (the
# first has an id that is not its title's slug, so a test of it proves the card
# is read),
# and Marge's personal one, titled "Homers 40th", which Homer must not see.
TOPIC_CARDS = dict([_topic_card("family", "van", "Camper Van"),
                    _topic_card("family", "quilt-for-lisa", "Quilt for Lisa"),
                    _topic_card("marge", "surprise-party", "Homers 40th")])

HOUSEHOLD_FILES = {**PEOPLE, "ontology.toml": ONTOLOGY, **TOPIC_CARDS}

# ── What is wrong today, as expected failures ────────────────────────────
#
# A test of behavior that is wrong today carries one marker. Each is a
# strict expected failure: it fails now, and when the behavior is built it
# passes, which fails the run until the marker is removed. The reason states
# the wanted behavior.

_DIARY_ROOM_READING = "You are reading a family's private memories room"


class Household:
    """The model both engines ask, at its boundary.

        household = Household(httpserver)
        household.says(persons=["Homer"], tags=["Camper Van"])
        household.url        # where a command reaches it
        household.llm()      # the client a test hands to code that takes one
        household.prompts    # every prompt sent, the newest last

    Without `says` it names nobody and offers no tag. A prompt of a kind it
    does not know is answered `{}` and kept in `unknown`, so a test notices
    an engine that asks something new.
    """

    def __init__(self, httpserver):
        self.model = Model(httpserver)
        self.url = self.model.url
        self.prompts: list[str] = []
        self.unknown: list[str] = []
        self._persons: list[str] = []
        self._tags: list[str] = []
        self._topics: list[str] | None = None
        self._summary: str | None = None
        self.model.answering(self._answer)

    def says(self, *, persons: list[str] = (), tags: list[str] = (), topics: list[str] | None = None,
             summary: str | None = None) -> None:
        """What the model names from now on: these people and these tags or
        topics. A capture's answer has `tags` as its keywords and `topics`
        (else `tags` again) as the topics it chose from its list, so one
        call gives both engines the same words. Its title repeats the first
        line of the words it reads (a title is one line), and its summary
        repeats them all unless `summary` is given, so a name in the text is
        a name in the title and the summary, as in an answer that follows
        its input."""
        self._persons, self._tags, self._summary = list(persons), list(tags), summary
        self._topics = None if topics is None else list(topics)

    def llm(self) -> LLM:
        """The framework's client for this model, as a bot builds it."""
        return LLM(AsyncOpenAI(base_url=self.url, api_key="k"), namespace="archivist-bot")

    def prompt_of(self, containing: str) -> str:
        """The newest prompt containing `containing`; fails when none does."""
        found = [p for p in self.prompts if containing in p]
        assert found, f"no prompt contains {containing!r}: {len(self.prompts)} prompts sent"
        return found[-1]

    def _answer(self, messages: list[dict]) -> str:
        prompt = messages[-1]["content"]
        # A conversation is kept whole, so a test reads all the model was shown.
        self.prompts.append("\n\n".join(m["content"] for m in messages[1:]) if len(messages) > 2 else prompt)
        if prompt.startswith(_DIARY_ROOM_READING):
            count = len(re.findall(r"^\[\d+\] ", prompt, re.M))
            return json.dumps({"messages": [{"n": n, "mode": "note"} for n in range(1, count + 1)]})
        if prompt.startswith("You are filing one entry"):
            words = re.search(r"Recorded by \w+, [^\n]*\n(?:Spoken to:[^\n]*\n)?\n([^\n]*)", prompt).group(1)
            return json.dumps({
                "title": words.rstrip("."), "description": "A note.", "facts": [],
                "summary": words if self._summary is None else self._summary,
                "persons": self._persons, "quotes": [], "topics": self._tags})
        if "Return ONLY a JSON object" in prompt and '"persons"' in prompt:
            words = re.search(r"CONTENT \(untrusted[^\n]*\n---\n(.*?)\n---", prompt, re.S).group(1)
            return json.dumps({"title": words.split("\n")[0].rstrip("."), "clean": "",
                               "summary": words if self._summary is None else self._summary,
                               "tags": self._tags, "persons": self._persons,
                               "topics": self._tags if self._topics is None else self._topics})
        if "Answer what the events change" in prompt:
            # The card conversation: the people and tags it names now, as
            # whole lists, and a note's clean version as the card holds it.
            card = json.loads(messages[2]["content"])
            answer = {"persons": self._persons, "tags": [*self._tags, *(self._topics or [])]}
            return json.dumps({**answer, "clean": card["clean"]} if "clean" in card else answer)
        if prompt.startswith("Write the opening paragraph"):
            return "A month of camping and quilting."
        self.unknown.append(prompt[:80])
        return "{}"
