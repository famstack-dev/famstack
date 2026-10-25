"""The unit lane's rules, for every directory of unit tests.

The unit lane is `tests/unit/` and each module's own `tests/<module>/unit/`.
Pytest applies a conftest's hooks and autouse fixtures only to the tests
below it, so each of those directories takes the rules with one import in
its conftest:

    from tests.lanes.unit import (  # noqa: F401
        diary_back_in_english, httpserver_listen_address, pytest_runtest_call,
        pytest_runtest_setup, pytest_runtest_teardown, stacklet_packages_as_they_were)

The rules: every stacklet is importable as its package before the first
test (`import_stacklets`); a test leaves the stacklet packages and the
diary's language as it found them; pytest-httpserver's stand-ins listen on
an address; and the lane stays off the network.

Every unit test runs with the socket layer guarded: a
connection to anything but this machine, or a name lookup of any host but
localhost, fails the test on the spot and says how to stand the service in.
Localhost stays open because the stand-ins listen there. Unix sockets stay
open because they never leave the machine. Connecting a UDP socket stays
open too: it sends nothing, and `Stack._lan_ip` uses it to ask the kernel
which interface it would route through.
A name under `.invalid` gets the answer every resolver must give it
(RFC 6761): it does not exist. That needs no network, and lets a test
check what happens to a name that does not resolve.

Why: a unit test once sent a real write request to the test rig's Forgejo on
a LAN address of the Mac running the suite. `./stack`'s plugin loader loads a
fresh copy of a stacklet's modules for each command, so a stand-in patched
onto one copy never reached the next, and the command went where its config
pointed. Only the rig's 401 kept it from writing. A unit test must never
reach a real instance, and nothing short of the socket layer can promise it.

The rig also listens on this Mac, so a loopback connection to a port a
shipped stacklet publishes is refused as well, unless this process is the
one listening there. Stand-ins take a random free port, and on Linux that
can be any port, a published one included; the rig is always another
process. The published ports are read from the stacklets once per session:
`port` and `[ports]` in each manifest, which the framework renders into
`{id}_url` and `{id}_{name}_url`, and the host side of each compose port
binding, which is how core and code publish the rest.

The guard is installed by the `pytest_runtest_*` hooks below rather than by a
fixture, so it also covers session fixtures a unit test is the first to ask
for. Pytest calls these hooks only for items below a conftest that imports
them, which keeps the integration, e2e and model lanes, which reach real
services on purpose, unguarded. The refusal is a test failure (`pytest.fail`),
not an `OSError`, so a command that turns connection errors into an answer
cannot hide it; and a refusal caught all the same, by a bare `except` or in
another thread, still fails the test when it returns.

It guards this process only. A test that starts `python -m stack` as a
subprocess is not covered.
"""

from __future__ import annotations

import functools
import ipaddress
import re
import socket
import sys
import tomllib
from pathlib import Path

import pytest

from stack.stacklet_code import import_stacklets

# Every stacklet is the package `stacklets.<id>` (`stack.stacklet_code`), as
# the framework loads it, so any unit test imports a stacklet's module by
# that name: `from stacklets.ai.hooks import on_start`. Registered before
# the first test, so a test that forgets the modules it loaded
# (`as_stack_runs_it`) puts the packages back as they were.
import_stacklets([Path(__file__).resolve().parents[2] / "stacklets"])


def _stacklet_modules() -> dict[str, object]:
    return {n: m for n, m in sys.modules.items() if n == "stacklets" or n.startswith("stacklets.")}


