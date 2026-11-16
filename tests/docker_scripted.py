"""A `docker` whose every answer a test writes, for code that parses what Docker prints.

`docker_with_projects` answers as a Mac does when stacklets run. A test of
how `stack.docker` reads one specific output, or reacts to one specific
exit code, needs the opposite: it says what `docker <words>` prints and
exits with, and then runs the real function against it.

    def test_a_failed_listing_is_empty(docker_scripted):
        docker_scripted.answers("compose ls", code=1)
        assert running_project_ids() == set()

    def test_the_first_build_pulls(docker_scripted):
        docker_scripted.answers("build --pull", code=1, times=1)
        docker_scripted.answers("build")
        compose_build("compose.yml")
        assert docker_scripted.commands()[-1][-1] == "build"

A rule names words, and matches a command that holds them in that order,
with other arguments between them (`compose ls` matches
`compose -f x ls --format json`). The rule with the most words wins; a
tie goes to the rule written first that still has answers left, so
rules with `times` queue in the order they were written. A rule with `times=N` answers N
commands and then stops matching. A command no rule matches exits 125 and
says `docker_scripted answers no: ...`, so an unplanned call fails loudly.

A leading `--context <name>` is dropped before matching, because the stack
pins every command to the runtime it chose and that pin outlives the test
that chose it. `commands()` lists the arguments without it and `contexts()`
lists the pin each command carried. The script is plain Python that reads
`rules.json` on every call, so a test may change its answers between two
commands, and it writes each command to `commands.log`.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

# The process `docker` starts. It runs under `-I`, which keeps the test
# session out of its imports, so it uses the standard library only.
_SCRIPT = '''\
import json
import sys
from pathlib import Path

folder = Path(sys.argv[1])
argv = sys.argv[2:]
context = None
if argv[:1] == ["--context"]:
    context, argv = argv[1], argv[2:]
with (folder / "commands.log").open("a") as log:
    log.write(json.dumps({"argv": argv, "context": context}) + "\\n")


def holds(words):
    at = 0
    for word in words:
        if word not in argv[at:]:
            return False
        at = argv.index(word, at) + 1
    return True


rules_file = folder / "rules.json"
rules = json.loads(rules_file.read_text())
best = None
for index, rule in enumerate(rules):
    if rule["times"] == 0 or not holds(rule["words"]):
        continue
    if best is None or len(rule["words"]) > len(rules[best]["words"]):
        best = index
if best is None:
    print("docker_scripted answers no: docker " + " ".join(argv), file=sys.stderr)
    sys.exit(125)
rule = rules[best]
if rule["times"] is not None:
    rule["times"] -= 1
    rules_file.write_text(json.dumps(rules))
sys.stdout.write(rule["stdout"])
sys.stderr.write(rule["stderr"])
sys.exit(rule["code"])
'''

__all__ = ["Docker", "docker_scripted"]


@dataclass
class Docker:
    """The scripted `docker` of one test, in `folder`.

        docker.answers("ps", stdout="stack-ai-main\\tabc123\\n")
        docker.commands()               # [["ps", "--format", ...]]
    """

    folder: Path

    def answers(self, words: str, *, stdout: str = "", stderr: str = "",
                code: int = 0, times: int | None = None) -> None:
        """Make a command that holds `words` print `stdout` and `stderr` and exit `code`."""
        rules = json.loads((self.folder / "rules.json").read_text())
        rules.append({"words": words.split(), "stdout": stdout, "stderr": stderr,
                      "code": code, "times": times})
        (self.folder / "rules.json").write_text(json.dumps(rules))

    def commands(self) -> list[list[str]]:
        """Every command Docker got, oldest first, without the word `docker`."""
        return [entry["argv"] for entry in self._record()]

    def contexts(self) -> list[str | None]:
        """The `--context` each command was pinned to, `None` for none, oldest first."""
        return [entry["context"] for entry in self._record()]

    def _record(self) -> list[dict]:
        log = self.folder / "commands.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def docker_scripted(monkeypatch, tmp_path) -> Docker:
    """A `docker` first on `PATH` that answers only what the test scripts."""
    folder = tmp_path / "docker_scripted"
    folder.mkdir()
    (folder / "rules.json").write_text("[]")
    (folder / "answer.py").write_text(_SCRIPT)
    script = folder / "docker"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{folder / "answer.py"}" "{folder}" "$@"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder), prepend=os.pathsep)
    return Docker(folder)
