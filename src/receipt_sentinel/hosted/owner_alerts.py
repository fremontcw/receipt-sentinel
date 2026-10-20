"""owner_alerts

Emails the author when a personal link first uses the real model, and when the monthly
cap is reached (spec §7). Azure Communication Services Email, signed in with the app's
managed identity. A failed email is logged and never reaches the visitor.

Status:
    None
Issues:
    None
Todo:
    None
"""

import logging
import os
from datetime import datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

ACS_ENDPOINT_ENV_VAR = "SENTINEL_ACS_ENDPOINT"
ALERT_FROM_ENV_VAR = "SENTINEL_ALERT_FROM"
ALERT_TO_ENV_VAR = "SENTINEL_ALERT_TO"


class OwnerAlerts(Protocol):
    def link_first_used(self, label: str, when: datetime) -> None: ...

    def monthly_cap_reached(self, month: str, spent_usd: float) -> None: ...


class EmailSender(Protocol):
    """The part of azure.communication.email.EmailClient this module uses."""

    def begin_send(self, message: dict[str, Any]) -> Any: ...


class LogOnlyAlerts:
    """Alerts for local runs: logged, not sent."""

    def link_first_used(self, label: str, when: datetime) -> None:
        logger.info("demo link %r first used the real model at %s", label, when.isoformat())

    def monthly_cap_reached(self, month: str, spent_usd: float) -> None:
        logger.info("monthly real-model cap reached for %s ($%.2f)", month, spent_usd)


class AcsEmailAlerts:
    """Alerts sent as email. Bodies carry the link label and a time, nothing else."""

    def __init__(self, client: EmailSender, *, sender: str, to: str) -> None:
        self._client = client
        self._sender = sender
        self._to = to

    def link_first_used(self, label: str, when: datetime) -> None:
        self._send(
            f'Receipt Sentinel: "{label}" used the real model',
            f'The demo link "{label}" made its first real-model call at {when:%Y-%m-%d %H:%M} UTC.',
        )

    def monthly_cap_reached(self, month: str, spent_usd: float) -> None:
        self._send(
            f"Receipt Sentinel: monthly cap reached ({month})",
            f"Real-model spend for {month} reached ${spent_usd:.2f}. Everyone is on the free "
            "demo until the 1st.",
        )

    def _send(self, subject: str, body: str) -> None:
        message = {
            "senderAddress": self._sender,
            "recipients": {"to": [{"address": self._to}]},
            "content": {"subject": subject, "plainText": body},
        }
        try:
            self._client.begin_send(message)
        except Exception as err:  # noqa: BLE001 - an alert must never break a visitor's request
            logger.error("owner alert email failed (%s): %s", subject, err)


def alerts_from_env() -> OwnerAlerts:
    """Email alerts when all three SENTINEL_ACS_ENDPOINT/_ALERT_FROM/_ALERT_TO are set."""
    endpoint = os.environ.get(ACS_ENDPOINT_ENV_VAR)
    sender = os.environ.get(ALERT_FROM_ENV_VAR)
    to = os.environ.get(ALERT_TO_ENV_VAR)
    if not (endpoint and sender and to):
        return LogOnlyAlerts()
    from azure.communication.email import EmailClient  # noqa: PLC0415 - hosted extra only
    from azure.identity import DefaultAzureCredential  # noqa: PLC0415

    return AcsEmailAlerts(EmailClient(endpoint, DefaultAzureCredential()), sender=sender, to=to)
