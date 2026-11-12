"""The guides that tell an admin and a family what famstack trusts.

    uv run --extra test pytest -n 4 tests/unit/surface/test_security_guides.py

The admin guide's part "Security" says which devices famstack trusts, how
a device is paired and removed, what happens only on the Mac, how first
passwords are made and changed, and the known limits. The user guide's
part "Your devices" says the same for a family member without a terminal.
The guides are the surface here: a person reads them, so the tests read
them as text, the way that person does.

What the tests pin:

- No guide says that a new person's first password is the first name. A
  line about first-name passwords is allowed only where it speaks of an
  install from beta.1 or earlier, because those installs still have them.
- Both parts exist, and "Security" answers its questions in the order an
  admin asks them.
- Every label the two parts quote in bold is a label a screen shows: the
  tools server's source holds it word for word. A renamed button that the
  guide still names fails here, not in front of a family member.
- Every `stack devices <verb>` the parts name is a verb that
  `./stack devices --help` lists.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCS = REPO / "docs"
ADMIN_GUIDE = DOCS / "admin-guide.md"
USER_GUIDE = DOCS / "user-guide.md"

# Where the screens' labels live: the tools server's pages and the
# Server room's notice, which the CLI writes.
LABEL_SOURCES = [REPO / "stacklets" / "core" / "tools-server", REPO / "lib" / "stack"]

# The questions of "Security", in the order the card gives them.
SECURITY_QUESTIONS = [
    "What does famstack trust?",
    "How does a device get paired?",
    "How is a device removed?",
    "What happens only on the Mac?",
    "How are first passwords made and changed?",
    "What are the known limits?",
]

# Ways the guides used to say that the first password is the first name.
FIRST_NAME_PASSWORD = re.compile(
    r"first name, lowercase, as first password"
    r"|password[^.]*\bis the first name"
    r"|password\*\*: same as the username"
    r"|Password: same"
    r"|first password[^.]*\btheir first name"
    r"|first password[^.]*\bthe first name",
    re.IGNORECASE,
)


# ── Reading a guide ──────────────────────────────────────────────────────


def section(path: Path, heading: str) -> str:
    """The text of the level-2 part `heading` of `path`, without its
    heading line, up to the next level-2 heading; "" when there is none."""
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1) if match else ""


def headings(text: str) -> list[str]:
    """The level-3 headings of `text`, in order."""
    return re.findall(r"^### (.+)$", text, re.M)


def bold(text: str) -> list[str]:
    """Every phrase `text` sets in bold inside a sentence, outside code
    blocks. A bold lead-in at the start of a list item or a warning is the
    guide's own words, not a label, so it is left out."""
    prose = re.sub(r"^```.*?^```", "", text, flags=re.M | re.S)
    prose = re.sub(r"^(\s*(?:[-*]|\d+\.|> ⚠️)\s*)\*\*[^*]+\*\*", r"\1", prose, flags=re.M)
    return re.findall(r"\*\*([^*]+)\*\*", prose)


def label_sources_text() -> str:
    """All Python, HTML and JavaScript of the screens, as one text."""
    parts = []
    for root in LABEL_SOURCES:
        for path in root.rglob("*"):
            if path.suffix in {".py", ".html", ".js"} and "tests" not in path.parts:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


# ── First passwords ──────────────────────────────────────────────────────


def test_no_guide_says_the_first_password_is_the_first_name():
    wrong = []
    for path in DOCS.rglob("*.md"):
        if "adr" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FIRST_NAME_PASSWORD.search(line) and "beta.1" not in line:
                wrong.append(f"{path.relative_to(REPO)}:{number}: {line.strip()[:100]}")
    assert not wrong, "\n".join(wrong)


def test_the_admin_guide_tells_an_old_install_how_to_change_first_names():
    security = section(ADMIN_GUIDE, "Security")
    assert "beta.1" in security
    for app in ("Element", "Immich", "Paperless", "Forgejo"):
        assert app in security, app


# ── The two parts ────────────────────────────────────────────────────────


def test_the_admin_guide_answers_the_security_questions_in_order():
    security = section(ADMIN_GUIDE, "Security")
    assert security, "the admin guide has no part '## Security'"
    asked = [h for h in headings(security) if h in SECURITY_QUESTIONS]
    assert asked == SECURITY_QUESTIONS


def test_security_names_the_tunnel_and_the_terminal_pairing():
    security = section(ADMIN_GUIDE, "Security")
    assert "ssh -L 42002:127.0.0.1:42002" in security
    assert "stack devices pair" in security
    assert "127.0.0.1:42002" in security


def test_the_user_guide_has_your_devices():
    devices = section(USER_GUIDE, "Your devices")
    assert devices, "the user guide has no part '## Your devices'"
    for words in ("phone", "laptop", "Remove"):
        assert words in devices, words


# ── Labels and commands ──────────────────────────────────────────────────


def test_every_bold_label_is_on_a_screen():
    screens = label_sources_text()
    missing = []
    for path, heading in ((ADMIN_GUIDE, "Security"), (USER_GUIDE, "Your devices")):
        for label in bold(section(path, heading)):
            # A label quoted with its trailing "›" or "…" is the same label.
            if label.rstrip("›… ") not in screens:
                missing.append(f"{path.name} § {heading}: {label}")
    assert not missing, "\n".join(missing)


def devices_verbs() -> set[str]:
    """The verbs `./stack devices --help` names, read from the command as a
    person runs it, so a move of the code behind it changes nothing here.
    The help line reads "verb  list, pair, add, remove, seen or notice"."""
    shown = subprocess.run([str(REPO / "stack"), "devices", "--help"], capture_output=True,
                           text=True, timeout=30, check=True).stdout
    line = re.search(r"^\s+verb\s+(.+)$", shown, re.M)
    assert line, shown
    return set(re.findall(r"[a-z]+", line.group(1))) - {"or"}


def test_every_devices_verb_named_is_a_verb_of_the_command():
    named = set()
    for path, heading in ((ADMIN_GUIDE, "Security"), (USER_GUIDE, "Your devices")):
        named |= set(re.findall(r"stack devices (\w+)", section(path, heading)))
    assert named, "the parts name no `stack devices` command"
    known = devices_verbs()
    assert named <= known, named - known
