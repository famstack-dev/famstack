"""`resolve_login`: the shared `--as <account>` lookup of `stack messages send`,
`upload` and `react`.

No `--as` is the system account. `--as agent` is the agent under whatever
name the instance gave it, `--as <id>-bot` a bot, and any other name a
family member. Where an account's devices keep their state follows from
it: a bot's stacklet, `messages` for a person.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "cli"))

from _matrix import Account, resolve_login  # noqa: E402


class TestResolveLogin:
    def test_family_member_reads_their_password(self):
        secrets = {"global__USER_MARGE_PASSWORD": "s3cret"}
        assert resolve_login("marge", secrets) == (
            Account("marge", "s3cret", "messages", "Marge", is_person=True), None)

    def test_missing_family_password_is_an_error(self):
        account, err = resolve_login("homer", {})
        assert account is None
        assert "homer" in err and "USER_HOMER_PASSWORD" in err

    def test_default_sender_is_stacker_bot(self):
        secrets = {"core__STACKER_BOT_PASSWORD": "botpass"}
        assert resolve_login(None, secrets) == (
            Account("stacker-bot", "botpass", "core", "stacker-bot"), None)

    def test_no_bot_password_is_an_error(self):
        account, err = resolve_login(None, {})
        assert account is None and "stacker-bot" in err


class TestTheAgentAndTheBots:
    def test_the_agent_is_found_under_the_name_the_instance_gave_it(self):
        secrets = {"agent__AGENT_BOT_PASSWORD": "agentpass"}
        stack_cfg = {"agent": {"name": "Merlin"}}

        assert resolve_login("agent", secrets, stack_cfg) == (
            Account("merlin-bot", "agentpass", "agent", "Merlin"), None)
        assert resolve_login("merlin-bot", secrets, stack_cfg)[0] == \
            resolve_login("agent", secrets, stack_cfg)[0]

    def test_the_agent_is_stacky_when_unnamed(self):
        secrets = {"agent__AGENT_BOT_PASSWORD": "agentpass"}
        account, _ = resolve_login("agent", secrets)
        assert account is not None and account.login == "stacky-bot"
        assert account.label == "Stacky"

    def test_a_bot_is_found_in_the_stacklet_that_holds_its_password(self):
        secrets = {"docs__ARCHIVIST_BOT_PASSWORD": "archpass",
                   "global__USER_HOMER_PASSWORD": "homer"}

        assert resolve_login("archivist-bot", secrets) == (
            Account("archivist-bot", "archpass", "docs", "archivist-bot"), None)

    def test_an_unknown_bot_is_an_error_not_a_person(self):
        account, err = resolve_login("kettle-bot", {"global__USER_KETTLE-BOT_PASSWORD": "x"})
        assert account is None and "kettle-bot" in err
