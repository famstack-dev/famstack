"""What the agent's vault tools promise: the commands they run actually work.

`test_agent_runtime_shims.py` proves the tools are registered with nanobot.
That is necessary and it is not enough. Both tools shipped registered and
non-functional, and every unit test stayed green, because nothing checked
the one thing that matters at runtime: the command line each tool builds
has to survive two gates it never sees.

  1. `stacklets/core/famstack-api.py` DOMAIN_ALLOW. The agent is an LLM, so
     it reaches the CLI through a curated allowlist. `memory person` was
     missing from it, so every call came back
     "error: 'memory person' is not allowed".

  2. The memory CLI's own argument parser. `memory_search` passed
     `--backend mem0`, a flag that has never existed, so every search
     returned a usage error and the agent looped retrying it.

Both gates live in other components, which is exactly why a stubbed
nanobot could not see either. So these tests drive the real tools, capture
the real argv, and hand it to the real allowlist matcher and the real
argparse parsers. Nothing here restates what the tools do; it asks the
components that judge them.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MEMORY_DIR = REPO_ROOT / "stacklets" / "memory"

TOOL_MODULES = ("memory_tool", "person_tool", "grep_tool", "sitecustomize")


# ── loading the real components under test ───────────────────────────

def _load_from_path(name: str, path: Path):
    """Import a module by file path.

    Neither component under test is importable the ordinary way:
    `famstack-api.py` has a hyphen in its name, and the client shim is an
    extensionless script. The explicit loader is what makes the second
    one work -- without it importlib cannot guess how to read a file with
    no `.py` suffix and hands back a spec with no loader at all.
    """
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    """The core API module that gates every command the agent runs."""
    return _load_from_path("famstack_api",
                           REPO_ROOT / "stacklets" / "core" / "famstack-api.py")


@pytest.fixture(scope="module")
def memory_cli():
    """The real memory CLI modules, for their real argparse parsers."""
    if str(MEMORY_DIR) not in sys.path:
        sys.path.insert(0, str(MEMORY_DIR))
    return {
        name: _load_from_path(f"memory_cli_{name}",
                              MEMORY_DIR / "cli" / f"{name}.py")
        for name in ("search", "person")
    }


@pytest.fixture
def vault_tools(monkeypatch, nanobot_stub):
    """The agent's tool classes, imported against a stub nanobot."""
    for name, module in nanobot_stub().items():
        monkeypatch.setitem(sys.modules, name, module)
    for name in TOOL_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)

    tools = {
        "memory_search": importlib.import_module("memory_tool").MemorySearchTool,
        "memory_person": importlib.import_module("person_tool").MemoryPersonTool,
    }
    yield tools
    for name in TOOL_MODULES:
        sys.modules.pop(name, None)


def argv_of(tool_cls, **kwargs) -> list[str]:
    """Run a tool and return the command line it tried to execute.

    Captures at `create_subprocess_exec` so the argv asserted on is the
    argv the tool would really have run, not a restatement of it.
    """
    captured: list[str] = []

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def _fake_exec(*args, **_kwargs):
        captured.extend(args)
        return _Proc()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    try:
        asyncio.run(tool_cls().execute(**kwargs))
    finally:
        monkey.undo()
    return captured


# ── gate 1: the core API allowlist ───────────────────────────────────

@pytest.mark.parametrize("tool_name,kwargs", [
    ("memory_search", {"query": "Homer"}),
    ("memory_person", {"name": "homer"}),
])
def test_the_command_a_tool_runs_is_allowed_by_the_api(api, vault_tools,
                                                       tool_name, kwargs):
    """A tool the agent cannot actually invoke is worse than a missing one.

    Asserted with DOMAIN_ALLOW's own matching rule rather than a copy of
    it, so tightening that rule fails here instead of in production.
    """
    argv = argv_of(vault_tools[tool_name], **kwargs)

    assert argv[0] == "stack", "tools invoke the CLI through the client shim"
    args = argv[1:]
    permitted = any(args[:len(p)] == p for p in api.DOMAIN_ALLOW)

    assert permitted, (
        f"{tool_name} runs 'stack {' '.join(args[:2])}', which DOMAIN_ALLOW "
        f"rejects. Allowed: {[' '.join(p) for p in api.DOMAIN_ALLOW]}"
    )


def test_the_allowlist_stays_read_only(api):
    """The agent must never reach a command that changes the stack.

    DOMAIN_ALLOW is a security boundary, not a convenience list. Adding
    memory person to it is fine; adding memory sync or a lifecycle verb
    would hand an LLM write access to the instance.
    """
    forbidden = {"up", "down", "restart", "destroy", "setup", "sync",
                 "pull", "wiki", "ontology"}
    for path in api.DOMAIN_ALLOW:
        assert not forbidden & set(path), f"{path} reaches a mutating command"


# ── gate 2: the memory CLI's real parser ─────────────────────────────

