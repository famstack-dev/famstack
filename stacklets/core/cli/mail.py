"""stack core mail — test IMAP configuration and list mailbox folders.

For each account in `stack.toml [[mail.accounts]]`, log in and list the
server's real folder names (often different from the webmail labels — Gmail's
`[Gmail]/All Mail`, a localized `Gesendet`, nested paths) with their flags,
and count the configured folder. Use it when setting up mail to confirm the
`folder` value points where you expect.

    stack core mail                 # all configured accounts
    stack core mail --account work  # just one

Read-only: it logs in and lists folders, never marks mail read or changes
anything. Runs inside stack-core-bot-runner (the host wrapper is a thin
docker-exec); passwords stay in the container env, never on the host CLI.
"""

HELP = "Test IMAP config and list mailbox folders"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402

_MAIL_CLI = "/stacklets/core/bot/mail_cli.py"


def run(args, stacklet, config):
    return dispatch(_MAIL_CLI, *sys.argv[3:])
