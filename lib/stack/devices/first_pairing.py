"""The first pairing after an update: what the restart says to an install
that has a family but no paired device.

    stack update            # moves the checkout to the release
    stack restart           # brings core up again, then, with no device paired:

    famstack's pages now open only on paired phones and browsers.
    Pair your phone: scan this code with its camera
    █████████████████████████████████████
    ...
    Pair the others on the Mac (Setup, Add a phone) or with: stack devices pair <id> (marge, bart, lisa)
    The Mac's own pages ask for the admin key: open them from the menu bar (Setup...) or with: stack devices admin-link

    needed(root)                  # True: a family, no device in the list
    others_line(root)             # "Pair the others ... (marge, bart, lisa)"
    server_room_text("de")        # "Links zu famstack öffnen sich jetzt nur ..."
    told(root); remember_told(root)

Why it exists. Since ADR-023, the home address's pages (the
`/go` links in the chat, the sign-in guide, the wiki) open only on a
paired device. An install from before that has a family and no paired
device, so after the update every phone, the admin's too, answers "This
browser does not know you yet", and allowing a waiting browser needs a
paired admin phone or the Mac. The update itself restarts nothing, so the
restart that applies it is where the admin learns how to pair: the same
QR code the installer ends with (ADR-023 D21), for the first admin.
The Mac's own pages ask for the admin key now too (ADR-023 D23, D24), so the
restart names the command that prints it, never the key itself.

The admins hear it once in the Server room as well, in the family's
language, without a code or a link: a pairing code in the chat would let
anyone who reads the chat pair a device. `told` and `remember_told` keep
"once" in a marker file beside the instance's other state, not in the
devices folder, which core mounts and which holds the list alone.

    stack restart ──▶ core is up? ──▶ needed()? ──▶ the Server room, once (told)
                                                └─▶ the lines and, in a terminal,
                                                    the first admin's QR code

What it does not do: print the QR code (the installer's last step does,
reused by the restart in `stack.cli`), send the message (`stack messages
send`, as the other Server room notices), or write the list of devices.
The design: ADR-023, A Mac without a screen.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .. import devices
from ..users import get_admin_user, load_users, user_id

__all__ = ["INTRO", "ADMIN_LINK_LINE", "TOLD_FILE", "needed", "first_admin", "others_line",
           "your_phone_line", "server_room_text", "told", "remember_told"]

# The restart's first line about pairing.
INTRO = "famstack's pages now open only on paired phones and browsers."
# Where the Mac's own pages get their key. The command prints the key;
# the restart's output never does, because it may go to a log or a pipe.
ADMIN_LINK_LINE = ("The Mac's own pages ask for the admin key: open them from the menu bar (Setup...) "
                   "or with: stack devices admin-link")
# The marker that the Server room heard it, under the instance directory.
# Not in `.stack/devices/`: core mounts that folder, and it holds the list alone.
TOLD_FILE = Path(".stack") / "first-pairing-told"

# The Server room's message, per language of `[core] language`. The labels
# are the Mac's pages' own ("Setup", "Add a phone"; "Einrichtung",
# "Handy hinzufügen"). No code and no link: whoever reads the chat must
# not be able to pair a device with it.
_SERVER_ROOM = {
    "en": ("Links to famstack now open only on paired phones and browsers. "
           "Pair yours at the Mac: Setup, then Add a phone."),
    "de": ("Links zu famstack öffnen sich jetzt nur auf hinzugefügten Handys und Browsern. "
           "Füg deins am Mac hinzu: Einrichtung, dann Handy hinzufügen."),
}


def needed(instance_dir: Path) -> bool:
    """True when the instance has a family with an admin and no paired
    device: then the restart says how to pair. False with any device in
    the list (a shared one too), and before the family is set up.

        needed(root)   # True: the Simpsons, an empty list of devices
    """
    return get_admin_user(instance_dir) is not None and not devices.listed(instance_dir)


def first_admin(instance_dir: Path) -> str:
    """The user id of the family's first admin, whose phone the restart's
    QR code pairs; "" without a family.

        first_admin(root)   # "homer"
    """
    admin = get_admin_user(instance_dir)
    return user_id(admin) if admin else ""


def others_line(instance_dir: Path) -> str:
    """The line that says how to pair everyone but the first admin, naming
    each id; "" when the first admin is the whole family.

        others_line(root)
        # "Pair the others on the Mac (Setup, Add a phone) or with:
        #  stack devices pair <id> (marge, bart, lisa)"
    """
    first = first_admin(instance_dir)
    others = [user_id(u) for u in load_users(instance_dir) if user_id(u) != first]
    if not others:
        return ""
    return ("Pair the others on the Mac (Setup, Add a phone) or with: "
            f"stack devices pair <id> ({', '.join(others)})")


def your_phone_line(instance_dir: Path) -> str:
    """The line in place of the QR code where nobody looks at a terminal
    (the menu bar app, a pipe): the command that shows it later.

        your_phone_line(root)   # "Pair your phone with: stack devices pair homer"
    """
    return f"Pair your phone with: stack devices pair {first_admin(instance_dir)}"


def server_room_text(language: str) -> str:
    """The Server room's message in `language` (`[core] language`),
    English for a language it has no text for.

        server_room_text("de")   # "Links zu famstack öffnen sich jetzt nur ..."
        server_room_text("fr")   # "Links to famstack now open only ..."
    """
    return _SERVER_ROOM.get(language, _SERVER_ROOM["en"])


def told(instance_dir: Path) -> bool:
    """True once the Server room heard the message (`remember_told`)."""
    return (instance_dir / TOLD_FILE).exists()


def remember_told(instance_dir: Path) -> None:
    """Write the marker that the Server room heard the message, with the
    time, so a later restart does not send it again. Call it only after
    the chat took the message: a chat that was down hears it next time."""
    marker = instance_dir / TOLD_FILE
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n")
