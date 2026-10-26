"""The backup stacklet's setup questions, as a screen walks them.

Backup asks for the disk to copy to, its passphrase when the disk is
encrypted, and the nightly time. A screen walks them with `stack configure
backup --json`, posting every answer so far: each call prints the next
form, the disk form again with an error when the disk will not do, or
`{"ready": true}` once the answers are applied.

The Mac is stood in by three scripts first on PATH, as macOS answers:
`mount` lists what is mounted, `diskutil` tells an encrypted volume and
its UUID, and `security` is the login Keychain. Each records how it was
called, so a test sees what reached the Keychain. Everything else is
`./stack` itself, run in this process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PASSPHRASE = "Moe's-Tavern-1989"
UUID = "4B1D7A52-6C0E-4F3B-9E21-0D5C3A7F8B11"

MOUNT = """\
/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
/dev/disk5s1 on /Volumes/backup-vault (apfs, local, nodev, nosuid, journaled, noowners)
/dev/disk6s1 on /Volumes/USB STICK (msdos, local, nodev, nosuid, noowners)
"""

APFS_LIST = """\
+-- Container disk5
    +-> Volume disk5s1
        APFS Volume Disk (Role):   disk5s1 (No specific role)
        Name:                      backup-vault (Case-insensitive)
        Mount Point:               /Volumes/backup-vault
        Capacity Consumed:         1.2 TB
        FileVault:                 {filevault}
"""

# The stand-ins are shell scripts: one runs in a few milliseconds, where a
# Python one takes fifty, and a walk through the forms calls them often.
STAND_INS = {
    "mount": """
cat "$HERE/mount.txt"
""",
    "diskutil": f"""
case "$1 $2" in
  "apfs list") cat "$HERE/apfs-list.txt" ;;
  "info backup-vault") echo "   Volume Name:   backup-vault"
                       echo "   Volume UUID:   {UUID}" ;;
  *) exit 1 ;;
esac
""",
    "security": """
[ -d "$HERE/keychain" ] || mkdir "$HERE/keychain"
account=""; password=""; previous=""
for arg in "$@"; do
  [ "$previous" = "-a" ] && account="$arg"
  [ "$previous" = "-w" ] && password="$arg"
  previous="$arg"
done
case "$1" in
  find-generic-password) [ -f "$HERE/keychain/$account" ] || exit 44
                         cat "$HERE/keychain/$account"; echo ;;
  add-generic-password) printf '%s' "$password" > "$HERE/keychain/$account" ;;
esac
""",
}

# Every stand-in records its name and arguments, one call per line,
# tab-separated, before it answers. The scripts are written once per
# session, since macOS checks a new executable on its first run; what a
# test's Mac holds is in the directory `STAND_IN_MAC` names.
PRELUDE = """#!/bin/sh
HERE=$STAND_IN_MAC
{ printf '%s' "${0##*/}"; for arg in "$@"; do printf '\\t%s' "$arg"; done
  printf '\\n'; } >> "$HERE/calls.tsv"
"""


class Mac:
    """The stand-ins' side: what is mounted, and what they were asked."""

    def __init__(self, state: Path):
        self.state = state

    def encrypted(self, yes: bool) -> None:
        (self.state / "apfs-list.txt").write_text(APFS_LIST.format(filevault="Yes" if yes else "No"))

    def calls(self, tool: str) -> list[list[str]]:
        log = self.state / "calls.tsv"
        lines = log.read_text().splitlines() if log.exists() else []
        return [call[1:] for call in (line.split("\t") for line in lines) if call[0] == tool]

    def keychain(self) -> dict[str, str]:
        folder = self.state / "keychain"
        return {p.name: p.read_text() for p in folder.iterdir()} if folder.exists() else {}

    def remembers(self, volume_uuid: str, passphrase: str) -> None:
        """A Keychain entry made before this test."""
        (self.state / "keychain").mkdir(exist_ok=True)
        (self.state / "keychain" / volume_uuid).write_text(passphrase)


@pytest.fixture(scope="session")
def stand_ins(tmp_path_factory) -> Path:
    """`mount`, `diskutil` and `security` as scripts, in one directory."""
    bin_dir = tmp_path_factory.mktemp("stand-ins")
    for name, body in STAND_INS.items():
        script = bin_dir / name
        script.write_text(PRELUDE + body)
        script.chmod(0o755)
    return bin_dir


@pytest.fixture
def mac(stand_ins, tmp_path, monkeypatch) -> Mac:
    """The stand-ins first on PATH; the backup disk mounted, APFS, not
    encrypted, and an empty Keychain."""
    state = tmp_path / "mac"
    state.mkdir()
    (state / "mount.txt").write_text(MOUNT)
    stand_in = Mac(state)
    stand_in.encrypted(False)
    monkeypatch.setenv("STAND_IN_MAC", str(state))
    monkeypatch.setenv("PATH", f"{stand_ins}:/usr/bin:/bin")
    return stand_in