@pytest.fixture(autouse=True)
def stacklet_packages_as_they_were():
    """Leave the stacklet packages as the test found them.

    A test that runs a stacklet of its own under a shipped id (a `memory`
    written into tmp_path) makes `stacklets.memory` that tree's, as a
    process that loads one stacklet from two trees does. Afterwards the
    repo's package and the modules loaded from it are back, so a later
    test imports the repo's `stacklets.memory` and not a directory that
    is gone. Modules a test imported from the repo's tree stay: a second
    copy of one would be a second `Put` class.
    """
    before = _stacklet_modules()
    yield
    replaced = [name for name, module in before.items() if name.count(".") == 1
                and sys.modules.get(name) is not module]
    for package in replaced:
        for name in [n for n in sys.modules if n == package or n.startswith(f"{package}.")]:
            del sys.modules[name]
        sys.modules.update({n: m for n, m in before.items()
                            if n == package or n.startswith(f"{package}.")})
        setattr(before["stacklets"], package.split(".")[1], before[package])
    if sys.modules.get("stacklets") is not before["stacklets"]:
        sys.modules["stacklets"] = before["stacklets"]

HOW_TO_STAND_IN = (
    "Unit tests stay off the network and off the ports a running instance "
    "listens on. Stand the service in on localhost, with pytest-httpserver's "
    "`httpserver` fixture or an in-process HTTP server as in "
    "tests/unit/stacklets/test_memory_card_links.py."
)

STACKLETS = Path(__file__).resolve().parents[2] / "stacklets"

# `${CODE_SSH_PORT:-222}` in a compose binding: the default is the port.
_DEFAULTED_VAR = re.compile(r"\$\{[^}:]+:-([^}]*)\}")

_INET = (socket.AF_INET, socket.AF_INET6)


def _address(host):
    """The IP address `host` spells, or None when it is a name."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except (AttributeError, ValueError):
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _is_this_machine(host) -> bool:
    """True for localhost, a loopback address, or the unspecified address
    (which a connection resolves to this machine)."""
    if host in (None, "", b"", "localhost", b"localhost"):
        return True
    address = _address(host)
    return address is not None and (address.is_loopback or address.is_unspecified)


def _is_reserved_invalid(host) -> bool:
    """True for a name under `.invalid`, which RFC 6761 reserves never to resolve."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    return isinstance(host, str) and host.rstrip(".").lower().endswith(".invalid")


@functools.cache
def published_ports() -> dict[int, str]:
    """Every host port a shipped stacklet publishes, mapped to its id."""
    import yaml

    ports: dict[int, str] = {}
    for manifest_path in sorted(STACKLETS.glob("*/stacklet.toml")):
        stacklet = manifest_path.parent.name
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        for port in [manifest.get("port"), *manifest.get("ports", {}).values()]:
            if isinstance(port, int):
                ports[port] = stacklet
        compose_path = manifest_path.parent / "docker-compose.yml"
        if compose_path.exists():
            compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
            for service in (compose.get("services") or {}).values():
                for binding in service.get("ports") or []:
                    if (port := _host_port(binding)) is not None:
                        ports[port] = stacklet
    return ports


def _host_port(binding) -> int | None:
    """The host port of a compose binding such as
    `"${PORT_BIND_IP:-127.0.0.1}:42000:8001"`, or None when it has none."""
    if isinstance(binding, dict):
        binding = binding.get("published", "")
    parts = _DEFAULTED_VAR.sub(r"\1", str(binding)).split("/", 1)[0].split(":")
    try:
        return int(parts[-2]) if len(parts) >= 2 else None
    except ValueError:
        return None


