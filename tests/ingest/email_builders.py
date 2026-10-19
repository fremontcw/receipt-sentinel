"""Raw RFC 822 bytes for ingest tests, with the From and auth headers written verbatim."""

from email.message import EmailMessage


def build_eml(
    from_header: str,
    *,
    subject: str = "Your receipt",
    date: str | None = "Mon, 17 Aug 2026 10:00:00 +0000",
    attachments: list[tuple[str, bytes]] | None = None,
    auth_results: list[str] | None = None,
    body: str = "Thanks for your purchase.",
) -> bytes:
    """Headers are written as given (so tests can forge or garble them); the MIME body is
    built by the stdlib. auth_results are written top-to-bottom in the order given."""
    mime = EmailMessage()
    mime.set_content(body)
    for filename, data in attachments or []:
        mime.add_attachment(data, maintype="application", subtype="octet-stream", filename=filename)
    lines = [f"Authentication-Results: {value}" for value in auth_results or []]
    lines += [f"From: {from_header}", f"Subject: {subject}"]
    if date is not None:
        lines.append(f"Date: {date}")
    return ("\r\n".join(lines) + "\r\n").encode() + mime.as_bytes()


def gmail_dmarc_pass(domain: str) -> str:
    """The Authentication-Results line Gmail's receiving server adds for a DMARC pass."""
    return (
        f"mx.google.com; dkim=pass header.i=@{domain}; dmarc=pass (p=REJECT) header.from={domain}"
    )
