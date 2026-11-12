"""A device's name, from the browser that paired it.

    name_of("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) ...")   # "iPhone"
    name_of("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ... Chrome/131.0 Safari/537.36")
    # "Chrome on a Mac"
    said("Chrome on a Mac", "de")                                            # "Chrome auf einem Mac"
    parts_of("Mozilla/5.0 (iPhone; ...) ... Version/18.1 Mobile/15E148 Safari/604.1")
    # ("Safari", "iPhone"): the fields of the Server room's notice

The Mac's list of devices shows it ("Homer · iPhone"), so the family can
tell its phones apart and remove the right one. The name comes from a
fixed list of browsers and systems, never from the User-Agent's text: a
browser chooses that text, and nothing it says reaches the Mac's pages
or the list as it is. The list keeps the English name; `said` gives it in
the page's language.
"""

from __future__ import annotations

from stack.devices.notice import SYSTEMS

__all__ = ["name_of", "parts_of", "said"]

# How a User-Agent names each browser of `stack.devices.notice.BROWSERS`,
# checked in order: Edge and Opera name Chrome too, Chrome names Safari.
_BROWSERS = (("edg/", "Edge"), ("opr/", "Opera"), ("firefox/", "Firefox"), ("fxios/", "Firefox"),
             ("crios/", "Chrome"), ("chrome/", "Chrome"), ("safari/", "Safari"))
# How it names each system of `SYSTEMS`, checked in order: an iPhone's
# agent says "like Mac OS X", and Android's says Linux.
_SYSTEMS = (("iphone", "iPhone"), ("ipad", "iPad"), ("cros", "Chromebook"),
            ("macintosh", "Mac"), ("windows", "Windows"), ("linux", "Linux"))
# Systems whose name is the device's whole name: a phone or a tablet.
_DEVICES = ("iPhone", "iPad", "Android phone", "Android tablet")
_GERMAN = {"on a Chromebook": "auf einem Chromebook", "on a Mac": "auf einem Mac",
           "on Windows": "unter Windows", "on Linux": "unter Linux",
           "Android phone": "Android-Handy", "Android tablet": "Android-Tablet",
           "A browser": "Ein Browser"}


def name_of(user_agent: str) -> str:
    """The device's name: "iPhone", "iPad", "Android phone", "Android
    tablet", or "<browser> <system>" ("Firefox on Windows"); "A browser"
    for anything it does not know."""
    browser, system = parts_of(user_agent)
    if system in _DEVICES:
        return system
    if browser and system:
        return f"{browser} {SYSTEMS[system]}"
    return browser or "A browser"


def parts_of(user_agent: str) -> tuple[str, str]:
    """The browser and the system, each a name from a fixed list or "":
    the fields the Server room's notice is written from on the Mac
    (`stack.devices.notice`), so no text of the User-Agent reaches the
    chat.

        parts_of("... (Macintosh; Intel Mac OS X 10_15_7) ... Chrome/131.0 Safari/537.36")
        # ("Chrome", "Mac")
        parts_of("... (Linux; Android 15; Pixel 8) ... Chrome/131.0.0.0 Mobile Safari/537.36")
        # ("Chrome", "Android phone")
        parts_of("curl/8.7.1")   # ("", "")
    """
    agent = user_agent.lower()
    browser = next((name for mark, name in _BROWSERS if mark in agent), "")
    if "android" in agent:
        return browser, "Android phone" if "mobile" in agent else "Android tablet"
    return browser, next((name for mark, name in _SYSTEMS if mark in agent), "")


def said(name: str, language: str) -> str:
    """`name` in `language` ("en" or "de"): the English name, or its German."""
    if language != "de":
        return name
    for english, german in _GERMAN.items():
        if name == english:
            return german
        if name.endswith(" " + english):
            return name[: -len(english)] + german
    return name