class NetworkGuard:
    """Refuse every socket operation that would leave this machine.

    `install()` replaces the socket functions that open a connection or look
    a name up, `uninstall()` puts the originals back, and `refusals` lists
    what was refused since the last install. `own_ports` are the ports this
    process listens on, which its stand-ins may use whatever they are.

        guard = NetworkGuard()
        guard.install()
        socket.create_connection(("192.0.2.1", 80))  # fails the test
        guard.uninstall()
    """

    def __init__(self) -> None:
        self.refusals: list[str] = []
        self.own_ports: set[int] = set()
        self._originals: list[tuple[object, str, object]] = []

    def install(self) -> None:
        self.refusals = []
        self._patch(socket.socket, "connect", self._guarded_connect)
        self._patch(socket.socket, "connect_ex", self._guarded_connect)
        self._patch(socket.socket, "listen", self._recording_listen)
        self._patch(socket, "getaddrinfo", self._guarded_lookup)
        self._patch(socket, "gethostbyname", self._guarded_lookup)
        self._patch(socket, "gethostbyname_ex", self._guarded_lookup)

    def uninstall(self) -> None:
        while self._originals:
            owner, name, own = self._originals.pop()
            if own is None:
                delattr(owner, name)  # it was inherited, as `socket.socket.connect` is
            else:
                setattr(owner, name, own)

    def _patch(self, owner, name: str, guarded) -> None:
        self._originals.append((owner, name, vars(owner).get(name)))
        setattr(owner, name, guarded(getattr(owner, name)))

    def _refuse(self, reached: str) -> None:
        message = f"A unit test tried to reach {reached}. {HOW_TO_STAND_IN}"
        self.refusals.append(message)
        pytest.fail(message)

    def _guarded_connect(self, original):
        def connect(sock, address):
            if sock.family in _INET and sock.type == socket.SOCK_STREAM:
                host, port = address[0], address[1]
                if not _is_this_machine(host):
                    self._refuse(f"{host}:{port}")
                elif port in published_ports() and port not in self.own_ports:
                    self._refuse(f"{host}:{port}, where a running instance's "
                                 f"{published_ports()[port]} stacklet listens")
            return original(sock, address)

        return connect

    def _recording_listen(self, original):
        def listen(sock, *args):
            result = original(sock, *args)
            if sock.family in _INET:
                self.own_ports.add(sock.getsockname()[1])
            return result

        return listen

    def _guarded_lookup(self, original):
        # An address needs no lookup, and connect() judges where it points.
        def lookup(host, *args, **kwargs):
            if _is_reserved_invalid(host):
                raise socket.gaierror(socket.EAI_NONAME, f"{host} does not exist (RFC 6761)")
            if not _is_this_machine(host) and _address(host) is None:
                port = args[0] if args else kwargs.get("port")
                self._refuse(f"{host}:{port}" if port is not None else str(host))
            return original(host, *args, **kwargs)

        return lookup


_guard = NetworkGuard()


# The memory diary's module, by its file: tests load it under more than one name.
_DIARY = f"{Path('stacklets', 'memory', 'bot', 'diary.py')}"


@pytest.fixture(autouse=True)
def diary_back_in_english():
    """Give the memory diary its English back after each test.

    The diary's commands select the diary's language for their process
    (`diary.configure_language`, from `LANGUAGE`), which is right for a
    command that runs in a process of its own. A test runs them in the
    test process, so one that compiles a German diary left German for the
    next test of the same worker, and the diary's tests read English, the
    baseline they are written in.
    """
    yield
    for module in list(sys.modules.values()):
        if str(getattr(module, "__file__", "") or "").endswith(_DIARY):
            module.configure_language("en")


@pytest.fixture(scope="session")
def httpserver_listen_address():
    """pytest-httpserver's stand-ins listen on 127.0.0.1, and their URLs say so.

    By default they bind `localhost`, which the server takes as 127.0.0.1,
    while a client (urllib, git's curl) asks for `localhost` and tries ::1
    first. A port free on 127.0.0.1 can be taken on ::1 by another process
    (on this Mac, OrbStack and Node listen there), and then the request
    reaches that process instead: git reported "Received HTTP/0.9 when not
    allowed" and a write never arrived. An address leaves nothing to resolve.
    """
    return "127.0.0.1", None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    _guard.install()
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    result = yield
    if _guard.refusals:
        pytest.fail(f"The code under test caught the refusal. {_guard.refusals[0]}")
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    try:
        return (yield)
    finally:
        _guard.uninstall()
