"""The wiki's model prompts: the file engine, and what each page's prompt carries.

The home, person and topic pages ask the model with a prompt read from
`templates/prompts/<kind>.md` (`prompt_files`). The prompt files are the
wording, and a reviewer reads them as the diff; these tests pin what does
not depend on the wording. What they promise:

- The engine fills values once, keeps or drops blocks by their value,
  leaves no blank line where a block stood alone, and refuses a marker
  that does not resolve instead of sending it to the model.
- A prompt file reads the same with a byte order mark or CRLF newlines.
- Each page's prompt carries every input it is given, carries an
  optional part exactly when its input is set, has no marker left, and
  does not carry the file's header comment.

Byte identity with the f-strings the files replaced was proven once, at
the commit that moved them, with golden files that are gone since.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from stacklets.memory.bot import home_page, person_page, prompt_files, topic_page
from stacklets.memory.bot.page_template import read_template
from stacklets.memory.bot.prompt_files import PromptError, render_text

TODAY = date(2026, 10, 11)

EVIDENCE = """[1] 2025-11-27 · note · Maggie's first word, filed by Marge
    Maggie said "Daddy" at breakfast. Homer cried.

[2] 2026-09-30 · document · Car insurance renewal, filed by Homer
    Springfield Mutual renews the car insurance for the pink sedan."""

NAMES = {"homer": "Homer Simpson", "marge": "Marge Simpson", "bart": "Bart Simpson",
         "lisa": "Lisa Simpson", "maggie": "Maggie Simpson"}

PREVIOUS = "Maggie is the baby of the family. [1]\n\n## Loves\n- Her pacifier. [1]"
NEW_EVIDENCE = "[2] 2026-09-30 · Car insurance renewal"
FACT = "Maggie was born on 14 January 2025."
RELATIVE = "Homer Simpson (Homer, Dad)"
TODO = "Ask Grandpa to feed the dog"


def _home(**changes) -> str:
    """The home page prompt for the Simpsons, with `changes` to the default inputs.

    The defaults set every optional part: a "lately" window of two
    sources and another name for Maggie.
    """
    inputs = dict(evidence=EVIDENCE, ids=list(NAMES), names=NAMES, lang="en",
                  recent=[1, 2], today=TODAY, aliases={"maggie": ["Margaret", "Mags"]})
    return home_page.prompt(**{**inputs, **changes})


def _person(**changes) -> str:
    """Maggie's page prompt, with `changes` to the default inputs.

    The defaults set every optional part: a fact, the family's names,
    a previous page with one new source since, and a "lately" source.
    """
    inputs = dict(display="Maggie Simpson", first_name="Maggie", evidence=EVIDENCE,
                  facts=[("born", FACT)], lately=[1], lang="en", family=[RELATIVE],
                  previous=PREVIOUS, new_evidence=[NEW_EVIDENCE])
    return person_page.prompt(**{**inputs, **changes})


def _topic(**changes) -> str:
    """The prompt for the shared topic "Itchy Scratchy Land", with `changes` to the default inputs.

    The defaults set every optional part: two open todo items.
    """
    inputs = dict(display="Itchy Scratchy Land", scope="shared", evidence_text=EVIDENCE,
                  todos=["Book the lodge", TODO], lang="en")
    return topic_page.prompt(**{**inputs, **changes})


# ── The engine ───────────────────────────────────────────────────────────

class TestTheEngine:
    def test_a_value_is_inserted_as_written(self):
        """Given a value that itself contains `{{x}}`, when it is filled in,
        then it stays as written. Evidence is the family's text, and may hold anything."""
        assert render_text("Sources: {{evidence}}", {"evidence": "{{x}} and {{#y}}"}) \
            == "Sources: {{x}} and {{#y}}"

    def test_a_missing_value_is_an_error(self):
        """Given a marker the caller has no value for, when it is filled,
        then it raises, naming the file. A silent gap would reach the model."""
        with pytest.raises(PromptError, match=r"home\.md.*today"):
            render_text("Today is {{today}}.", {}, source="home.md")

    @pytest.mark.parametrize("value, kept", [("1, 2", "set"), ("", "unset"), (True, "set"), (False, "unset")])
    def test_a_block_and_its_inverse_follow_the_value(self, value, kept):
        """Given a block and its inverse, when the value is set or not,
        then exactly one of them is kept."""
        text = "{{#lately}}set{{/lately}}{{^lately}}unset{{/lately}}"
        assert render_text(text, {"lately": value}) == kept

    def test_a_dropped_block_on_its_own_lines_leaves_no_blank_line(self):
        """Given block markers alone on their lines, when the block is dropped or kept,
        then the lines around it join as if the markers were never there."""
        text = "before\n  {{#x}}\ninside\n  {{/x}}\nafter\n"
        assert render_text(text, {"x": ""}) == "before\nafter\n"
        assert render_text(text, {"x": "y"}) == "before\ninside\nafter\n"

    def test_blocks_of_different_names_nest(self):
        """Given a block inside another, when both are decided,
        then each follows its own value."""
        text = "{{#a}}A{{#b}}B{{/b}}{{^b}}not B{{/b}}{{/a}}"
        assert render_text(text, {"a": True, "b": ""}) == "Anot B"

    @pytest.mark.parametrize("broken", [
        "{{#fact}}x{{/facts}}",            # mistyped closer
        "{{#fact}}x",                      # unclosed block
        "x{{/fact}}",                      # closer without an opener
        "{{ name }}",                      # spaces
        "{{first-name}}",                  # not a name
        "{{#a}}x{{#a}}y{{/a}}z{{/a}}",     # same-name nesting, not supported
        "{{name",                          # never closed
    ])
    def test_a_broken_marker_fails_instead_of_reaching_the_model(self, broken):
        """Given a marker that does not resolve, when the text is filled,
        then it raises, naming the file and the marker."""
        values = {"fact": "x", "facts": "x", "name": "Bart", "first_name": "Bart", "a": True}
        with pytest.raises(PromptError, match=r"^person\.md: marker "):
            render_text(broken, values, source="person.md")

    @pytest.mark.parametrize("raw", [
        "﻿<!--\nhead\n-->\nline one\r\nline two\r\n",
        "<!--\r\nhead\r\n-->\r\nline one\r\nline two\r\n",
        "<!--\nhead\n-->\nline one\nline two\n",
    ])
    def test_a_file_reads_the_same_with_a_bom_or_crlf(self, tmp_path, raw):
        """Given a prompt file saved with a byte order mark or Windows newlines,
        when it is read, then it reads as the plain file does. Editors add both."""
        path = tmp_path / "prompt.md"
        path.write_bytes(raw.encode("utf-8"))
        assert read_template(path) == "line one\nline two\n"


