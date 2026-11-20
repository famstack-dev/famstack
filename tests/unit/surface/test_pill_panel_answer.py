"""What the dashboard's pill panel reads: the profile's JSON with its words.

    GET /dashboard/api/person/homer      the panel's read, as one viewer may see it

A click on a family pill opens a short, read-only profile under the pills
(`dashboard/components/pill-panel.js`). The panel draws only what this read
answers, words included, so it shows no more than the profile page and
says it in the page's words: the German genitive and the relative days
are the server's, not the script's.

    who looks                  the panel's words
    Marge's phone on Homer     header block, "Homer's devices show on Homer's own phone."
    a shared device            header block, "Devices show on each person's own phone."
    Homer (admin) on Lisa      her devices, "1 device waits to join", "Settings and devices"
    Lisa on her own            "Your devices", her phone tagged "This device", the link

Driven through the tools server's FastAPI app as browsers open it (the
Browser DSL), on the dashboard's shared host stand-in
(`tests/dashboard_host.py`). Homer is the family's admin.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from .conftest import tools_server
from .dsl import Browser
from .setup_stand_in import on_this_mac, paired_phone, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

HOME = "192.168.1.20:42000"
MAC_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

devices = tools_server("devices")


@pytest.fixture
def simpsons(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack with the chat running and core's links on the
    Mac's home-network address. No join request is left behind."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    devices.JOINS.forget()
    devices.NOTICES.forget()
    host = stack_in_state(tmp_path, monkeypatch, httpserver)("chat")
    yield host
    devices.CODES.forget()
    devices.JOINS.forget()


def a_waiting_browser() -> None:
    """A laptop on the Wi-Fi that opened the home address unknown and now
    waits to join with a code."""
    import server
    laptop = Browser(server.app, listener=8001, host=HOME, user_agent=MAC_CHROME, paired=False)
    assert laptop.opens("/dashboard").status == 401


def a_device_unused_since(person: str, days: int) -> None:
    """A device of `person` paired `days` ago and not used since."""
    import os
    from pathlib import Path

    from stack import devices as device_list
    instance = Path(os.environ["DEVICES_FILE"]).parents[2]
    added = device_list.add(instance, person=person, name="iPad", key_hash="0" * 64,
                            now=datetime.now().astimezone() - timedelta(days=days))
    assert added.get("ok"), added


def test_marges_phone_on_homers_pill_reads_the_line_why_and_no_link(simpsons):
    """Given Marge's phone, when it reads Homer's panel, then it gets the
    header block's words and the line why, and no device words, no
    waiting line and no link to the page."""
    marges = paired_phone("marge")

    status, read = marges.reads("/dashboard/api/person/homer")

    assert status == 200
    assert read["devices"] is None
    said = read["said"]
    assert said["why"] == "Homer's devices show on Homer's own phone."
    assert said["admin"] == "Looks after famstack"
    assert said["memory"] == "Homer's memory"
    assert said["heading"] is None and said["route"] is None and said["waiting"] is None
    assert said["close"] == "Close"


def test_a_shared_device_reads_the_shared_line(simpsons):
    """Given a shared screen, when it reads Homer's panel, then the line
    why is the one for a shared device."""
    import server
    shared = Browser(server.app, listener=8001, host=HOME)

    _, read = shared.reads("/dashboard/api/person/homer")

    assert read["said"]["why"] == "Devices show on each person's own phone."


def test_homer_on_lisas_pill_reads_her_devices_the_waiting_line_and_the_link(simpsons):
    """Given Homer's phone (an admin), a device of Lisa's unused for 40
    days and one browser waiting, when Homer reads Lisa's panel, then the
    words name her devices, the old one says since when, one device waits
    to join, and the link leads to Settings and devices."""
    homers = paired_phone("homer")
    a_device_unused_since("lisa", 40)
    a_waiting_browser()

    _, read = homers.reads("/dashboard/api/person/lisa")

    said = read["said"]
    assert said["heading"] == "Lisa's devices"
    assert said["waiting"] == "1 device waits to join"
    assert said["route"] == "Settings and devices"
    assert said["why"] is None
    day = (datetime.now().astimezone() - timedelta(days=40)).date()
    assert [d["when"] for d in read["devices"]] == [f"not seen since {day.day} {day.strftime('%B')}"]


def test_lisas_own_panel_tags_her_phone(simpsons):
    """Given Lisa's phone, when she reads her own panel, then the heading
    is "Your devices", her phone is seen now, and the link is there."""
    lisas = paired_phone("lisa")

    _, read = lisas.reads("/dashboard/api/person/lisa")

    said = read["said"]
    assert said["heading"] == "Your devices"
    assert said["memory"] == "Your memory"
    assert said["route"] == "Settings and devices"
    assert said["waiting"] is None
    assert [(d["this"], d["when"]) for d in read["devices"]] == [(True, "seen now")]


def test_the_mac_reads_the_link_and_the_waiting_line_in_german(simpsons):
    """Given the Mac's browser in German, when it reads Bart's
    panel with a browser waiting, then the words are German, with Bart's
    genitive."""
    a_waiting_browser()
    mac = on_this_mac("de")

    _, read = mac.reads("/dashboard/api/person/bart")

    said = read["said"]
    assert said["heading"] == "Barts Geräte"
    assert said["empty"] == "Bart hat noch kein Gerät."
    assert said["waiting"] == "1 Gerät wartet auf Einlass"
    assert said["route"] == "Einstellungen und Geräte"
