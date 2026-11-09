"""`stack messages users` names the app of each sign-in.

The setup's phone step is done only once the admin signed in with the
phone app, not when the browser or the computer app on the Mac did. So
the command lists each account's sign-ins, read-only, with the app and
its kind ("phone", "computer", "browser", or "" when the chat server does
not say), and keeps the fields it had.

The command runs as the framework runs it (`stack.stacklet_code.load`,
then `run(args, stacklet, config)`), against Synapse's admin API stood in
on localhost by `pytest-httpserver`. The user agents are the shapes
Element X, Element Desktop and a browser send.
"""

from __future__ import annotations

from pathlib import Path

from stack.stacklet_code import load

MESSAGES = Path(__file__).resolve().parents[3] / "stacklets" / "messages"

HOMER, MARGE = "@homer:simpson", "@marge:simpson"
ELEMENT_X_ANDROID = "Element X/25.10.1 (Google Pixel 8; Android 15; AP4A.250105.002; Sdk 25.10.1)"
ELEMENT_DESKTOP = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Element/1.12.3 Chrome/138.0.7204.97 Electron/37.2.0 "
                   "Safari/537.36")
SAFARI_ON_THE_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                     "(KHTML, like Gecko) Version/18.1 Safari/605.1.15")
CHROME_ON_A_PHONE = ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")


def _synapse(httpserver, devices: dict[str, list[dict] | None]) -> None:
    """Synapse's admin API: a login, the users, and each user's devices
    (None answers 404, a server that does not list them)."""
    httpserver.expect_request("/_matrix/client/v3/login", method="POST").respond_with_json(
        {"access_token": "t", "device_id": "STACKCLI", "user_id": "@stackadmin:simpson"})
    httpserver.expect_request("/_synapse/admin/v2/users").respond_with_json({"users": [
        {"name": HOMER, "displayname": "Homer", "admin": True, "last_seen_ts": 1762700000000},
        {"name": MARGE, "displayname": "Marge", "admin": False, "last_seen_ts": None}]})
    for user, listed in devices.items():
        path = f"/_synapse/admin/v2/users/{user}/devices"
        if listed is None:
            httpserver.expect_request(path).respond_with_json({"errcode": "M_UNKNOWN"}, status=404)
        else:
            httpserver.expect_request(path).respond_with_json({"devices": listed, "total": len(listed)})


def _users(httpserver, tmp_path) -> dict:
    """`stack messages users --json`, as the framework runs the command."""
    command = load(MESSAGES, MESSAGES / "cli" / "users.py")
    config = {"is_healthy": lambda: True, "instance_dir": str(tmp_path),
              "stack": {"messages": {"server_name": "simpson"}},
              "secrets": {"global__ADMIN_PASSWORD": "stand-in"},
              "manifest": {"ports": {"synapse": httpserver.port}}}
    return command.run([], {"id": "messages"}, config)


def _device(device_id: str, agent: str | None, seen: int = 1762700000000) -> dict:
    return {"device_id": device_id, "display_name": "Homer's", "last_seen_ip": "192.168.1.31",
            "last_seen_ts": seen, "last_seen_user_agent": agent, "user_id": HOMER}


def test_each_sign_in_is_named_by_its_app_and_its_kind(httpserver, tmp_path):
    """Given the admin signed in with Element X on a phone, Element on the
    Mac and Safari, then each sign-in names its app, and only the phone
    app is of kind phone."""
    _synapse(httpserver, {HOMER: [_device("PIXEL8", ELEMENT_X_ANDROID),
                                  _device("MACAPP", ELEMENT_DESKTOP),
                                  _device("SAFARI", SAFARI_ON_THE_MAC)],
                          MARGE: []})

    homer = _users(httpserver, tmp_path)["users"][0]

    assert [(d["id"], d["app"], d["kind"]) for d in homer["devices"]] == [
        ("PIXEL8", "Element X Android", "phone"),
        ("MACAPP", "Element Desktop", "computer"),
        ("SAFARI", "Element Web", "browser")]
    assert homer["devices"][0]["last_seen"] == 1762700000000


def test_a_browser_on_a_phone_is_a_browser_not_the_phone_app(httpserver, tmp_path):
    """Given the admin opened Element Web in the phone's browser, then that
    sign-in is a browser: the phone app has not signed in."""
    _synapse(httpserver, {HOMER: [_device("CHROME", CHROME_ON_A_PHONE)], MARGE: []})

    homer = _users(httpserver, tmp_path)["users"][0]

    assert [d["kind"] for d in homer["devices"]] == ["browser"]


def test_a_sign_in_the_server_says_nothing_about_has_no_app_and_no_kind(httpserver, tmp_path):
    """Given a device without a user agent, as Synapse lists a sign-in it
    has not seen since it began to record them, then its app and kind are
    empty: whoever reads the list cannot tell whether it is the phone."""
    _synapse(httpserver, {HOMER: [_device("OLDAPP", None)], MARGE: []})

    homer = _users(httpserver, tmp_path)["users"][0]

    assert [(d["app"], d["kind"]) for d in homer["devices"]] == [("", "")]


def test_users_keep_their_fields_and_a_list_the_server_refused_is_none(httpserver, tmp_path):
    """Resting state: each user keeps `id`, `name`, `admin` and
    `last_seen`; the sign-ins are an addition. A user whose devices the
    server did not list has `devices` None, never an empty list, which
    would say nobody signed in."""
    _synapse(httpserver, {HOMER: None, MARGE: []})

    users = _users(httpserver, tmp_path)["users"]

    assert [{k: u[k] for k in ("id", "name", "admin", "last_seen")} for u in users] == [
        {"id": HOMER, "name": "Homer", "admin": True, "last_seen": 1762700000000},
        {"id": MARGE, "name": "Marge", "admin": False, "last_seen": None}]
    assert [u["devices"] for u in users] == [None, []]
    assert all(r.method == "GET" for r, _ in httpserver.log if "/_synapse/" in r.path)
