"""build_demo_inbox

Writes examples/demo_inbox/: four synthetic receipt emails plus the allowlist that
`make ingest-demo` uses. Deterministic (fixed MIME boundaries), so re-running rewrites
identical files. Run: uv run python scripts/build_demo_inbox.py

Status:
    None
Issues:
    None
Todo:
    None
"""

import re
from datetime import UTC, datetime, time
from email.message import EmailMessage
from email.utils import format_datetime

from receipt_sentinel.evals.eval_commands import REPO_ROOT
from receipt_sentinel.evals.golden_case import GOLDEN_DIR, load_golden_cases

DEMO_INBOX = REPO_ROOT / "examples" / "demo_inbox"
SHOP = "receipts@demo-coffee.example"
DEMO_ALLOWLIST = f"""\
# Allowlist for `make ingest-demo` only; the real one is config/allowlist.yaml.
addresses: [{SHOP}]
domains: []
trusted_authserv_id: mx.google.com
"""


def _gmail_dmarc_pass(domain: str) -> str:
    return (
        f"mx.google.com; dkim=pass header.i=@{domain}; dmarc=pass (p=REJECT) header.from={domain}"
    )


def _message(
    *,
    from_header: str,
    subject: str,
    sent: datetime,
    attachment: tuple[str, bytes],
    dmarc: str | None,
) -> bytes:
    message = EmailMessage()
    if dmarc:
        message["Authentication-Results"] = dmarc
    message["From"] = from_header
    message["To"] = "me@example.com"
    message["Subject"] = subject
    message["Date"] = format_datetime(sent)
    message.set_content("Thanks for your purchase. Your receipt is attached.")
    message.add_attachment(
        attachment[1], maintype="application", subtype="octet-stream", filename=attachment[0]
    )
    # Fixed boundary: the stdlib default is random, which would rewrite the files every run
    message.set_boundary("demo-" + "-".join(re.findall(r"[a-z0-9]+", subject.lower())))
    return message.as_bytes()


def main() -> int:
    """Write the four demo messages and the demo allowlist."""
    cases = {case.id: case for case in load_golden_cases()}

    def fixture(case_id: str) -> tuple[bytes, datetime]:
        case = cases[case_id]
        assert case.fixture is not None  # noqa: S101 - committed golden data, checked by tests
        sent = datetime.combine(case.email_date, time(9, 30), tzinfo=UTC)
        return (GOLDEN_DIR / case.fixture).read_bytes(), sent

    pdf, pdf_sent = fixture("fixture_pdf_native_0001_dining_easy")
    photo, photo_sent = fixture("fixture_photo_adversarial_0001_override")
    DEMO_INBOX.mkdir(parents=True, exist_ok=True)
    messages = {
        "01_allowlisted_pdf": _message(
            from_header=f"Demo Coffee <{SHOP}>",
            subject="Your receipt",
            sent=pdf_sent,
            attachment=("receipt.pdf", pdf),
            dmarc=_gmail_dmarc_pass("demo-coffee.example"),
        ),
        "02_allowlisted_photo": _message(
            from_header=f"Demo Coffee <{SHOP}>",
            subject="Fuel receipt photo",
            sent=photo_sent,
            attachment=("IMG_2041.jpg", photo),
            dmarc=_gmail_dmarc_pass("demo-coffee.example"),
        ),
        "03_unknown_sender": _message(
            from_header="Unknown Shop <billing@unknown-shop.example>",
            subject="Invoice attached",
            sent=pdf_sent,
            attachment=("invoice.pdf", b"%PDF-1.7 never opened: sender is not allowlisted"),
            dmarc=_gmail_dmarc_pass("unknown-shop.example"),
        ),
        "04_spoofed_display_name": _message(
            from_header=f'"{SHOP}" <attacker@evil.example>',
            subject="Urgent: updated receipt",
            sent=pdf_sent,
            attachment=("receipt.pdf", b"%PDF-1.7 never opened: spoofed display name"),
            dmarc=None,
        ),
    }
    for name, raw in messages.items():
        (DEMO_INBOX / f"{name}.eml").write_bytes(raw)
    (DEMO_INBOX / "allowlist.yaml").write_text(DEMO_ALLOWLIST)
    print(f"wrote {len(messages)} messages to {DEMO_INBOX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
