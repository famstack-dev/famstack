"""The stack as the dashboard reaches it, for an instance that runs nowhere.

The dashboard's tools server asks two things outside itself: the host API
(`stack list`, `stack family`, who is in the chat, a streamed `stack up`)
over a socket, and the memory's cards API for its history over HTTP. This
module stands in for both, on loopback ports, for a made-up family in a
named state. The first-run browser test drives the dashboard through it,
and so does the preview (`tools/dashboard_preview.py`), so a screen looks
the same in both:

    host = DashboardHost.in_state("signed", root=tmp_path / "instance")
    port = host.serve()                            # the host API, on 127.0.0.1:<port>
    history_url = serve_history(lambda: host.history)  # the cards API's /history
    host.enter("services")                         # the next state, same servers
    host.jump("chat")                              # any state, also back

    os.environ["STACK_API_HOST"] = "127.0.0.1"
    os.environ["STACK_API_PORT"] = str(port)
    os.environ["MEMORY_CARDS_URL"] = history_url

The listing is the real `stack list` of a throwaway instance, with Docker
answered for it: the stacklets of the state run and answer their checks,
everything else is available. A `stack up` streamed from the page plays a
few steps, then the stacklet runs. Nothing here runs Docker, writes outside
`root`, or reaches the network.

The states, in the order a family goes through them:

    fresh      core runs, nothing else: the first run's end
    chat       the chat runs, nobody has signed in yet
    signed     the admin has signed in to the chat, in the browser on the Mac
    services   photos, documents, code and the memory run too; no AI
    done       the AI runs as well, and Documents has filed a document
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__all__ = ["DashboardHost", "FAMILY", "FIRST_DOCUMENT", "FIREWALL_BLOCKS", "FIREWALL_OFF", "STATES",
           "SIGN_INS", "serve_history"]

REPO = Path(__file__).resolve().parents[1]

FAMILY = {"name": "Simpson", "domain": "", "language": "en", "chat_server": "simpson", "people": [
    {"id": "homer", "name": "Homer", "relation": "parent", "words": "he", "role": "admin",
     "email": "homer@home.local"},
    {"id": "marge", "name": "Marge", "relation": "parent", "words": "she", "role": "member",
     "email": "marge@home.local"},
    {"id": "bart", "name": "Bart", "relation": "child", "words": "he", "role": "member",
     "email": "bart@home.local"},
    {"id": "lisa", "name": "Lisa", "relation": "child", "words": "she", "role": "member",
     "email": "lisa@home.local"},
]}

# The Simpsons' first passwords, as `stack family set` stores them in
# `.stack/secrets.toml`: four words each, never the username. Homer's is
# fixed, so every preview picture shows the same password.
PASSWORDS = {"homer": "otter-maple-cloud-lamp", "marge": "fiber-tulip-cozy-swan",
             "bart": "yeast-jolly-pouch-drum", "lisa": "sax-tiara-opal-quilt"}

# A first document, as the memory's history tells it: Documents filed it.
FIRST_DOCUMENT = {"changes": [{
    "at": "2026-10-28T09:00:00+00:00", "kind": "filed", "who": "marge", "who_name": "Marge",
    "count": 1, "types": {"document": 1},
    "cards": [{"id": "permission-slip", "title": "Permission slip", "type": "document",
               "link": "/permission-slip"}]}]}
NO_HISTORY = {"changes": []}

# The Mac's firewall as `stack core firewall --json` answers: off, and as
# step 4 warns of it, blocking OrbStack.
FIREWALL_OFF = {"on": False, "blocks": ""}
FIREWALL_BLOCKS = {"on": True, "blocks": "orbstack"}

# Sign-ins as `stack messages users` names them (`messages/cli/_devices.py`):
# the browser on the Mac, Element X on an Android phone, and one whose app
# the chat server does not say.
SIGN_INS = {
    "browser": {"id": "WEBMAC", "app": "Element Web", "kind": "browser",
                "last_seen": 1761560000000},
    "phone": {"id": "PIXEL8", "app": "Element X Android", "kind": "phone",
              "last_seen": 1761560600000},
    "unknown": {"id": "OLDAPP", "app": "", "kind": "", "last_seen": 1761560300000},
}

CORE_AND_CHAT = ("core", "messages")
SERVICES = (*CORE_AND_CHAT, "photos", "docs", "code", "memory")


@dataclass(frozen=True)
class State:
    """What runs, who has signed in to the chat, and what the memory filed."""
    running: tuple[str, ...]
    signed_in: tuple[str, ...] = ()
    history: dict = field(default_factory=lambda: NO_HISTORY)


STATES = {
    "fresh": State(("core",)),
    "chat": State(CORE_AND_CHAT),
    "signed": State(CORE_AND_CHAT, signed_in=("homer",)),
    "services": State(SERVICES, signed_in=("homer",)),
    "done": State((*SERVICES, "ai"), signed_in=("homer",), history=FIRST_DOCUMENT),
}

# What `stack up` reports, as the stand-in plays it: the chat's own steps,
# and a few for any other stacklet.
UP_STEPS = {
    "messages": ["Rendering environment", "Pulling images", "Starting containers",
                 "Creating accounts for everyone", "Opening the family's rooms"],
}
OTHER_STEPS = ["Rendering environment", "Pulling images", "Starting containers"]
STEP_SECS = 0.7


_LISTING = threading.Lock()
# The host API's first-words commands, by the `stack ai` command each runs.
FIRST_WORDS = {"ai_welcome": "hello", "ai_intro": "intro"}


class DashboardHost:
    """The host API for one throwaway instance under `root`.

    `running` and `signed_in` change as the page starts stacklets; `asked`
    keeps every request, for a test to read. `firewall` is the answer of
    `stack core firewall --json`: off unless a test or a shot sets it. Who
    signed in did so in the browser on the Mac; `sign_ins` names other
    sign-ins per person (`host.sign_ins["homer"] = [SIGN_INS["phone"]]`,
    None for a chat server that did not list them). A test with stacklets of its
    own puts them into an extension dir under `root` before the first
    request, and answers `configure` with `configure`. `mac` is the Mac's
    address in `[core] host`: localhost, or its home-network address
    ("192.168.1.20") for the chat's address a phone gets."""

    def __init__(self, root: Path, running=("core",), signed_in=(), history=NO_HISTORY,
                 *, configure=None, steps: dict | None = None, mac: str = "localhost"):
        self.root = root
        self.running = set(running)
        self.signed_in = set(signed_in)
        self.sign_ins: dict[str, list[dict] | None] = {}
        self.history = history
        self.asked: list[dict] = []
        # The Server room's messages that `stack devices notice` sent.
        self.posted: list[dict] = []
        self.firewall: dict = FIREWALL_OFF
        self.configure = configure or (lambda answers: {"ready": True})
        self.steps = {**UP_STEPS, **(steps or {})}
        self._after_up: str | None = None
        _write_instance(root, mac)

    @classmethod
    def in_state(cls, name: str, root: Path, **kwargs) -> "DashboardHost":
        """The host API for one of `STATES`."""
        state = STATES[name]
        return cls(root, state.running, state.signed_in, state.history, **kwargs)

    def enter(self, name: str) -> None:
        """Move to one of `STATES`. A stacklet once set up stays set up in
        the instance, as on a Mac, so go through them in their order."""
        state = STATES[name]
        self.running, self.signed_in = set(state.running), set(state.signed_in)
        self.history = state.history

    def jump(self, name: str) -> None:
        """Move to one of `STATES` from any other, also back: a stacklet
        the state does not run was never set up, as on a Mac that is at
        that point of its setup."""
        running = STATES[name].running
        for marker in [*(self.root / ".stack").glob("*.setup-done"),
                       *(self.root / ".stack").glob("*.up")]:
            if marker.name.split(".", 1)[0] not in running:
                marker.unlink()
        self.enter(name)

    # ── The host API's answers ───────────────────────────────────────────

    def answer(self, request: dict) -> list[dict]:
        """The reply lines to one request, in order; `_wait` is how long to
        wait before a line is sent."""
        self.asked.append(request)
        cmd = request.get("cmd")
        if cmd == "up" and request.get("stream"):
            return self.up(request["stacklet"])
        if cmd == "list":
            return [self.listing()]
        if cmd == "family":
            return [FAMILY]
        # A person's password is read for real on the instance, as the host
        # API reads it (`stack.passwords.of_people`).
        if cmd == "passwords":
            from stack import passwords
            return [{"passwords": passwords.of_people(self._stack(), request.get("people"))}]
        if cmd == "configure":
            return [self.configure(request.get("answers", {}))]
        if cmd == "firewall":
            return [self.firewall]
        # The AI step's two commands run for real on the instance, as the
        # host API runs `./stack ai recommended`: a test points the AI
        # stacklet at its own oMLX stand-in.
        if cmd == "ai_recommended":
            return [self._stack().run_cli_command("ai", "recommended", ["show"])]
        if cmd == "ai_download":
            speech = "on" if request.get("speech") else "off"
            return [self._stack().run_cli_command(
                "ai", "recommended", ["download", "--speech", speech, "--no-wait"])]
        # `ai_switch` runs `stack ai switch <address> --key -` for real, the
        # key on stdin as the host API sends it.
        if cmd == "ai_switch":
            return [self._switch(request)]
        # The AI's key runs for real as well, as `./stack ai key show` and
        # `new --no-wait`: a test gives the instance its `[ai]` and its
        # home directory oMLX's settings.
        if cmd == "ai_key":
            return [self._stack().run_cli_command("ai", "key", ["show"])]
        if cmd == "ai_new_key":
            return [self._stack().run_cli_command("ai", "key", ["new", "--no-wait"])]
        # The AI's first words run for real too, as `./stack ai hello --audio`
        # and `intro --audio`, against the speech server the test stands in.
        if cmd in FIRST_WORDS:
            return [self._stack().run_cli_command("ai", FIRST_WORDS[cmd], ["--audio"])]
        # The list of paired devices runs for real on the instance, as the
        # host API runs `./stack devices ... --json`.
        if cmd == "devices":
            return [self._devices_command("list", {})]
        if cmd == "devices_set":
            return [self._devices_command(request.get("action", ""), request)]
        # The Server room's notice is written for real, as `./stack devices
        # notice` writes it; the chat is stood in: what it would send goes
        # to `posted` while messages runs.
        if cmd == "join_notice":
            return [self._join_notice(request)]
        if cmd == "chat_users" and "messages" in self.running:
            return [{"users": [{"id": f"@{p['id']}:{FAMILY['chat_server']}", "name": p["name"],
                                "admin": p["role"] == "admin",
                                "last_seen": 1761560000000 if p["id"] in self.signed_in else None,
                                "devices": self._devices(p["id"])}
                               for p in FAMILY["people"]]}]
        return [{"error": f"{cmd} is not stood in"}]

    def _devices(self, person: str) -> list[dict] | None:
        """One person's sign-ins: the ones set, else the browser on the Mac
        for whoever signed in."""
        if person in self.sign_ins:
            return self.sign_ins[person]
        return [SIGN_INS["browser"]] if person in self.signed_in else []

    def _devices_command(self, verb: str, args: dict) -> dict:
        """`stack devices <verb>` on the instance, as `handle_devices` runs it."""
        from stack import devices
        return devices.command(self.root, verb, args, datetime.now().astimezone())

    def _join_notice(self, request: dict) -> dict:
        """`stack devices notice` on the instance, with the chat stood in."""
        from stack.devices import notice as devices_notice
        if request.get("kind") == "added":
            notice = devices_notice.added(self.root, request.get("device", ""),
                                          request.get("by", ""))
        else:
            notice = devices_notice.waits(self.root, request, home=self._stack()._home_url())
        if "error" in notice:
            return notice
        if "messages" not in self.running:
            return {"ok": True, "sent": False, "why": "Messages is not running"}
        self.posted.append(notice)
        return {"ok": True, "sent": True}

    def _switch(self, request: dict) -> dict:
        """`stack ai switch` on the instance, as the host API runs it."""
        import io
        import sys
        args = [request["address"], "--key", "-"]
        if request.get("model"):
            args += ["--model", request["model"]]
        saved = sys.stdin
        sys.stdin = io.StringIO(str(request.get("key") or "") + "\n")
        try:
            return self._stack().run_cli_command("ai", "switch", args)
        finally:
            sys.stdin = saved

    def _stack(self):
        """A Stack over the instance, as `./stack` builds one."""
        from stack import Stack
        return Stack(REPO, self.root / "data", instance_dir=self.root)

    def listing(self) -> dict:
        """`stack list --json` of the instance, with Docker answering that
        what runs runs and answers its checks."""
        import stack.docker
        from stack import Stack

        # A page reads the listing while a run's thread adds to `running`, and
        # two pages read it at once: each reads one copy, and one at a time,
        # because Docker's answers are stood in for on the module.
        with _LISTING:
            running = set(self.running)
            for sid in running:
                (self.root / ".stack" / f"{sid}.setup-done").touch()
                # A stacklet without containers runs while its stamp exists.
                (self.root / ".stack" / f"{sid}.up").touch()
            stck = Stack(REPO, self.root / "data", instance_dir=self.root)
            saved = stack.docker.project_states, stack.docker.check_health
            stack.docker.project_states = lambda: {sid: "running" for sid in running}
            stack.docker.check_health = lambda url, headers=None: True
            try:
                return json.loads(json.dumps(stck.list(), default=str))
            finally:
                stack.docker.project_states, stack.docker.check_health = saved

    def up(self, sid: str) -> list[dict]:
        """The run's events, each a line, the result last; the stacklet
        runs once they are sent."""
        events = [{"id": i, "type": "step.done", "time": _now(), "data": {"text": text},
                   "_wait": STEP_SECS}
                  for i, text in enumerate(self.steps.get(sid, OTHER_STEPS), start=1)]
        self._after_up = sid
        events.append({"id": len(events) + 1, "type": "result", "time": _now(),
                       "data": {"ok": True, "stacklet": sid, "healthy": True}})
        return events

    # ── The socket ───────────────────────────────────────────────────────

    def serve(self) -> int:
        """Answer the host API's protocol on a loopback port, in daemon
        threads: one JSON request a line, one reply line each. Returns the
        port."""
        listener = socket.create_server(("127.0.0.1", 0))

        def reply(conn):
            with conn, conn.makefile("rb") as reader:
                request = json.loads(reader.readline())
                for line in self.answer(request):
                    time.sleep(line.pop("_wait", 0))
                    conn.sendall((json.dumps(line) + "\n").encode())
                if self._after_up:
                    self.running.add(self._after_up)
                    self._after_up = None

        def accept():
            while True:
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                threading.Thread(target=reply, args=(conn,), daemon=True).start()

        threading.Thread(target=accept, daemon=True).start()
        self._listener = listener
        return listener.getsockname()[1]

    def close(self) -> None:
        if getattr(self, "_listener", None):
            self._listener.close()


