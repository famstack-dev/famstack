"""MatrixClient power-level helpers for `stack messages setup`.

The setup CLI lowers the Family space's `m.space.child` PL threshold
to 0 so any joined family member can add rooms to the space. The
helpers must be:

  - **Idempotent.** Re-running setup on a production instance must
    not over-write power_levels content that is already correct.
  - **Non-destructive.** Only the requested event threshold changes.
    `users` (the admin's PL 100) and every other key in the
    power_levels event are preserved verbatim.
  - **Self-healing.** A production instance that was set up before
    the fix shipped picks up the open-to-members PL the next time
    `stack messages setup` runs, with no manual Synapse poking.

These tests pin all three properties against fakes that capture the
HTTP traffic, so we never reach a real Synapse from the test rig.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "cli"))

import _matrix as _matrix_mod  # noqa: E402
from _matrix import MatrixClient  # noqa: E402


# ── HTTP fake ────────────────────────────────────────────────────────────


class HttpRecorder:
    """Records every Matrix HTTP call and serves canned responses.

    Routes are matched by ``(method, path_suffix)``; the recorder
    fails loudly if a call comes in for an unprepared route so a
    surprise GET / PUT in a test surfaces immediately instead of
    silently returning a default.
    """

    def __init__(self):
        self.calls: list[tuple] = []
        self._routes: dict[tuple[str, str], tuple[int, dict]] = {}

    def respond(self, method, path_suffix, *, status=200, body=None):
        self._routes[(method, path_suffix)] = (status, body or {})

    def _match(self, method, url):
        # `get_state` and `set_power_levels` build URLs that differ on
        # the trailing slash: GET ends with `/<state_key>` (empty key
        # becomes a trailing slash); PUT omits it. Strip the slash on
        # both sides so routes match either shape.
        url_norm = url.rstrip("/")
        for (m, suffix), response in self._routes.items():
            if m == method and url_norm.endswith(suffix.rstrip("/")):
                return response
        raise AssertionError(
            f"unexpected {method} {url} -- no route registered"
        )

    def get(self, url, **kw):
        self.calls.append(("GET", url, None))
        return self._match("GET", url)

    def put(self, url, body=None, **kw):
        self.calls.append(("PUT", url, body))
        return self._match("PUT", url)


def _client_with_http(tmp_path, monkeypatch) -> tuple[MatrixClient, HttpRecorder]:
    http = HttpRecorder()
    monkeypatch.setattr(_matrix_mod, "_get", http.get)
    monkeypatch.setattr(_matrix_mod, "_put", http.put)
    client = MatrixClient("http://h", "home", str(tmp_path))
    client.token = "fake-token"
    return client, http


def _puts(http: HttpRecorder) -> list[tuple]:
    return [c for c in http.calls if c[0] == "PUT"]


# ── ensure_event_power_level ────────────────────────────────────────────


class TestEnsureEventPowerLevel:
    """The single seam every higher-level PL helper composes on. Pin
    the behaviour here so callers can trust the contract: idempotent,
    preserving, returns a discriminated status."""

    SPACE_ID = "!space:home"
    PL_URL = (
        f"/_matrix/client/v3/rooms/{SPACE_ID}/state/m.room.power_levels/"
    )
    EXISTING = {
        "users": {"@stackadmin:home": 100},
        "users_default": 0,
        "events": {"m.space.child": 50, "m.room.name": 50},
        "events_default": 0,
        "state_default": 50,
    }

    def test_lowers_threshold_and_returns_set(self, tmp_path, monkeypatch):
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=self.EXISTING)
        http.respond("PUT", self.PL_URL, body={})

        outcome = client.ensure_event_power_level(
            self.SPACE_ID, "m.space.child", 0,
        )

        assert outcome == "set"
        puts = _puts(http)
        assert len(puts) == 1
        _, _, body = puts[0]
        assert body["events"]["m.space.child"] == 0
        # Every other key was preserved verbatim.
        assert body["users"] == {"@stackadmin:home": 100}
        assert body["users_default"] == 0
        assert body["events_default"] == 0
        assert body["state_default"] == 50
        # The unrelated event override survives.
        assert body["events"]["m.room.name"] == 50

    def test_already_correct_returns_ok_without_writing(
        self, tmp_path, monkeypatch,
    ):
        """Idempotency: when the target value is already in place, no
        PUT is made. This is the property a production re-run leans
        on -- we never re-stomp a healthy state event."""
        existing = {
            **self.EXISTING,
            "events": {**self.EXISTING["events"], "m.space.child": 0},
        }
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=existing)

        outcome = client.ensure_event_power_level(
            self.SPACE_ID, "m.space.child", 0,
        )

        assert outcome == "ok"
        assert _puts(http) == []

    def test_missing_event_override_is_added(self, tmp_path, monkeypatch):
        """Spaces created before any `events` override exists have no
        `events` key at all in their power_levels content. The helper
        must add the key and write, not crash."""
        bare = {
            "users": {"@stackadmin:home": 100},
            "users_default": 0,
            "events_default": 0,
            "state_default": 50,
        }
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=bare)
        http.respond("PUT", self.PL_URL, body={})

        outcome = client.ensure_event_power_level(
            self.SPACE_ID, "m.space.child", 0,
        )

        assert outcome == "set"
        body = _puts(http)[0][2]
        assert body["events"] == {"m.space.child": 0}
        # users key still survives.
        assert body["users"] == {"@stackadmin:home": 100}

    def test_get_failure_returns_failed_without_writing(
        self, tmp_path, monkeypatch,
    ):
        """Synapse refuses to serve power_levels -- treat as failure
        rather than guessing what to write. The setup output surfaces
        the failure so the operator knows their re-run didn't heal
        the production instance."""
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, status=500, body={})

        outcome = client.ensure_event_power_level(
            self.SPACE_ID, "m.space.child", 0,
        )

        assert outcome == "failed"
        assert _puts(http) == []

    def test_put_failure_returns_failed(self, tmp_path, monkeypatch):
        """Synapse rejects the PL write (admin token expired, room
        gone, etc.). The helper surfaces the failure so the caller
        does not assume success."""
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=self.EXISTING)
        http.respond("PUT", self.PL_URL, status=403, body={"errcode": "M_FORBIDDEN"})

        outcome = client.ensure_event_power_level(
            self.SPACE_ID, "m.space.child", 0,
        )

        assert outcome == "failed"


# ── open_space_to_members ───────────────────────────────────────────────


class TestOpenSpaceToMembers:
    """The high-level entry point `setup.py` calls. Same idempotency
    guarantees as the underlying helper, addressing the one specific
    UX fix (Element's 'Create a room in this Space' button)."""

    SPACE_ID = "!space:home"
    PL_URL = (
        f"/_matrix/client/v3/rooms/{SPACE_ID}/state/m.room.power_levels/"
    )
    DEFAULT_PRIVATE_CHAT_PL = {
        "users": {"@stackadmin:home": 100},
        "users_default": 0,
        "events": {"m.space.child": 50},
        "events_default": 0,
        "state_default": 50,
    }

    def test_first_run_lowers_threshold(self, tmp_path, monkeypatch):
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=self.DEFAULT_PRIVATE_CHAT_PL)
        http.respond("PUT", self.PL_URL, body={})

        assert client.open_space_to_members(self.SPACE_ID) == "set"

    def test_rerun_is_silent_noop(self, tmp_path, monkeypatch):
        """Production re-run protection: the second time setup is run
        against an already-healed instance, no PL write happens."""
        already_open = {
            **self.DEFAULT_PRIVATE_CHAT_PL,
            "events": {"m.space.child": 0},
        }
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=already_open)

        assert client.open_space_to_members(self.SPACE_ID) == "ok"
        assert _puts(http) == []

    def test_does_not_grant_user_power(self, tmp_path, monkeypatch):
        """The fix opens room creation -- it must NOT silently elevate
        any user to a higher PL. The `users` dict is preserved exactly
        as it came in from Synapse."""
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=self.DEFAULT_PRIVATE_CHAT_PL)
        http.respond("PUT", self.PL_URL, body={})

        client.open_space_to_members(self.SPACE_ID)

        body = _puts(http)[0][2]
        assert body["users"] == {"@stackadmin:home": 100}
        # Make sure we didn't accidentally bump anyone.
        for user, level in body["users"].items():
            assert level <= 100  # tautological but documents the intent


