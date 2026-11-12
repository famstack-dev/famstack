"""`tools/reach-probe` as a maintainer runs it, against a stand-in `docker`.

The tool asks Docker which containers of the stack run, then asks each
port of the stack from inside every container's network. Here `docker` is
a stand-in first on `PATH`: it lists two containers (`stack-agent` and
`stack-code`) with their ports and mounts, and runs the helper's probe on
this Mac, so the probe connects to real sockets on localhost:

    port       what listens                          the probe sees
    web        HTTP, 401 for the Mac's Host, 200     200 / 401
               otherwise, with a body and a cookie
    ssh        TCP that greets like SSH              open / open
    closed     nothing                               closed / closed

The stand-in records every docker command, so a test can check that the
tool only reads (`ps`, `inspect`) and starts nothing but `--rm` helpers.
The tool runs as a process, as a maintainer runs it.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "tools" / "reach-probe"
FAKE_HOST = "127.0.0.1:42002"
SECRET = "s3cret-session-token"

# The stand-in `docker`: reads its containers from `containers.json` next
# to it and appends each command line to `commands.jsonl`.
STAND_IN = r'''
import json, subprocess, sys
from pathlib import Path

here = Path(__file__).parent
args = sys.argv[1:]
with open(here / "commands.jsonl", "a") as log:
    log.write(json.dumps(args) + "\n")
containers = json.loads((here / "containers.json").read_text())
if args[0] == "ps":
    print("\n".join(entry["Name"].lstrip("/") for entry in containers))
elif args[0] == "inspect":
    print(json.dumps([entry for entry in containers if entry["Name"].lstrip("/") in args[1:]]))
elif args[0] == "run":
    probe = args[args.index("python") + 1:]
    sys.exit(subprocess.run([sys.executable, *probe]).returncode)
else:
    sys.exit(125)
'''


# ── The behaviour ────────────────────────────────────────────────────────

def test_each_container_reports_every_port_of_the_stack(stack):
    """Given two running containers publishing three ports between them,
    when the maintainer runs the probe with --json, then each container
    has a row for every port: the status code plain and with the Mac's
    Host header, "open" for a port that answers without HTTP and "closed"
    for one that does not answer. A difference between the two columns is
    what shows a check that trusts the Host header."""
    result = stack.run("--json")

    assert result.returncode == 0, result.stderr
    found = json.loads(result.stdout)
    assert found["fake_host"] == FAKE_HOST
    expected = [
        {"port": stack.web, "answers": 200, "with_fake_host": 401},
        {"port": stack.ssh, "answers": "open", "with_fake_host": "open"},
        {"port": stack.closed, "answers": "closed", "with_fake_host": "closed"},
    ]
    expected.sort(key=lambda row: row["port"])
    assert [(c["name"], c["ports"]) for c in found["containers"]] == [
        ("stack-agent", expected), ("stack-code", expected)]


def test_mounts_that_expose_the_stack_folder_are_listed(stack):
    """Given one container that mounts a file inside `.stack/` and a data
    folder, and one that mounts the whole checkout, when the probe runs,
    then it lists the secrets file and the checkout, and not the data
    folder: a mount of a folder that holds `.stack/` shows the secrets
    as well."""
    found = json.loads(stack.run("--json").stdout)

    mounts = {c["name"]: [(m["source"], m["destination"]) for m in c["mounts"]]
              for c in found["containers"]}
    assert mounts == {
        "stack-agent": [(str(stack.instance / ".stack" / "secrets.toml"), "/run/secrets.toml")],
        "stack-code": [(str(stack.instance), "/repo")],
    }


def test_the_table_carries_the_same_facts(stack):
    """Given the same stack, when the probe runs without --json, then it
    ends with exit 0 and prints a row per container and port with both
    answers, and the mounts' paths."""
    result = stack.run()

    assert result.returncode == 0, result.stderr
    rows = [line.split() for line in result.stdout.splitlines()]
    assert ["stack-agent", str(stack.web), "200", "401"] in rows
    assert ["stack-code", str(stack.closed), "closed", "closed"] in rows
    assert str(stack.instance / ".stack" / "secrets.toml") in result.stdout


def test_it_prints_no_part_of_an_answer_but_its_status(stack):
    """Given a port whose answer carries a token in its body and a cookie,
    when the probe runs, then neither appears in what it prints: only the
    status code leaves the helper."""
    for flag in ([], ["--json"]):
        result = stack.run(*flag)
        assert SECRET not in result.stdout + result.stderr


