"""The framework's people and topic readers use neutral words.

`lib/stack/` is the generic core. Its modules name a person, a member, a topic and the
shared bucket. Family words belong to the stacklets and to the user documents.
This test fails when a `household_*` module returns or when one of the readers of the
people and the topics names a household or a character of the test families.
"""

from pathlib import Path

LIB = Path(__file__).resolve().parents[3] / "lib" / "stack"

READERS = [
    "people/names.py",
    "topics.py",
    "filing/people.py",
    "filing/topic_list.py",
    "filing/vocabulary.py",
]

# A config value such as the default bucket "family" stays a value, so only these words are banned.
BANNED = ["household", "simpson", "homer", "marge", "maggie", "lisa", "bart", "burns", "springfield"]


def test_no_module_of_the_core_is_named_household():
    assert sorted(p.name for p in LIB.rglob("*.py") if p.name.startswith("household")) == []


def test_the_readers_use_neutral_words():
    found = {}
    for name in READERS:
        text = (LIB / name).read_text(encoding="utf-8").lower()
        hits = [word for word in BANNED if word in text]
        if hits:
            found[name] = hits
    assert found == {}
