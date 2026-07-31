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

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "memory"))

from lib import (  # noqa: E402
    load_correspondents_from_vault,
    load_persons_from_vault,
)


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


def test_a_vault_with_nothing_in_it_is_not_an_error(bare_host, tmp_path):
    """The empty case ran before the import did, which is what hid this.

    `stack memory person` looked fine against an empty vault because it
    returned before reaching the import. Pin both loaders on an empty
    vault so that early return can never again pass for proof that the
    populated path works.
    """
    assert load_persons_from_vault(tmp_path) == []
    assert load_correspondents_from_vault(tmp_path, shared_bucket="family") == []