def serve_history(history) -> str:
    """The memory's cards API as far as the dashboard reads it: `/history`
    answers what `history()` returns at that moment, on a loopback port, in
    a daemon thread. Returns the base URL for `MEMORY_CARDS_URL`."""
    class History(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(history()).encode()
            self.send_response(200 if self.path.startswith("/history") else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), History)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def _write_instance(root: Path, mac: str = "localhost") -> None:
    """A `stack.toml` for the throwaway instance: port mode on the Mac's
    address `mac`, the chat named for the family, extensions under `root`;
    its `users.toml` with the family's people, whom `stack devices
    add` pairs a device for; and their passwords (`PASSWORDS`) in the
    secrets."""
    (root / ".stack").mkdir(parents=True, exist_ok=True)
    users = root / "users.toml"
    if not users.exists():
        from stack.family import users_toml
        users.write_text(users_toml([{"id": p["id"], "name": p["name"], "role": p["role"]}
                                     for p in FAMILY["people"]]))
    secrets = root / ".stack" / "secrets.toml"
    if not secrets.exists():
        from stack.secrets import TomlSecretStore
        store = TomlSecretStore(secrets)
        for person, password in PASSWORDS.items():
            store.set("global", f"USER_{person.upper()}_PASSWORD", password)
    toml = root / "stack.toml"
    if not toml.exists():
        toml.write_text(
            f'[core]\ndata_dir = "{root / "data"}"\nhost = "{mac}"\n'
            f'extension_dirs = ["{root / "extensions"}"]\n\n'
            f'[messages]\nserver_name = "{FAMILY["chat_server"]}"\n')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