# ── User power-level promotion ───────────────────────────────────────────


class TestEnsureUserPowerLevel:
    """`ensure_user_power_level` is the seam ``stack messages setup``
    uses to give every ``role = admin`` family member PL 100 in the
    Family space and the seeded rooms. The Synapse "admin" flag on
    createUser is server-side only -- it does NOT grant in-room power,
    so without this seam every family admin would still need someone
    to elevate them manually in Element."""

    ROOM_ID = "!room:home"
    PL_URL = f"/_matrix/client/v3/rooms/{ROOM_ID}/state/m.room.power_levels"
    EXISTING = {
        "users": {"@stackadmin:home": 100},
        "users_default": 0,
        "events": {},
        "events_default": 0,
        "state_default": 50,
    }

    def test_promotes_member_to_admin(self, tmp_path, monkeypatch):
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=self.EXISTING)
        http.respond("PUT", self.PL_URL, body={})

        outcome = client.ensure_user_power_level(
            self.ROOM_ID, "@homer:home", 100,
        )

        assert outcome == "set"
        body = _puts(http)[0][2]
        assert body["users"]["@homer:home"] == 100
        # Existing admin is preserved verbatim.
        assert body["users"]["@stackadmin:home"] == 100

    def test_already_at_level_is_silent_noop(self, tmp_path, monkeypatch):
        """Re-run safety: the second pass over an already-promoted
        admin makes no PL write. setup.py also surfaces this as 'ok',
        which means the output line does not appear -- the operator
        only sees the changes."""
        existing = {
            **self.EXISTING,
            "users": {"@stackadmin:home": 100, "@homer:home": 100},
        }
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=existing)

        outcome = client.ensure_user_power_level(
            self.ROOM_ID, "@homer:home", 100,
        )

        assert outcome == "ok"
        assert _puts(http) == []

    def test_does_not_clobber_other_users(self, tmp_path, monkeypatch):
        """Bumping one admin must preserve every other entry in the
        ``users`` dict, including any manually-set PL the operator
        granted via Element."""
        existing = {
            **self.EXISTING,
            "users": {
                "@stackadmin:home": 100,
                "@marge:home": 100,         # another admin already bumped
                "@bart:home": 25,           # operator-granted intermediate PL
            },
        }
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, body=existing)
        http.respond("PUT", self.PL_URL, body={})

        client.ensure_user_power_level(self.ROOM_ID, "@homer:home", 100)

        body = _puts(http)[0][2]
        assert body["users"] == {
            "@stackadmin:home": 100,
            "@marge:home": 100,
            "@bart:home": 25,
            "@homer:home": 100,
        }

    def test_failure_paths_return_failed(self, tmp_path, monkeypatch):
        """GET or PUT refusal surfaces as 'failed' so the setup output
        prints a 'could not promote' line and the operator knows the
        re-run did not heal the instance."""
        client, http = _client_with_http(tmp_path, monkeypatch)
        http.respond("GET", self.PL_URL, status=500, body={})

        outcome = client.ensure_user_power_level(
            self.ROOM_ID, "@homer:home", 100,
        )

        assert outcome == "failed"


