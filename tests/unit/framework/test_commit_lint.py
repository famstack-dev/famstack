"""The guard that keeps commit subjects parseable as changelog entries.

The convention is only worth writing down if it is checked the same way
every time, by the hook before a commit exists and by CI before it
merges. These pin the rules, and one of them pins the rules against the
document that explains them, because that pair has already drifted once:
the table in dev.md was missing the scope seven commits already used.
"""

import importlib.machinery
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# The tool has no .py extension, matching tools/branch-status, so it is
# loaded by path rather than imported.
_spec = importlib.util.spec_from_loader(
    "commit_lint",
    importlib.machinery.SourceFileLoader(
        "commit_lint", str(REPO_ROOT / "tools" / "commit-lint")),
)
commit_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(commit_lint)


# ── The rules ────────────────────────────────────────────────────────────

class TestSubjectsThatPass:

    @pytest.mark.parametrize("subject", [
        "fix(archivist): stop filing your conversations, keep what you pin",
        "feat(memory): ask the family vault a question in plain words",
        "docs: write down how commits and PRs become the changelog",
        "docs(readme): stop announcing an old release as current",
        "feat(docs)!: move Paperless to 3.0.4",
        "chore(release): v0.3.0-beta.4",
        "fix(ai): install the local AI engine on current Homebrew (#72)",
        "fix(diary): front-page intro addresses the family directly",
        "feat(capture): file a link card when a page cannot be read (#96)",
        "feat(voice): treat a voice message like any other message (#85)",
    ])
    def test_real_subjects_from_the_history(self, subject):
        assert commit_lint.check(subject) == []

    @pytest.mark.parametrize("subject", [
        "Merge branch 'main' into feat/x",
        'Revert "feat(photos): something"',
        "fixup! fix(core): something",
    ])
    def test_git_writes_these_itself(self, subject):
        """Linting them teaches people to pass --no-verify, nothing else."""
        assert commit_lint.check(subject) == []


class TestSubjectsThatFail:

    def test_no_type_at_all(self):
        """The real one: the Paperless 3.0.4 move, the most important entry
        in its release, unclassifiable and so missing from the changelog."""
        problems = commit_lint.check("Move the docs stacklet to Paperless-ngx 3.0.4")
        assert any("does not parse" in p for p in problems)

    def test_a_type_nobody_agreed_on(self):
        problems = commit_lint.check("improvement(photos): make it faster")
        assert problems

    def test_a_scope_that_renders_nowhere(self):
        problems = commit_lint.check("fix(archivist-bot): file a document once")
        assert any("not a scope" in p for p in problems)

    def test_documentation_scopes_name_the_document(self):
        """`docs(dev-guide)` is right and `fix(dev-guide)` is not: only
        documentation is scoped by document."""
        assert commit_lint.check("docs(dev-guide): explain the release gate") == []
        assert commit_lint.check("fix(dev-guide): explain the release gate")

    def test_too_long_to_read_in_a_list(self):
        subject = "fix(core): " + "x" * 80
        assert any("limit is 72" in p for p in commit_lint.check(subject))

    def test_the_pr_number_github_appends_is_not_counted(self):
        """CI checks the PR title before a squash merge appends " (#106)",
        so counting the suffix failed subjects that passed CI."""
        subject = "test(archivist): pin the note in the demo-rig todo capture scenario"
        assert len(subject) <= commit_lint.SUBJECT_LIMIT
        assert commit_lint.check(f"{subject} (#106)") == []

    def test_written_as_a_title(self):
        problems = commit_lint.check("feat(photos): Stop the upload stalling")
        assert any("capital" in p for p in problems)

    def test_ends_with_a_full_stop(self):
        problems = commit_lint.check("feat(photos): stop the upload stalling.")
        assert any("full stop" in p for p in problems)


# ── The guard against the document drifting ──────────────────────────────

class TestRulesMatchTheDocumentation:
    """dev.md explains the rules to a person; this script enforces them.

    Two copies of one list is a drift waiting to happen, and it already
    happened: `stack` was used by seven commits before the table admitted
    it existed.
    """

    def _table_terms(self, heading: str) -> set[str]:
        """Backticked terms in the first column of the table under a heading."""
        text = (REPO_ROOT / "docs" / "agent" / "dev.md").read_text()
        section = text.split(heading, 1)[1]
        terms = set()
        for line in section.splitlines():
            if not line.startswith("|") or line.startswith("|---"):
                continue
            first = line.split("|")[1]
            found = re.findall(r"`([a-z0-9-]+)`", first)
            if not found:
                break  # past the table
            terms.update(found)
        return terms

    def test_every_documented_type_is_accepted(self):
        documented = self._table_terms("| Type | Section | Shown |")
        assert documented, "the types table moved or changed shape"
        assert documented == set(commit_lint.TYPES)

    def test_every_documented_scope_is_accepted(self):
        documented = self._table_terms("| Scope | Renders as |")
        assert documented, "the scopes table moved or changed shape"
        assert documented == set(commit_lint.SCOPES)


# ── The surfaces ─────────────────────────────────────────────────────────

