"""The one-shot sender's device: one per account, and one send at a time.

Two processes on one key store corrupt it, so two sends as the same
account take turns: the second waits until the first has finished. The
device shows up in a person's device list under a name that says what it
is. The send itself (login, keys, encryption) runs against a real Synapse
on the rig.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))

from stack.matrix.send import device_names, exclusive  # noqa: E402

# A second process taking the same account's lock, as a second send would.
SECOND_SEND = (
    "import sys; from pathlib import Path; from stack.matrix.send import exclusive\n"
    "with exclusive(Path(sys.argv[1])): print('sent')\n"
)


def _second_send(lock: Path, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", SECOND_SEND, str(lock)],
                          capture_output=True, text=True, timeout=timeout,
                          env={"PYTHONPATH": str(_REPO / "lib")})


def test_a_second_send_as_the_same_account_waits_for_the_first(tmp_path):
    lock = tmp_path / "stacky-bot-cli.lock"

    with exclusive(lock):
        with pytest.raises(subprocess.TimeoutExpired):
            _second_send(lock, timeout=1.5)

    assert _second_send(lock, timeout=10).stdout.strip() == "sent"


def test_the_device_is_named_after_its_account():
    assert device_names("@stacky-bot:simpson") == ("stacky-bot-cli", "stacky-bot (command line)")