@pytest.mark.parametrize("tool_name,command,kwargs", [
    ("memory_search", "search",
     {"query": "Homer", "limit": 10, "person": "homer", "tag": "Insurance",
      "scope": "family"}),
    ("memory_person", "person", {"name": "homer"}),
])
def test_the_flags_a_tool_sends_are_flags_the_cli_accepts(
        memory_cli, vault_tools, tool_name, command, kwargs):
    """Every option the tool passes has to parse, including the optional ones.

    The optional arguments matter most: a flag only sent when the model
    fills in that parameter is one a hand test almost never exercises,
    which is how `--backend mem0` survived. Parsed by the CLI's own
    parser, so renaming a flag breaks this test rather than the agent.
    """
    argv = argv_of(vault_tools[tool_name], **kwargs)
    assert argv[1:3] == ["memory", command]

    parser = memory_cli[command]._parser()
    try:
        parser.parse_args(argv[3:])
    except SystemExit as exit_:
        pytest.fail(
            f"{tool_name} builds `{' '.join(argv)}`, which "
            f"`stack memory {command}` rejects (argparse exit {exit_.code})"
        )


def test_an_unknown_flag_would_be_caught(memory_cli):
    """Guards the guard: prove the parser check can actually fail.

    Without this, a parser that silently swallowed unknown options would
    make the test above pass against the very bug it exists to catch.
    """
    parser = memory_cli["search"]._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["Homer", "--backend", "mem0"])


def test_search_sends_no_backend_flag(vault_tools):
    """The specific regression: `--backend mem0` was never a real option.

    Pinned by name because the failure it caused was silent from the
    agent's side. Every search returned a usage error, the model read it
    as an empty result, and retried with different arguments instead of
    surfacing anything.
    """
    argv = argv_of(vault_tools["memory_search"], query="Homer")

    assert "--backend" not in argv


# ── gate 3: the transport between the tool and the CLI ───────────────
#
# The two gates above both read argv straight out of the tool. Nothing
# the agent runs reaches the CLI that way: it goes over a socket as one
# line of text and is rebuilt on the other side. That hop was untested,
# and it was losing every argument boundary.


@pytest.fixture(scope="module")
def shim():
    """The container-side `stack` shim, the agent's only route to the CLI."""
    return _load_from_path("stack_client_shim",
                           REPO_ROOT / "stacklets" / "agent" / "client" / "stack")


class TestArgvSurvivesTheWire:
    """The shim encodes argv as one line; `handle_plaintext` rebuilds it
    with `shlex.split`. If the two disagree about quoting, the CLI runs a
    different command than the tool asked for -- and the tool cannot tell,
    because it never sees the argv that actually ran.
    """

    def test_a_multi_word_argument_arrives_as_one_argument(self, shim):
        """The regression, in one line.

        `memory search "school run"` arrived as four words. argparse
        rejected `run` as a stray positional, so the agent's main
        retrieval tool failed on every query longer than one word --
        which is nearly every real question a family asks.
        """
        import shlex
        argv = ["memory", "search", "school run", "--limit", "10"]

        assert shlex.split(shim.wire_line(argv)) == argv

    @pytest.mark.parametrize("argument", [
        "school run",
        "when does the car insurance renew?",
        "Marge's dentist",
        'he said "no"',
        "back\\slash",
        "a  double  space",
        "--not-a-flag",
        "",
    ])
    def test_anything_a_family_might_type_survives(self, shim, argument):
        """The query is free text a person typed, so the encoding has to
        hold for apostrophes, quotes, question marks and empty strings --
        not just for the tidy identifiers a hand test reaches for."""
        import shlex
        argv = ["memory", "search", argument]

        assert shlex.split(shim.wire_line(argv)) == argv

    def test_the_real_tool_argv_survives_it(self, api, shim, vault_tools):
        """End to end: what the tool builds is what the CLI would run.

        Uses a multi-word query on purpose. Every fixture in this file
        used to search for "Homer", which is exactly why a bug that only
        bites on a space lived through all of them.
        """
        import shlex
        argv = argv_of(vault_tools["memory_search"],
                       query="when is the school run", limit=10)

        rebuilt = shlex.split(shim.wire_line(argv[1:]))

        assert rebuilt == argv[1:]
        assert any(rebuilt[:len(p)] == p for p in api.DOMAIN_ALLOW)