class TestCommandLine:
    """Three ways in, because a hook has a file, CI has a range and a
    title, and the release gate has a range."""

    def _run(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "commit-lint"), *args],
            capture_output=True, text=True, cwd=cwd or REPO_ROOT,
        )

    @pytest.fixture
    def three_commits(self, tmp_path):
        """A repository of known depth.

        Reading this repo's own history fails wherever the checkout is
        shallow, which is every CI run: `HEAD~3` does not resolve at
        depth 1. A range test that depends on ambient history is a test
        that passes on a laptop and fails on a runner.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for name, subject in [("a", "feat(photos): back up over Wi-Fi"),
                              ("b", "fix(core): answer the first message"),
                              ("c", "docs: explain the release gate")]:
            (repo / name).write_text(name)
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com",
                            "-c", "user.name=T", "commit", "-qm", subject], check=True)
        return repo

    def test_a_good_title_exits_zero(self):
        assert self._run("--title", "fix(core): answer the first message").returncode == 0

    def test_a_bad_title_exits_one_and_says_why(self):
        result = self._run("--title", "made it better")
        assert result.returncode == 1
        assert "does not parse" in result.stderr

    def test_a_range_says_what_it_checked(self, three_commits):
        result = self._run("--range", "HEAD~2..HEAD", cwd=three_commits)

        assert result.returncode == 0, result.stderr
        assert "2 subject(s) checked" in result.stdout

    def test_an_empty_range_is_a_failure_not_a_pass(self, three_commits):
        """How this guard dies quietly: a shallow checkout leaves nothing
        to read and the step goes green having checked nothing."""
        result = self._run("--range", "HEAD..HEAD", cwd=three_commits)

        assert result.returncode == 1
        assert "no commits" in result.stderr

    def test_a_message_file(self, tmp_path):
        message = tmp_path / "COMMIT_EDITMSG"
        message.write_text("feat(photos): back up from the phone over Wi-Fi\n\nbody\n")
        assert self._run("--message", str(message)).returncode == 0

    def test_only_the_first_line_of_a_message_is_the_subject(self, tmp_path):
        """Bodies hold prose, footers and quoted output. None of it is
        the changelog line, so none of it is linted."""
        message = tmp_path / "COMMIT_EDITMSG"
        message.write_text(
            "fix(memory): keep the vault syncing after the Mac changes IP\n\n"
            "Upgrade: nothing to do.\n"
            "A body line that would never pass as a subject.\n"
        )
        assert self._run("--message", str(message)).returncode == 0


# ── No tool attribution ──────────────────────────────────────────────────

class TestNoToolAttribution:
    """Commits and pull requests carry no credit line for the tool that
    helped write them: no Co-Authored-By trailer, no "Generated with"
    footer. A description of the tool is fine; an advertisement is not."""

    @pytest.mark.parametrize("text", [
        "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>",
        "co-authored-by: Someone <someone@example.com>",
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        "Generated with Claude Code",
        "See https://claude.com/claude-code",
        "Signed off by noreply@anthropic.com",
    ])
    def test_credit_lines_are_found(self, text):
        assert commit_lint.attribution(f"Summary line.\n\n{text}\n") != []

    @pytest.mark.parametrize("text", [
        "a language server was wired up. In Claude Code the `LSP` tool is",
        "Claude Code reads CLAUDE.md, not AGENTS.md, so the repo rules were never",
        "The co-author of the ADR is named in the header.",
    ])
    def test_describing_the_tool_is_not_attribution(self, text):
        """Both sentences are from commits already on main."""
        assert commit_lint.attribution(text) == []


class TestAttributionOnTheCommandLine:

    def _run(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "commit-lint"), *args],
            capture_output=True, text=True, cwd=cwd or REPO_ROOT,
        )

    def test_a_trailer_in_the_message_body_fails_the_commit(self, tmp_path):
        message = tmp_path / "COMMIT_EDITMSG"
        message.write_text(
            "fix(core): answer the first message\n\nBody.\n\n"
            "Co-Authored-By: Claude <noreply@anthropic.com>\n")
        result = self._run("--message", str(message))
        assert result.returncode == 1
        assert "Co-Authored-By" in result.stderr

    def test_gits_comment_lines_are_not_the_message(self, tmp_path):
        """The editor template is stripped by git before the commit
        exists, so whatever it says is not part of what lands."""
        message = tmp_path / "COMMIT_EDITMSG"
        message.write_text(
            "fix(core): answer the first message\n\n"
            "# Generated with Claude Code, in a comment git will strip\n")
        assert self._run("--message", str(message)).returncode == 0

    def test_a_pull_request_body_with_a_footer_fails(self):
        result = self._run("--body", "## Summary\n- a change\n\n"
                           "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")
        assert result.returncode == 1
        assert "Generated with" in result.stderr

    def test_a_clean_pull_request_body_passes(self):
        assert self._run("--body", "## Summary\n- a change\n").returncode == 0

    def test_a_range_fails_on_a_trailer_in_any_body(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for name, message in [
            ("a", "feat(photos): back up over Wi-Fi"),
            ("b", "fix(core): answer the first message\n\n"
                  "Co-Authored-By: Claude <noreply@anthropic.com>"),
        ]:
            (repo / name).write_text(name)
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com",
                            "-c", "user.name=T", "commit", "-qm", message], check=True)
        result = self._run("--range", "HEAD~1..HEAD", cwd=repo)
        assert result.returncode == 1
        assert "Co-Authored-By" in result.stderr