# ── What each page's prompt carries ─────────────────────────────────────

PROMPTS = {"home": _home, "person": _person, "topic": _topic}


@pytest.mark.parametrize("page", sorted(PROMPTS))
@pytest.mark.parametrize("lang", ["en", "de"])
def test_no_marker_and_no_header_comment_reach_the_model(page, lang):
    """Given each page's prompt with every optional part set, in each language,
    when it is built, then no marker is left and the file's comment is not in it."""
    prompt = PROMPTS[page](lang=lang)
    assert "{{" not in prompt
    assert "<!--" not in prompt and "prompt_files.py" not in prompt
    header = read_template(prompt_files.PROMPTS / f"{page}.md")
    assert prompt.startswith(header[:40])


@pytest.mark.parametrize("page", sorted(PROMPTS))
def test_the_answer_example_is_valid_json(page):
    """Given each page's prompt, when the example of the answer is read as JSON,
    then it parses. A model copies the example's shape, quotes included."""
    prompt = PROMPTS[page]()
    start = prompt.index("\n{\n", prompt.index("Answer with one JSON object")) + 1
    example = prompt[start:prompt.index("\n}\n", start) + 2]
    assert isinstance(json.loads(example), dict)


class TestTheHomePrompt:
    def test_it_carries_every_input(self):
        """Given the Simpsons, when the home prompt is built, then it carries the
        date, every person with their names, the sources and the language."""
        prompt = _home(lang="de")
        assert TODAY.isoformat() in prompt and EVIDENCE in prompt and "German" in prompt
        assert all(f"- {pid}: {name}" in prompt for pid, name in NAMES.items())
        assert "Margaret, Mags" in prompt

    def test_lately_is_asked_for_only_with_recent_sources(self):
        """Given recent sources or none, when the home prompt is built, then the
        "lately" field, and the rule on how to write it, are there exactly when
        there are some."""
        assert '"lately":' in _home(recent=[1, 2]) and '"lately"' not in _home(recent=[])
        assert "1, 2" in _home(recent=[1, 2])

    def test_a_person_without_a_name_is_still_listed(self):
        """Given a person the family declared without a full name, when the prompt
        is built, then they are on the roster by their id."""
        assert "- maggie: " in _home(names={**NAMES, "maggie": ""}, aliases=None)


class TestThePersonPrompt:
    def test_it_carries_every_input(self):
        """Given Maggie with every optional input, when her prompt is built, then
        it carries her names, the sources, the facts, the family, the previous page,
        what is new, the "lately" source and the language."""
        prompt = _person(lang="de")
        for given in ("Maggie Simpson", EVIDENCE, FACT, RELATIVE, PREVIOUS, NEW_EVIDENCE, "German"):
            assert given in prompt
        assert "sources 1:" in prompt

    @pytest.mark.parametrize("change, absent", [
        ({"facts": []}, FACT),
        ({"family": None}, RELATIVE),
        ({"previous": ""}, PREVIOUS),
        ({"new_evidence": []}, NEW_EVIDENCE),
    ])
    def test_an_optional_part_is_there_exactly_when_its_input_is(self, change, absent):
        """Given one optional input left out, when the prompt is built,
        then what it would carry is not there."""
        assert absent not in _person(**change)

    def test_what_is_new_is_only_said_of_a_previous_page(self):
        """Given new sources but no previous page, when the prompt is built,
        then the new sources are not listed: there is nothing they are new to."""
        assert NEW_EVIDENCE not in _person(previous="")


class TestTheTopicPrompt:
    def test_it_carries_every_input(self):
        """Given a topic with todo items, when its prompt is built, then it carries
        its name, the sources, the todo items and the language."""
        prompt = _topic(lang="de")
        assert "Itchy Scratchy Land" in prompt and EVIDENCE in prompt and "German" in prompt
        assert f"- {TODO}" in prompt

    def test_todo_items_are_there_exactly_when_the_topic_has_some(self):
        """Given a topic with no open items, when its prompt is built,
        then no todo item line is in it."""
        assert f"- {TODO}" not in _topic(todos=[])

    def test_a_shared_topic_and_a_persons_own_read_differently(self):
        """Given the same topic as shared and as one person's own, when the prompts are
        built, then they differ, and only in the sentence that says whose it is."""
        shared, own = _topic(scope="shared"), _topic(scope="personal")
        assert shared != own
        assert shared.splitlines()[1:] == own.splitlines()[1:]
