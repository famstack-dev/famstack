"""The dashboard's host API stand-in answers requests that arrive at once.

The setup page asks the host API for the listing, the AI's plan and the
AI's last answer in parallel, each in a thread of its own. The real host
API (`stacklets/core/famstack-api.py`) runs each request as a `./stack`
process of its own, so no request sees another's state. The stand-in
(`tests/dashboard_host.py`) runs them in the test's process, where a
stacklet's command puts its folder on `sys.path` and removes the modules it
loaded when it ends (`stack.hooks.stacklet_imports`). Two commands at once
then removed each other's modules while they loaded, and one of them
answered with an error such as `'stacklets.ai.cli.check'`: under load, the
setup page then showed the AI step as not asked instead of ready.
"""

from __future__ import annotations

import threading

import pytest

from stack.ai import kept_answer
from tests.dashboard_host import DashboardHost

# The listing asks Docker which containers run; on this Mac none does.
pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

# A server elsewhere, written into `stack.toml` only; nothing connects to it.
AI = {"provider": "external", "openai_url": "http://192.168.1.30:11434/v1",
      "openai_key": "ollama-key-of-the-simpsons", "default": "qwen3:8b"}
# The page's three reads of the AI step, as `server.py` asks them at once.
READS = ("list", "ai_recommended", "ai_answer")
# Rounds of the three reads. Before the stand-in answered one request at a
# time, about one round in three had an error, so a pass by chance is
# unlikely (0.7^20, about 0.1 %). Each round runs three commands, about
# 1.5 s in all; a fixed count, because the race shows only by repeating.
ROUNDS = 20


def _instance(tmp_path):
    """The Simpsons' instance at the services step, with a server elsewhere
    that answered the stack's question."""
    root = tmp_path / "instance"
    root.mkdir()
    ai = "".join(f'{key} = "{value}"\n' for key, value in AI.items())
    (root / "stack.toml").write_text(
        f'[core]\ndata_dir = "{root / "data"}"\nhost = "localhost"\nprofile = "small"\n'
        f'extension_dirs = ["{root / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n'
        f"\n[ai]\n{ai}")
    kept_answer.keep(root, AI, {"ok": True, "model": AI["default"], "address": AI["openai_url"]})
    return DashboardHost.in_state("services", root=root)


def test_reads_that_arrive_at_once_each_get_their_answer(tmp_path):
    """Given the AI step's page, when it asks for the listing, the AI's plan
    and the AI's last answer at once, then each request gets its own
    answer and none an error, in every round."""
    host = _instance(tmp_path)
    errors: list[str] = []

    for _ in range(ROUNDS):
        start = threading.Barrier(len(READS))

        def read(cmd: str) -> None:
            start.wait()
            [answer] = host.answer({"cmd": cmd})
            if "error" in answer:
                errors.append(f"{cmd}: {answer['error']}")

        threads = [threading.Thread(target=read, args=(cmd,)) for cmd in READS]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert errors == []