@pytest.fixture
def backup(stack_cli, mac):
    """`stack configure backup --json --answer <each>`, its JSON answer."""
    def configure(**answers: str) -> dict:
        flags = [f for key, value in answers.items() for f in ("--answer", f"{key}={value}")]
        ran = stack_cli("configure", "backup", "--json", *flags)
        assert ran.code == 0, ran.err
        return ran.answer
    return configure


# ── The forms, one after the other ───────────────────────────────────────

def test_with_no_answers_it_asks_for_the_disk_and_offers_the_mounted_ones(backup):
    form = backup()["form"]

    assert form["id"] == "backup.disk"
    assert form["schema"]["properties"]["disk"]["examples"] == ["backup-vault", "USB STICK"]
    assert form["errors"] == {}


def test_an_encrypted_disk_asks_for_its_passphrase_next(backup, mac):
    mac.encrypted(True)

    form = backup(disk="backup-vault")["form"]

    assert form["id"] == "backup.passphrase"
    assert form["schema"]["properties"]["passphrase"]["writeOnly"] is True


def test_a_plain_disk_goes_straight_to_the_time(backup):
    form = backup(disk="backup-vault")["form"]

    assert form["id"] == "backup.schedule"
    assert form["schema"]["properties"]["time"]["format"] == "time"


def test_a_disk_that_is_not_mounted_comes_back_with_the_reason(backup):
    form = backup(disk="offsite")["form"]

    assert form["id"] == "backup.disk"
    assert "is not mounted at /Volumes/offsite" in form["errors"]["disk"]


def test_a_disk_that_is_not_apfs_comes_back_with_the_reason(backup):
    """FAT and exFAT ignore the immutability flag the backup relies on."""
    form = backup(disk="USB STICK")["form"]

    assert form["id"] == "backup.disk"
    assert "Reformat as APFS" in form["errors"]["disk"]


def test_a_time_that_is_not_a_time_comes_back_with_the_reason(backup):
    form = backup(disk="backup-vault", time="2am")["form"]

    assert form["errors"] == {"time": "Answer with a time like 02:00 or 22:15."}


# ── Applied ──────────────────────────────────────────────────────────────

def test_every_answer_is_applied_as_the_hook_always_wrote_it(backup, mac, stack_cli):
    """The target in stack.toml, and the passphrase in the Keychain in the
    layout `diskutil apfs unlockVolume` reads."""
    mac.encrypted(True)
    answers = stack_cli.instance.parent / "answers.json"
    answers.write_text(json.dumps({"disk": "backup-vault", "passphrase": PASSPHRASE,
                                   "time": "03:30"}))

    ran = stack_cli("configure", "backup", "--json", "--answers", str(answers))

    assert ran.answer == {"ready": True}
    assert ('[backup.targets.vault]\nengine = "external-disk"\ndisk = "backup-vault"\n'
            'schedule = "30 3 * * *"\n') in stack_cli.toml()
    assert mac.keychain() == {UUID: PASSPHRASE}
    assert ["add-generic-password", "-a", UUID, "-s", UUID, "-D", "APFS Volume Password",
            "-l", "backup-vault", "-w", PASSPHRASE, "-U"] in mac.calls("security")
    assert PASSPHRASE not in ran.out + ran.err + stack_cli.toml()


def test_a_plain_disk_puts_nothing_in_the_keychain(backup, mac):
    assert backup(disk="backup-vault", time="02:00") == {"ready": True}
    assert mac.calls("security") == []


def test_a_passphrase_already_in_the_keychain_is_not_asked_again(backup, mac):
    mac.encrypted(True)
    mac.remembers(UUID, "stored before")

    assert backup(disk="backup-vault")["form"]["id"] == "backup.schedule"


def test_a_configured_backup_is_ready_and_unchanged(backup, stack_cli):
    backup(disk="backup-vault", time="02:00")
    before = stack_cli.toml()

    assert backup(disk="offsite", time="04:00") == {"ready": True}
    assert stack_cli.toml() == before


# ── In a terminal ────────────────────────────────────────────────────────

def test_in_a_terminal_the_passphrase_is_read_without_echo(stack_cli, mac, monkeypatch):
    import getpass
    mac.encrypted(True)
    typed = iter(["", "22:15"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(typed))
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: PASSPHRASE)

    ran = stack_cli("configure", "backup", terminal=True)

    assert ran.code == 0, ran.err
    assert "Mounted now: backup-vault (apfs), USB STICK (msdos)" in ran.out
    assert "is APFS-encrypted" in ran.out
    assert mac.keychain() == {UUID: PASSPHRASE}
    assert 'schedule = "15 22 * * *"' in stack_cli.toml()
    assert PASSPHRASE not in ran.out + ran.err