class TestFailureIsVisibleToTheCaller:
    """A command that failed must not look like one that worked.

    This is what turned a one-argument bug into a hang: the shim always
    exited 0, so `memory_search` read argparse's usage text as search
    results, returned it to the model as an answer, and the model called
    the identical tool again. It was still doing that five minutes later.
    """

    def test_a_failed_command_reports_its_status(self, api, shim):
        reply = api._with_exit("stack memory search: error: unrecognized\n", 2)
        text, code = shim.split_exit_code(reply)

        assert code == 2
        assert "unrecognized" in text
        assert api.EXIT_MARKER not in text, "protocol noise must not reach the model"

    def test_a_successful_command_is_untouched(self, api, shim):
        """Success has to stay byte-identical, or every reply grows a line."""
        reply = api._with_exit("#1  vault/homer/about.md  score=0.82\n", 0)

        assert reply == "#1  vault/homer/about.md  score=0.82\n"
        assert shim.split_exit_code(reply) == (reply, 0)

    def test_a_refusal_is_a_failure_too(self, api, shim):
        """A denied command used to come back as ordinary output. The
        model cannot act on a refusal it cannot recognise as one."""
        _, code = shim.split_exit_code(api.handle_plaintext("up memory"))

        assert code != 0

    def test_output_that_looks_like_the_marker_is_not_mistaken_for_one(self, shim):
        # A vault note quoting the marker must not silently set an exit code.
        body = "the log said stack-exit: 3 and then stopped\n"

        assert shim.split_exit_code(body) == (body, 0)


# ── the vault root a profile actually lives in ───────────────────────

def test_person_reads_generated_profiles(memory_cli, tmp_path):
    """`about.md` pages are generated, and generation writes to the brain.

    memory is source-only. The installer purges generated pages from it
    ("purged 1 generated source page(s)"), so a person page can only ever
    be found in the brain projection. Reading the source vault alone
    meant the command returned "no profile" for every family member who
    had one.
    """
    brain = tmp_path / "memory" / "brain"
    (brain / "homer").mkdir(parents=True)
    (brain / "homer" / "about.md").write_text(
        "---\ntitle: Homer\nslug: homer\ncanonical: Homer\n---\n\n"
        "# Homer\n\nSafety Inspector, Sector 7-G.\n", encoding="utf-8")

    result = memory_cli["person"].run(
        ["homer", "--no-refresh"], None, {"data_dir": str(tmp_path)})

    assert result.get("path") == "homer/about.md", (
        f"expected the generated profile, got {result}"
    )


def test_a_hand_written_source_page_wins(memory_cli, tmp_path, capsys):
    """Source beats projection when a household curates a page by hand.

    The brain is rebuildable output; memory is what a family actually
    wrote. If both hold a page for the same person, the curated one is
    the answer. Both files sit at the same relative path, so the printed
    body is the only thing that can prove which one was read.
    """
    vault = tmp_path / "memory" / "vault"
    brain = tmp_path / "memory" / "brain"
    for root, marker in ((vault, "hand written"), (brain, "generated")):
        (root / "homer").mkdir(parents=True)
        (root / "homer" / "about.md").write_text(
            f"---\ntitle: Homer\nslug: homer\ncanonical: Homer\n---\n\n"
            f"# Homer\n\n{marker}\n", encoding="utf-8")

    result = memory_cli["person"].run(
        ["homer", "--no-refresh"], None, {"data_dir": str(tmp_path)})

    assert result.get("ok") is True
    assert "hand written" in capsys.readouterr().out


def test_missing_person_still_reports_cleanly(memory_cli, tmp_path):
    """No profile anywhere is a clean error, not a crash."""
    (tmp_path / "memory" / "brain").mkdir(parents=True)

    result = memory_cli["person"].run(
        ["nobody", "--no-refresh"], None, {"data_dir": str(tmp_path)})

    assert "error" in result


# ── the tree the agent's file tools actually see ─────────────────────

def test_the_agent_mounts_the_tree_that_holds_profiles():
    """Stacky's `vault/` mount must contain the pages it promises.

    The mount exists so nanobot's workspace-scoped file tools can read
    person and topic pages, and the model prefers those tools over
    anything else: given a `vault/` directory it will read and grep it
    rather than call `memory_person`. So whatever is mounted there is,
    in practice, the agent's whole picture of the family.

    Generated pages live only in the brain projection. Mounting the
    memory source clone therefore handed the agent a tree that could
    never hold a profile, and it reported "There is no
    vault/homer/about.md" for a member who had one. Wrong beats missing:
    a confident denial is worse than no answer.

    Compared against the memory stacklet's own declaration rather than a
    hardcoded path, so if memory ever moves the projection this fails
    instead of silently drifting.
    """
    import tomllib

    agent = tomllib.loads(
        (REPO_ROOT / "stacklets" / "agent" / "stacklet.toml").read_text())
    memory = tomllib.loads(
        (REPO_ROOT / "stacklets" / "memory" / "stacklet.toml").read_text())

    mounted = agent["env"]["defaults"]["MEMORY_VAULT_DIR"]
    brain = memory["env"]["defaults"]["BRAIN_REPO_DIR"]
    source = memory["env"]["defaults"]["MEMORY_VAULT_DIR"]

    assert mounted == brain, (
        f"agent mounts {mounted!r}; generated profiles live in {brain!r}"
    )
    assert mounted != source, "the source clone never holds generated pages"
