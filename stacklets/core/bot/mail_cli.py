"""stack core mail — IMAP diagnostics (runs inside stack-core-bot-runner).

The host-side `stack core mail` docker-execs this. The container already has
the rendered `MAIL_ACCOUNTS_JSON` (the same config the mail bot reads) and
`stack.mail_fetcher`, so the host CLI stays stdlib-only.

For each configured account it logs in and lists the server's real folder
names + flags — which often differ from the webmail labels (Gmail's
`[Gmail]/All Mail`, a localized `Gesendet`, nested paths) — and counts the
configured folder, so the admin can confirm the `folder` value in
stack.toml [[mail.accounts]] points where they think.

    python mail_cli.py                 # all configured accounts
    python mail_cli.py --account work  # just one
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "/app")  # stack.mail_fetcher

from stack.mail_fetcher import MailFetcher, account_from_entry  # noqa: E402


def _mask_secret(secret: str) -> str:
    """A safe preview of a credential, to confirm the secret handed over.

    Empty is called out loudly — that is the tell that the secret-store key
    didn't match the account name (the rendered password is blank, which then
    looks like a wrong password). Otherwise show first/last 2 chars + length,
    enough to eyeball against the password manager without printing it. Short
    secrets show length only, so a weak one isn't half-revealed.
    """
    if not secret:
        return "(EMPTY — secret did not hand over; check the .stack/secrets.toml key)"
    n = len(secret)
    if n < 8:
        return f"(set, {n} chars — too short to preview safely)"
    return f"{secret[:2]}***{secret[-2:]}  ({n} chars)"


def _configured_accounts() -> list:
    """Accounts parsed from MAIL_ACCOUNTS_JSON (the rendered env)."""
    raw = os.environ.get("MAIL_ACCOUNTS_JSON", "").strip()
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"MAIL_ACCOUNTS_JSON is not valid JSON: {e}", file=sys.stderr)
        return []
    accounts = []
    for entry in entries if isinstance(entries, list) else []:
        acc = account_from_entry(entry)
        if acc:
            accounts.append(acc)
        else:
            print(
                f"  ! skipping incomplete account: {entry.get('name') or entry}",
                file=sys.stderr,
            )
    return accounts


def main(argv: list[str]) -> int:
    want = None
    if "--account" in argv:
        i = argv.index("--account")
        want = argv[i + 1] if i + 1 < len(argv) else None

    accounts = _configured_accounts()
    if want is not None:
        accounts = [a for a in accounts if a.name == want]

    if not accounts:
        target = f" named '{want}'" if want else ""
        print(
            f"No mail account{target} configured. Add one under "
            "stack.toml [[mail.accounts]].",
            file=sys.stderr,
        )
        return 1

    rc = 0
    for a in accounts:
        security = "SSL" if a.ssl else "plaintext"
        print(f"\n● {a.name}: {a.user} @ {a.host}:{a.port} ({security})")
        print(f"  credential: {_mask_secret(a.password)}")
        try:
            info = MailFetcher(a).probe()
        except Exception as e:  # noqa: BLE001 — surface any IMAP/socket error
            print(f"  ✗ connection failed: {e}")
            rc = 1
            continue
        count = info["count"]
        count_s = f"{count} messages" if count is not None else "could not select"
        print(f"  ✓ connected — folder '{info['folder']}': {count_s}")
        folders = info["folders"]
        print(f"  folders ({len(folders)}):")
        for flags, name in folders:
            tag = f"   ({flags})" if flags else ""
            print(f"    - {name}{tag}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
