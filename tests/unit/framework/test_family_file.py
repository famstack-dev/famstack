"""The family file: what `stack family set` accepts, refuses, and fills in.

Every writer of the family (the install from a file, the dashboard's first
run, a person with a text editor) is held to one schema and a few rules
the schema cannot say. A refusal names every problem with the field it is
about, so a screen can put the message beside the field and a terminal
beside the person. Tested through `stack.family.load`, which is what the
command reads a file with, and `people`, the file with its defaults.
"""

from __future__ import annotations

import json

import pytest

from stack import family


def fields(text: str, name: str = "family.toml") -> list[str]:
    """The fields the file's problems are about, in the order found."""
    return [p.field for p in family.load(text, name)[1]]


ONE = 'name = "Simpson"\n[[people]]\nname = "Homer Simpson"\n'


def test_a_name_and_one_person_is_a_whole_family():
    assert fields(ONE) == []


# ── The shape ────────────────────────────────────────────────────────────

def test_the_name_and_the_people_are_required():
    assert fields("") == ["name", "people"]
    assert fields('name = "Simpson"\npeople = []\n') == ["people"]
    assert fields('name = "Simpson"\n[[people]]\nwords = "he"\n') == ["people.0.name"]


def test_a_relation_or_words_outside_the_list_are_refused():
    text = ONE + 'relation = "uncle"\nwords = "they"\n'
    assert fields(text) == ["people.0.relation", "people.0.words"]


def test_an_empty_answer_counts_as_left_out():
    """A form sends "" for a field nobody filled in; that is no answer, not a wrong one."""
    data = {"name": "Simpson", "domain": "", "people": [
        {"name": "Homer Simpson", "relation": "", "words": ""}]}

    assert family.problems(data) == []
    homer = family.people(data)[0]
    assert (homer["relation"], homer["words"]) == ("parent", None)


@pytest.mark.parametrize("domain", ["simpsons.com", "Simpsons.COM", "home.simpsons.co.uk", "my-family.de"])
def test_a_domain_name_is_a_web_address(domain):
    assert fields(ONE.replace("\n[[", f'\ndomain = "{domain}"\n[[', 1)) == []


@pytest.mark.parametrize("domain", [
    "simpsons", "https://simpsons.com", "simpsons.com/home", "192.168.1.20", "-simpsons.com", "simp sons.com"])
def test_anything_else_is_not(domain):
    assert fields(ONE.replace("\n[[", f'\ndomain = "{domain}"\n[[', 1)) == ["domain"]


def test_a_field_the_file_does_not_know_is_named():
    problems = family.load(ONE + "age = 39\n", "f.toml")[1]

    assert [p.field for p in problems] == ["people.0.age"]
    assert "name, relation, words and looks_after" in problems[0].message


def test_values_of_the_wrong_kind_are_refused():
    assert fields(ONE + 'looks_after = "yes"\n') == ["people.0.looks_after"]
    assert fields('name = "Simpson"\n[people]\nname = "Homer"\n') == ["people"]
    assert fields("[]", "family.json") == [""]


# ── The rules ────────────────────────────────────────────────────────────

def test_a_name_twice_is_refused_whatever_its_case():
    text = ONE + '[[people]]\nname = "homer  simpson"\n'
    assert fields(text) == ["people.1.name"]


def test_two_names_that_sign_in_alike_are_refused():
    problems = family.load(ONE + '[[people]]\nname = "Homer Jay"\n', "f.toml")[1]

    assert [p.field for p in problems] == ["people.1.name"]
    assert "homer" in problems[0].message and "Homer Simpson" in problems[0].message


def test_a_first_name_with_nothing_in_a_to_z_gives_no_username():
    assert fields(ONE.replace("Homer Simpson", "李 明")) == ["people.0.name"]


def test_names_are_spelled_out_for_the_username():
    assert family.username("Jürgen Müller") == "juergen"
    assert family.username("Zoë") == "zoe"
    assert family.username("Anne-Marie") == "anne-marie"


@pytest.mark.parametrize("name, server_name", [
    ("Müller", "mueller"),
    ("Müller-Schmidt", "mueller-schmidt"),
    ("Groß", "gross"),
    ("Brontë", "bronte"),
    ("van_der Berg", "vanderberg"),
])
def test_the_chat_server_name_is_spelled_in_a_to_z(name, server_name):
    """The chat's server name is a DNS name, and Synapse refuses to start on
    anything else: `müller` and `van_der_berg` are both refused (checked
    against the Synapse messages ships, 2026-10-27)."""
    assert family.sanitize_server_name(name) == server_name


@pytest.mark.parametrize("name", ["simpson", "mueller-schmidt", "home.internal"])
def test_a_valid_server_name_stays_as_it_is(name):
    assert family.sanitize_server_name(name) == name


def test_naming_nobody_who_looks_after_the_server_is_refused():
    text = ONE + "looks_after = false\n"
    assert fields(text) == ["people"]


def test_every_problem_is_found_in_one_pass():
    """The shape's problems do not hide the rules' ones."""
    text = ('name = "Simpson"\ndomain = "simpsons"\n'
            '[[people]]\nname = "Homer"\nrelation = "uncle"\nlooks_after = false\n'
            '[[people]]\nname = "Homer"\n')
    assert fields(text) == ["domain", "people.0.relation", "people.1.name", "people"]


# ── The defaults ─────────────────────────────────────────────────────────

def test_the_first_two_are_parents_and_the_first_looks_after_the_server():
    data = {"name": "Simpson", "people": [
        {"name": "Homer"}, {"name": "Marge"}, {"name": "Bart"}, {"name": "Abe", "relation": "grandparent"}]}

    people = family.people(data)

    assert [p["relation"] for p in people] == ["parent", "parent", "child", "grandparent"]
    assert [p["looks_after"] for p in people] == [True, False, False, False]


def test_naming_someone_who_looks_after_the_server_names_only_them():
    data = {"name": "Simpson", "people": [
        {"name": "Homer"}, {"name": "Lisa", "looks_after": True}, {"name": "Marge", "looks_after": True}]}

    assert [p["looks_after"] for p in family.people(data)] == [False, True, True]


# ── Reading the file ─────────────────────────────────────────────────────

def test_json_reads_like_toml():
    data = {"name": "Simpson", "people": [{"name": "Homer Simpson"}]}

    assert family.load(json.dumps(data), "family.json") == (data, [])
    assert family.load(json.dumps(data), "-") == (data, [])
    assert family.load(ONE, "-")[0] == data


def test_a_file_that_does_not_parse_says_which_format_it_was_read_as():
    data, problems = family.load('{"name": ', "family.json")

    assert data is None
    assert problems[0].field == ""
    assert problems[0].message.startswith("The file is not valid JSON")
