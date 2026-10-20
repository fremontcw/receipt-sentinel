"""azure_table_stores

LinkStore and SpendStore on Azure Table Storage. The app reaches the account with its
managed identity (DefaultAzureCredential); there is no connection string or key.
Datetimes are stored as ISO-8601 UTC strings; ETags give the conditional writes that
stop two requests spending the same call.

Status:
    None
Issues:
    None
Todo:
    None
"""

import os
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
)
from azure.data.tables import TableClient, TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential

from receipt_sentinel.hosted.demo_links import DemoLink

TABLES_ENDPOINT_ENV_VAR = "SENTINEL_TABLES_ENDPOINT"
_LINK_PARTITION = "link"
_SPEND_PARTITION = "spend"
_SPEND_ATTEMPTS = 5
STORE_TIMEOUT_SECONDS = 2


def table_service_from_env() -> TableServiceClient | None:
    """The Table service at SENTINEL_TABLES_ENDPOINT via managed identity, or None if unset."""
    endpoint = os.environ.get(TABLES_ENDPOINT_ENV_VAR)
    if not endpoint:
        return None
    # Fail fast: a slow store should drop visitors to free mode, not hold their requests
    return TableServiceClient(
        endpoint,
        credential=DefaultAzureCredential(),
        retry_total=1,
        connection_timeout=STORE_TIMEOUT_SECONDS,
        read_timeout=STORE_TIMEOUT_SECONDS,
    )


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _parse(value: str) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class TableLinkStore:
    """LinkStore in table `demolinks`: PartitionKey "link", RowKey = token SHA-256."""

    def __init__(self, service: TableServiceClient, table_prefix: str = "") -> None:
        # Bicep creates the table; touching the network here would turn a storage blip
        # during a cold start into a dead site instead of free mode
        self._table: TableClient = service.get_table_client(f"{table_prefix}demolinks")

    def add(self, link: DemoLink) -> None:
        self._table.create_entity(self._entity(link))

    def get(self, token_sha256: str) -> DemoLink | None:
        try:
            entity = self._table.get_entity(_LINK_PARTITION, token_sha256)
        except ResourceNotFoundError:
            return None
        return self._link(entity)

    def list_all(self) -> list[DemoLink]:
        query = self._table.query_entities(
            "PartitionKey eq @pk", parameters={"pk": _LINK_PARTITION}
        )
        return [self._link(entity) for entity in query]

    def replace_if_unchanged(self, link: DemoLink, etag: str) -> bool:
        try:
            self._table.update_entity(
                self._entity(link),
                mode=UpdateMode.REPLACE,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except (ResourceModifiedError, ResourceNotFoundError):
            return False
        return True

    def revoke(self, link_id_or_label: str) -> bool:
        for link in self.list_all():
            if link_id_or_label in (link.link_id, link.label):
                return self.replace_if_unchanged(replace(link, revoked=True), link.etag)
        return False

    @staticmethod
    def _entity(link: DemoLink) -> dict[str, Any]:
        return {
            "PartitionKey": _LINK_PARTITION,
            "RowKey": link.token_sha256,
            "link_id": link.link_id,
            "label": link.label,
            "created_at": _iso(link.created_at),
            "expires_at": _iso(link.expires_at),
            "calls_used": link.calls_used,
            "call_cap": link.call_cap,
            "first_used_at": _iso(link.first_used_at),
            "revoked": link.revoked,
        }

    @staticmethod
    def _link(entity: Any) -> DemoLink:
        created_at, expires_at = _parse(entity["created_at"]), _parse(entity["expires_at"])
        assert created_at is not None and expires_at is not None  # noqa: S101 - written by _entity
        return DemoLink(
            link_id=entity["link_id"],
            label=entity["label"],
            token_sha256=entity["RowKey"],
            created_at=created_at,
            expires_at=expires_at,
            calls_used=int(entity["calls_used"]),
            call_cap=int(entity["call_cap"]),
            first_used_at=_parse(entity["first_used_at"]),
            revoked=bool(entity["revoked"]),
            etag=entity.metadata["etag"],
        )


class TableSpendStore:
    """SpendStore in table `demospend`: PartitionKey "spend", RowKey = month."""

    def __init__(self, service: TableServiceClient, table_prefix: str = "") -> None:
        self._table: TableClient = service.get_table_client(f"{table_prefix}demospend")

    def spent_usd(self, month: str) -> float:
        row = self._row(month)
        return float(row["usd"]) if row is not None else 0.0

    def add_usd(self, month: str, amount: float) -> float:
        written = self._update(month, lambda row: {"usd": round(row["usd"] + amount, 6)})
        assert written is not None  # noqa: S101 - the change always writes
        return float(written["usd"])

    def mark_cap_alert_sent(self, month: str) -> bool:
        written = self._update(
            month, lambda row: None if row["cap_alert_sent"] else {"cap_alert_sent": True}
        )
        return written is not None

    def _row(self, month: str) -> Any:
        try:
            return self._table.get_entity(_SPEND_PARTITION, month)
        except ResourceNotFoundError:
            return None

    def _update(
        self, month: str, change: Callable[[dict[str, Any]], dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        """Read-modify-write one month's row with ETag retries.

        Returns:
            The written values, or None when `change` returned None (nothing to write).
        """
        for _ in range(_SPEND_ATTEMPTS):
            row = self._row(month)
            current: dict[str, Any] = {"usd": 0.0, "cap_alert_sent": False}
            if row is not None:
                current = {"usd": float(row["usd"]), "cap_alert_sent": bool(row["cap_alert_sent"])}
            changes = change(current)
            if changes is None:
                return None
            entity = {"PartitionKey": _SPEND_PARTITION, "RowKey": month, **current, **changes}
            try:
                if row is None:
                    self._table.create_entity(entity)
                else:
                    self._table.update_entity(
                        entity,
                        mode=UpdateMode.REPLACE,
                        etag=row.metadata["etag"],
                        match_condition=MatchConditions.IfNotModified,
                    )
            except (ResourceExistsError, ResourceModifiedError):
                continue
            return entity
        raise RuntimeError(f"demospend {month}: too many concurrent writers")
