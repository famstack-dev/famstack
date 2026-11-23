"""The memory lib reads the vault on a host with no pip packages.

`./stack` runs under the system interpreter with `PYTHONPATH=lib` and
nothing else. README.md calls the CLI "zero pip deps" and
docs/admin-guide.md says "no virtualenvs, no pip install. That is
intentional." Every host-side read path therefore has to work with the
stdlib plus `stack.*`.

This is easy to break without noticing, because the `test` extra
installs `python-frontmatter` for the bot suites. A loader that reaches
for it stays green here and fails for every real user the moment they
run the command. That is exactly what happened: `stack memory person`
shipped and could not run on any clean host.

So these tests make the *production* environment the thing under test.
Blocking the module in `sys.modules` is what a machine that never ran
`pip install` looks like from inside an import statement. Assert on the
data the loaders return, not on which parser they chose, so a future
swap to another stdlib parser keeps them passing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from stacklets.memory.lib import (  # noqa: E402
    card_ids_for,
    correspondent_titles_for,
    load_correspondents_from_vault,
)
from stack.people.names import load_persons_from_vault  # noqa: E402


@pytest.fixture
def bare_host(monkeypatch):
    """A host where `import frontmatter` fails, as on a real install.

    Setting the entry to None is how CPython represents "this import
    has already been tried and there is nothing there": the next
    `import frontmatter` raises ImportError without touching the disk.
    """
    monkeypatch.setitem(sys.modules, "frontmatter", None)
    with pytest.raises(ImportError):
        import frontmatter  # noqa: F401
    return True


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── persons: what `stack memory person` walks ────────────────────────

def test_persons_load_without_the_pip_package(bare_host, tmp_path):
    """`stack memory person <name>` resolves a name on a clean host.

    Name resolution is the only reason that command parses frontmatter
    at all -- the profile body is stripped with a regex. If this raises,
    the command is dead on arrival for everyone.
    """
    _write(tmp_path / "marge" / "about.md",
           "---\ntitle: Marge\nslug: marge\ncanonical: Marge\n"
           "synonyms:\n  - Marjorie\n  - Marge Bouvier\n---\n\n# Marge\n")

    [person] = load_persons_from_vault(tmp_path)

    assert person.canonical == "Marge"
    assert person.slug == "marge"
    assert person.synonyms == ["Marjorie", "Marge Bouvier"]


def test_person_kind_filter_survives_on_a_bare_host(bare_host, tmp_path):
    """A non-person page at a member path stays excluded.

    Worth pinning separately: if the parser returned nothing on a bare
    host, `kind` would read as absent, the page would fall back to its
    slug, and a correspondent would quietly enter the family roster.
    Degrading to an empty dict is not a safe failure here.
    """
    _write(tmp_path / "duff-insurance" / "about.md",
           "---\nkind: correspondent\ncanonical: Duff Insurance\n---\n")

    assert load_persons_from_vault(tmp_path) == []


def test_a_person_whose_frontmatter_does_not_parse_is_named_and_left_out(bare_host, tmp_path, capsys):
    """A broken page is never guessed at: its folder is no person's name.

    Read as a page without fields, it would enter the classifier's
    prompt as a person called `moe`.
    """
    broken = _write(tmp_path / "moe" / "about.md",
                    "---\ncanonical: Moe\n  indented: nope\n---\n")
    _write(tmp_path / "marge" / "about.md", "---\ncanonical: Marge\n---\n")

    assert [p.canonical for p in load_persons_from_vault(tmp_path)] == ["Marge"]
    assert capsys.readouterr().err.count(str(broken)) == 1


# ── correspondents: same import, same exposure ───────────────────────

def test_correspondents_load_without_the_pip_package(bare_host, tmp_path):
    """`stack memory correspondents` shares the defect and the fix."""
    _write(tmp_path / "family" / "correspondents" / "duff.md",
           "---\nkind: correspondent\ncanonical: Duff Brewery\n"
           "aliases:\n  - Duff Beer\n---\n\n# Duff Brewery\n")

    [correspondent] = load_correspondents_from_vault(tmp_path,
                                                     shared_bucket="family")

    assert correspondent.canonical == "Duff Brewery"
    assert correspondent.aliases == ["Duff Beer"]


# ── query rewrite: the LLM stack must stay off the host ──────────────

def test_the_query_rewrite_does_not_drag_the_llm_stack_onto_the_host():
    """`stack memory search` imports the module that holds the rewrite.

    The rewrite needs a model, so its imports (loguru, the OpenAI SDK
    through `stack.ai.client`) are inside the function that uses them.
    Hoisting one of those to the top of the module is a natural-looking
    tidy-up that kills the search command on every clean host, and no
    test in the bot suites would notice, because the bot has both.

    A subprocess is the point: it imports the module fresh, with those
    packages blocked the way a machine that never ran `pip install`
    blocks them.
    """
    probe = textwrap.dedent("""
        import sys
        for absent in ("loguru", "openai", "aiohttp", "frontmatter"):
            sys.modules[absent] = None
        import lib
        print(lib.keywords_to_regex(["C++", "Camping"]))
        print("keywords" in lib.build_rewrite_prompt("q?", "(...)", "en"))
        print(lib.parse_rewrite_response('{"keywords": ["Zelt"]}'))
    """)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([
        str(_REPO_ROOT / "lib"), str(_REPO_ROOT / "stacklets" / "memory"),
    ])}
    result = subprocess.run([sys.executable, "-c", probe],
                            capture_output=True, text=True, timeout=60, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [r"C\+\+|Camping", "True", "['Zelt']"]


def test_a_vault_with_nothing_in_it_is_not_an_error(bare_host, tmp_path):
    """The empty case ran before the import did, which is what hid this.

    `stack memory person` looked fine against an empty vault because it
    returned before reaching the import. Pin both loaders on an empty
    vault so that early return can never again pass for proof that the
    populated path works.
    """
    assert load_persons_from_vault(tmp_path) == []
    assert load_correspondents_from_vault(tmp_path, shared_bucket="family") == []


# ── migration passes: a broken card is skipped, by name ──────────────

def test_a_migration_pass_names_a_card_whose_frontmatter_does_not_parse(tmp_path, capsys):
    """The pass leaves the card as it is and the log says which one."""
    vault = tmp_path / "vault"
    broken = "family/correspondents/moe.md"
    _write(vault / broken, "---\ntype: correspondent\ncanonical: Moe\n  indented: nope\n---\n")
    _write(vault / "family/correspondents/duff.md",
           "---\ntype: correspondent\ncanonical: Duff Brewery\n---\n")
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    subprocess.run(["git", "-C", str(vault), "add", "."], check=True)

    found = correspondent_titles_for(vault)

    assert list(found.changes) == ["family/correspondents/duff.md"]
    assert capsys.readouterr().err.count(broken) == 1


def test_a_migration_pass_over_valid_cards_says_nothing(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write(vault / "family/correspondents/duff.md",
           "---\ntype: correspondent\ncanonical: Duff Brewery\n---\n")
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    subprocess.run(["git", "-C", str(vault), "add", "."], check=True)

    correspondent_titles_for(vault)

    assert capsys.readouterr().err == ""


def _commit_all(vault: Path, message: str) -> str:
    git = ["git", "-C", str(vault), "-c", "user.name=Homer", "-c", "user.email=homer@simpson.test"]
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_a_push_that_leaves_a_broken_card_alone_reports_nothing(tmp_path, capsys):
    """The curator gives ids on every push; a broken card it does not touch
    is not named again each time, only by the pass over every card."""
    vault = tmp_path / "vault"
    broken = "family/correspondents/moe.md"
    _write(vault / broken, "---\ntype: correspondent\ntitle: Moe\n  indented: nope\n---\n")
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    before = _commit_all(vault, "a broken card")
    _write(vault / "homer/notes/donuts.md", "# Donuts\n")
    _commit_all(vault, "a note")

    pushed = card_ids_for(vault, since=before)

    assert list(pushed.changes) == ["homer/notes/donuts.md"]
    assert not any(broken in line for line in pushed.left)
    assert capsys.readouterr().err == ""
    assert [line for line in card_ids_for(vault).left if broken in line]


# ── topic cards: a broken one is named and left out ──────────────────

def test_a_topic_card_whose_frontmatter_does_not_parse_is_named_and_left_out(tmp_path, capsys):
    """The broken card declares no topic, and the log says which file."""
    from stack.topics import topic_card_titles

    broken = _write(tmp_path / "family" / "taxes" / "topic.md",
                    "---\ntype: topic\ntitle: Taxes\n  indented: nope\n---\n")
    _write(tmp_path / "family" / "school" / "topic.md",
           "---\ntype: topic\ntitle: School\n---\n")

    titles = topic_card_titles(tmp_path)

    assert list(titles.values()) == ["School"]
    err = capsys.readouterr().err
    assert err.count(str(broken)) == 1
    assert "frontmatter does not parse" in err
