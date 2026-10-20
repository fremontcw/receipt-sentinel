"""demo_link_commands

`sentinel demo-link issue|list|revoke`: the author's side of personal links. Runs on the
author's laptop against the deployed Table Storage, signed in with their own Azure login.

Status:
    None
Issues:
    None
Todo:
    None
"""

import os
import sys
from datetime import UTC, datetime

from receipt_sentinel.hosted.demo_links import LinkStore, issue_link, link_state

PUBLIC_URL_ENV_VAR = "SENTINEL_PUBLIC_URL"
EXIT_SETUP_MISSING = 2


def command_demo_link_issue(store: LinkStore, label: str, public_url: str, now: datetime) -> int:
    """Create a link and print it, ready to paste into an email. The token is shown once."""
    label = label.strip()
    if not label:
        print('give the link a label, e.g. "Acme - Jane, eng manager"', file=sys.stderr)
        return 1
    token, link = issue_link(store, label, now)
    print(
        f"{link.label} ({link.link_id}): expires {link.expires_at:%Y-%m-%d}, {link.call_cap} calls"
    )
    print(f"{public_url.rstrip('/')}/?k={token}")
    return 0


def command_demo_link_list(store: LinkStore, now: datetime) -> int:
    """One line per link: label, id, calls used, first used, expiry, state. Never the token."""
    for link in sorted(store.list_all(), key=lambda x: x.created_at):
        first = f"{link.first_used_at:%Y-%m-%d %H:%M}" if link.first_used_at else "never"
        print(
            f"{link.label}  {link.link_id}  {link.calls_used}/{link.call_cap}  "
            f"first used {first}  expires {link.expires_at:%Y-%m-%d}  {link_state(link, now)}"
        )
    return 0


def command_demo_link_revoke(store: LinkStore, label_or_id: str) -> int:
    """Turn a link off by its label or id."""
    if store.revoke(label_or_id):
        print(f"revoked {label_or_id}")
        return 0
    print(f"no link with label or id {label_or_id!r}", file=sys.stderr)
    return 1


def dispatch_demo_link(command: str, value: str | None) -> int:
    """CLI entry: checks the environment, then runs one command against Table Storage."""
    from receipt_sentinel.hosted.azure_table_stores import (  # noqa: PLC0415 - hosted extra
        TABLES_ENDPOINT_ENV_VAR,
        TableLinkStore,
        table_service_from_env,
    )

    required = [TABLES_ENDPOINT_ENV_VAR] + ([PUBLIC_URL_ENV_VAR] if command == "issue" else [])
    if missing := [name for name in required if not os.environ.get(name)]:
        print(f"set {', '.join(missing)} first (see infra/hosted_demo_setup.md)", file=sys.stderr)
        return EXIT_SETUP_MISSING
    service = table_service_from_env()
    assert service is not None  # noqa: S101 - endpoint checked above
    store = TableLinkStore(service)
    now = datetime.now(UTC)
    if command == "issue":
        return command_demo_link_issue(store, value or "", os.environ[PUBLIC_URL_ENV_VAR], now)
    if command == "revoke":
        return command_demo_link_revoke(store, value or "")
    return command_demo_link_list(store, now)
