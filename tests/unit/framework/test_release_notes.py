"""The release notes, generated from the commit log.

`script/release-notes` turns the commits since the last tag into the
change list of a GitHub release, laid out the way docs/agent/dev.md
specifies ("Commits: the subject is the changelog"). These tests drive it
the way a maintainer does, from outside a repository whose history is
known, and hold the output to that spec.

The last class keeps git-cliff's copy of the types and scopes in step with
commit-lint's. git-cliff cannot read Python, so the table exists twice,
and two copies of one list drift unless something checks them.
"""

import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "script" / "release-notes"
CLIFF = tomllib.loads((REPO_ROOT / "cliff.toml").read_text())

_spec = importlib.util.spec_from_loader(
    "commit_lint",
    importlib.machinery.SourceFileLoader(
        "commit_lint", str(REPO_ROOT / "tools" / "commit-lint")),
)
commit_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(commit_lint)


# git-cliff runs through uvx, which downloads it on first use. The unit
# lane runs offline, so the tests that render notes run only where uv has
# it cached, which one `script/release-notes` call takes care of.
GIT_CLIFF = re.search(r"git-cliff@[\d.]+", SCRIPT.read_text())[0]
OFFLINE = {**os.environ, "UV_OFFLINE": "1"}


def _git_cliff_cached() -> bool:
    if shutil.which("uvx") is None:
        return False
    result = subprocess.run(["uvx", "--quiet", GIT_CLIFF, "--version"],
                            capture_output=True, env=OFFLINE)
    return result.returncode == 0


needs_git_cliff = pytest.mark.skipif(
    not _git_cliff_cached(), reason=f"{GIT_CLIFF} is not in the uv cache")


# ── A repository with a known history ────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com",
                    "-c", "user.name=T", *args], check=True, capture_output=True)


def _commit(repo: Path, message: str) -> None:
    (repo / "change").write_text(message)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


@pytest.fixture
def repo(tmp_path):
    """A checkout carrying the real script and config, tagged v0.1.0."""
    repo = tmp_path / "repo"
    (repo / "script").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "script")
    shutil.copy(REPO_ROOT / "cliff.toml", repo)
    _git(tmp_path, "init", "-q", str(repo))
    _commit(repo, "chore: start")
    _git(repo, "tag", "v0.1.0")
    return repo