# ── Atomic creation-time override ────────────────────────────────────────


class TestCreateRoomSpaceOverride:
    """Fresh installs get the open-to-members PL baked into the very
    first ``createRoom`` request via ``power_level_content_override``.
    No second API call needed; no race window where the space briefly
    has the default PL 50 threshold.

    The non-space branch must NOT carry the override -- regular rooms
    have no m.space.child semantics and the extra field would just be
    noise (and potentially mask a real bug in a regression test)."""

    CREATE_URL = "/_matrix/client/v3/createRoom"

    def test_space_creation_includes_pl_override(self, tmp_path, monkeypatch):
        client, http = _client_with_http(tmp_path, monkeypatch)
        # POST goes through _post, which we have to monkeypatch too.
        post_calls: list[tuple] = []

        def fake_post(url, body=None, **kw):
            post_calls.append(("POST", url, body))
            return 200, {"room_id": "!new:home"}

        monkeypatch.setattr(_matrix_mod, "_post", fake_post)

        client.create_room("family", name="Family", space=True)

        assert len(post_calls) == 1
        body = post_calls[0][2]
        # The override is present and targets exactly the m.space.child
        # event.
        assert body.get("power_level_content_override") == {
            "events": {"m.space.child": 0},
        }
        # The space marker is independently present.
        assert body.get("creation_content") == {"type": "m.space"}

    def test_regular_room_creation_omits_pl_override(
        self, tmp_path, monkeypatch,
    ):
        """No m.space.child override on plain rooms -- it would have
        no effect and is the kind of unused payload that drifts into
        a bug over time."""
        client, http = _client_with_http(tmp_path, monkeypatch)
        post_calls: list[tuple] = []

        def fake_post(url, body=None, **kw):
            post_calls.append(("POST", url, body))
            return 200, {"room_id": "!new:home"}

        monkeypatch.setattr(_matrix_mod, "_post", fake_post)

        client.create_room("famchat", name="Family Room", space=False)

        body = post_calls[0][2]
        assert "power_level_content_override" not in body
        assert "creation_content" not in body
