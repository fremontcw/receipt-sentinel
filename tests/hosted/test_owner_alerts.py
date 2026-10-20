import logging
from datetime import UTC, datetime

import pytest

from receipt_sentinel.hosted.owner_alerts import AcsEmailAlerts, LogOnlyAlerts, alerts_from_env


class FailingEmailClient:
    def begin_send(self, message: object) -> object:
        raise RuntimeError("ACS is down")


def test_acs_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    alerts = AcsEmailAlerts(FailingEmailClient(), sender="a@b.c", to="me@example.com")
    with caplog.at_level(logging.ERROR):
        alerts.link_first_used("Acme - Jane", datetime(2026, 10, 20, tzinfo=UTC))
    assert "ACS is down" in caplog.text


def test_alerts_from_env_without_settings_logs_only(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SENTINEL_ACS_ENDPOINT", "SENTINEL_ALERT_FROM", "SENTINEL_ALERT_TO"):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(alerts_from_env(), LogOnlyAlerts)