def _notes(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the command from the directory above the checkout, the way the
    workspace release skill calls it."""
    return subprocess.run([str(repo / "script" / "release-notes"), *args],
                          capture_output=True, text=True, cwd=repo.parent,
                          env=OFFLINE)


def _sections(notes: str) -> list[str]:
    return re.findall(r"^### (.+)$", notes, re.M)


def _section(notes: str, heading: str) -> str:
    return notes.split(f"### {heading}\n", 1)[1].split("\n### ", 1)[0].strip()


# ── The layout ───────────────────────────────────────────────────────────

@needs_git_cliff
class TestTheLayout:

    def test_sections_come_in_the_documented_order(self, repo):
        """What needs the admin first, then what matters most to them."""
        for message in ["docs: explain the release gate",
                        "perf(ai): answer faster",
                        "fix(core): answer the first message",
                        "feat(photos): back up over Wi-Fi",
                        "security(messages): close the open room",
                        "feat(docs)!: move Paperless to 3.0.4"]:
            _commit(repo, message)

        result = _notes(repo)

        assert result.returncode == 0, result.stderr
        assert _sections(result.stdout) == [
            "Action required", "Security", "Added", "Fixed", "Performance",
            "Documentation"]

    def test_an_entry_names_what_changed_the_way_a_reader_knows_it(self, repo):
        """Scopes render as labels, entries with one label sit together,
        and entries with no scope come last in their section."""
        for message in ["fix(wiki): show the new page",
                        "fix: stop the clock drifting",
                        "fix(core): answer the first message",
                        "fix(diary): keep the entry date"]:
            _commit(repo, message)

        fixed = _section(_notes(repo).stdout, "Fixed")

        assert fixed.split("\n")[:4] == [
            "- **Core Stacklet:** Answer the first message",
            "- **Memory Stacklet:** Show the new page",
            "- **Memory Stacklet:** Keep the entry date",
            "- Stop the clock drifting",
        ]

    def test_internal_work_is_committed_but_not_announced(self, repo):
        for message in ["feat(photos): back up over Wi-Fi",
                        "refactor(core): split the runner",
                        "test(stack): cover the empty range",
                        "chore: tidy imports", "ci: cache uv",
                        "style: wrap long lines", "build: pin git-cliff"]:
            _commit(repo, message)

        notes = _notes(repo).stdout

        assert _sections(notes) == ["Added"]
        for internal in ["runner", "empty range", "imports", "cache uv",
                         "long lines", "pin git-cliff"]:
            assert internal not in notes

    def test_a_merge_is_not_an_entry(self, repo):
        """The merged commits are listed already. commit-lint's range
        check leaves merges out for the same reason."""
        _git(repo, "checkout", "-qb", "topic")
        _commit(repo, "feat(photos): back up over Wi-Fi")
        _git(repo, "checkout", "-q", "-")
        _git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #1 from x/topic",
             "topic")

        notes = _notes(repo).stdout

        assert "Merge pull request" not in notes
        assert "Back up over Wi-Fi" in notes

    def test_action_required_quotes_what_the_admin_must_do(self, repo):
        """Any `!`, `Upgrade:` or `BREAKING CHANGE:` lands here, whatever
        the type, with the footer quoted as markdown and kept inside its
        entry. The footer is the instruction; the subject alone is not."""
        _commit(repo, "feat(docs)!: move Paperless to 3.0.4\n\nWhy.\n\n"
                      "Upgrade: back up `~/famstack-data/docs` before restarting\n"
                      "with `./stack restart docs`. There is no downgrade.")
        _commit(repo, "chore(stack): drop the old label\n\n"
                      "Upgrade: run `./stack restart all` once.")
        _commit(repo, "fix(ai)!: drop the old engine\n\n"
                      "BREAKING CHANGE: remove `[ai] engine` from `stack.toml`.")

        notes = _notes(repo).stdout
        action = _section(notes, "Action required")

        assert _sections(notes) == ["Action required"]
        assert ("- **Docs Stacklet:** Move Paperless to 3.0.4\n\n"
                "  **Upgrade:** Back up `~/famstack-data/docs` before restarting\n"
                "  with `./stack restart docs`. There is no downgrade.") in action
        assert ("  **Upgrade:** Run `./stack restart all` once.") in action
        assert ("  **BREAKING CHANGE:** Remove `[ai] engine` from `stack.toml`."
                ) in action

    def test_a_subject_that_does_not_parse_is_shown_not_dropped(self, repo):
        """An unclassified subject blocks the tag (dev.md, Releases, step
        3b), so the notes put it where the maintainer will see it."""
        _commit(repo, "Move the docs stacklet to Paperless-ngx 3.0.4")

        notes = _notes(repo).stdout

        assert _section(notes, "Unclassified") == (
            "- Move the docs stacklet to Paperless-ngx 3.0.4")


# ── Which release ────────────────────────────────────────────────────────

@needs_git_cliff
class TestWhichRelease:

    def test_by_default_it_is_what_the_next_tag_will_ship(self, repo):
        _commit(repo, "feat(photos): back up over Wi-Fi")
        _git(repo, "tag", "v0.2.0")
        _commit(repo, "fix(core): answer the first message")

        notes = _notes(repo).stdout

        assert "Answer the first message" in notes
        assert "Back up over Wi-Fi" not in notes

    def test_latest_is_what_the_newest_tag_shipped(self, repo):
        _commit(repo, "feat(photos): back up over Wi-Fi")
        _git(repo, "tag", "v0.2.0")
        _commit(repo, "fix(core): answer the first message")

        notes = _notes(repo, "--latest").stdout

        assert "Back up over Wi-Fi" in notes
        assert "Answer the first message" not in notes

    def test_nothing_to_announce_says_so_instead_of_an_empty_release(self, repo):
        assert _notes(repo).stdout.strip() == "No user-facing changes since v0.1.0."

        _commit(repo, "chore: tidy imports")

        assert _notes(repo).stdout.strip() == "No user-facing changes since v0.1.0."


class TestAShallowCheckout:

    def test_is_refused_rather_than_read_as_a_quiet_release(self, repo, tmp_path):
        """git-cliff finds no commits in a shallow clone and says nothing
        about it, which reads exactly like a release with no changes."""
        _commit(repo, "feat(photos): back up over Wi-Fi")
        shallow = tmp_path / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}",
                        str(shallow)], check=True)

        result = _notes(shallow)

        assert result.returncode == 1
        assert "fetch --unshallow" in result.stderr


# ── One table, two readers ───────────────────────────────────────────────

class TestTheTablesAgreeWithCommitLint:
    """cliff.toml repeats TYPES and SCOPES from tools/commit-lint. These
    apply git-cliff's own rules to each name and compare the result."""

    @staticmethod
    def _section_for(kind: str) -> str:
        """The section a commit of this type lands in, "" if it is skipped."""
        for parser in CLIFF["git"]["commit_parsers"]:
            if "message" in parser and re.search(parser["message"], f"{kind}: x"):
                return "" if parser.get("skip") else re.sub(r"<!--.*?-->", "", parser["group"])
        raise AssertionError(f"no parser matches {kind!r}")

    @staticmethod
    def _label_for(scope: str) -> str | None:
        """The label git-cliff's preprocessors give this scope, None for General."""
        message = f"feat({scope}): x"
        for rule in CLIFF["git"]["commit_preprocessors"]:
            message = re.sub(rule["pattern"], rule["replace"].replace("${1}", r"\g<1>"),
                             message)
        match = re.fullmatch(r"feat\((.+)\): x", message)
        return match and match[1]

    def test_every_type_lands_in_the_section_commit_lint_names(self):
        named_in_cliff = {m[1] for p in CLIFF["git"]["commit_parsers"]
                          if (m := re.fullmatch(r"\^([a-z]+)", p.get("message", "")))}
        cliff = {kind: self._section_for(kind)
                 for kind in named_in_cliff | set(commit_lint.TYPES)}

        assert cliff == commit_lint.TYPES, (
            f"only in commit-lint: {sorted(set(commit_lint.TYPES) - named_in_cliff)}, "
            f"only in cliff.toml: {sorted(named_in_cliff - set(commit_lint.TYPES))}")

    def test_every_scope_renders_the_label_commit_lint_names(self):
        named_in_cliff = {m[1] for p in CLIFF["git"]["commit_preprocessors"]
                          if (m := re.search(r"\\\(([a-z0-9-]+)\\\)", p["pattern"]))}
        cliff = {scope: self._label_for(scope)
                 for scope in named_in_cliff | set(commit_lint.SCOPES)}

        assert cliff == commit_lint.SCOPES, (
            f"only in commit-lint: {sorted(set(commit_lint.SCOPES) - named_in_cliff)}, "
            f"only in cliff.toml: {sorted(named_in_cliff - set(commit_lint.SCOPES))}")