def test_it_only_reads_and_starts_nothing_but_removed_helpers(stack):
    """Given the stack, when the probe runs, then the only docker commands
    are `ps`, `inspect` and one `run --rm` per container in that
    container's network: no exec, no restart, no write."""
    stack.run("--json")

    kinds = [command[0] for command in stack.commands()]
    assert set(kinds) == {"ps", "inspect", "run"}
    runs = [command[:4] for command in stack.commands() if command[0] == "run"]
    assert runs == [["run", "--rm", "--network", "container:stack-agent"],
                    ["run", "--rm", "--network", "container:stack-code"]]


def test_no_running_container_is_a_plain_answer(stack):
    """Given no running container of the stack, when the probe runs, then
    it says so and ends with exit 0; --json gives an empty list."""
    stack.containers([])

    result = stack.run()
    assert result.returncode == 0, result.stderr
    assert "No container of the stack runs" in result.stdout
    assert json.loads(stack.run("--json").stdout)["containers"] == []


# ── The stand-in stack ───────────────────────────────────────────────────

class Stack:
    """Two containers behind the stand-in `docker`, and the ports they publish.

        stack.run("--json")      # the tool as a process, its docker the stand-in
        stack.commands()         # [["ps", ...], ["inspect", ...], ["run", "--rm", ...]]
    """

    def __init__(self, root: Path, web: int, ssh: int, closed: int):
        self.root, self.web, self.ssh, self.closed = root, web, ssh, closed
        self.instance = root / "famstack"
        (self.instance / ".stack").mkdir(parents=True)
        self.bin = root / "bin"
        self.bin.mkdir()
        (self.bin / "stand_in.py").write_text(STAND_IN)
        docker = self.bin / "docker"
        docker.write_text(f"#!/bin/sh\nexec '{sys.executable}' -I '{self.bin / 'stand_in.py'}' \"$@\"\n")
        docker.chmod(0o755)
        self.containers([
            _inspected("stack-agent", {"8002/tcp": web, "22/tcp": ssh}, [
                (self.instance / ".stack" / "secrets.toml", "/run/secrets.toml"),
                (root / "data" / "agent", "/data")]),
            _inspected("stack-code", {"3000/tcp": closed}, [(self.instance, "/repo")]),
        ])

    def containers(self, entries: list[dict]) -> None:
        """Make `entries` (`docker inspect` entries) the running containers."""
        (self.bin / "containers.json").write_text(json.dumps(entries))

    def commands(self) -> list[list[str]]:
        """Every docker command the tool ran so far, as argument lists."""
        lines = (self.bin / "commands.jsonl").read_text().splitlines()
        return [json.loads(line) for line in lines]

    def run(self, *args: str) -> subprocess.CompletedProcess:
        """Run the tool with `args`, on this instance, probing localhost."""
        env = {"PATH": f"{self.bin}:/usr/bin:/bin"}
        return subprocess.run(
            [sys.executable, str(TOOL), "--instance", str(self.instance),
             "--target", "127.0.0.1", *args],
            capture_output=True, text=True, env=env, timeout=60)


@pytest.fixture
def stack(tmp_path):
    """The stand-in stack, with its three ports listening (or not) until the test ends."""
    web = ThreadingHTTPServer(("127.0.0.1", 0), _Web)
    ssh = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()
    threads = [threading.Thread(target=web.serve_forever, daemon=True),
               threading.Thread(target=_greeter, args=(ssh, stop), daemon=True)]
    for thread in threads:
        thread.start()
    try:
        yield Stack(tmp_path, web.server_port, ssh.getsockname()[1], _free_port())
    finally:
        web.shutdown()
        web.server_close()
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
        ssh.close()


# ── What listens on the ports ────────────────────────────────────────────

class _Web(BaseHTTPRequestHandler):
    """401 for the Mac's own Host header, 200 otherwise; both with a token."""

    def do_GET(self):
        self.send_response(401 if self.headers.get("Host") == FAKE_HOST else 200)
        self.send_header("Set-Cookie", f"session={SECRET}")
        self.end_headers()
        self.wfile.write(SECRET.encode())

    def log_message(self, *args):
        pass


def _greeter(server: socket.socket, stop: threading.Event) -> None:
    """Greet every connection like an SSH server, until `stop` is set."""
    server.settimeout(0.1)
    while not stop.is_set():
        try:
            conn, _ = server.accept()
        except OSError:
            continue
        conn.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")
        conn.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _inspected(name: str, ports: dict[str, int], mounts: list[tuple[Path, str]]) -> dict:
    """A `docker inspect` entry with the fields the tool reads."""
    return {
        "Name": f"/{name}",
        "NetworkSettings": {"Ports": {inner: [{"HostIp": "0.0.0.0", "HostPort": str(port)}]
                                      for inner, port in ports.items()}},
        "Mounts": [{"Type": "bind", "Source": str(source), "Destination": destination}
                   for source, destination in mounts],
    }
