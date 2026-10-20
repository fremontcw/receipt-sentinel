import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from azure.core.credentials import AzureNamedKeyCredential
from azure.data.tables import TableServiceClient

from receipt_sentinel.hosted.azure_table_stores import TableLinkStore, TableSpendStore
from receipt_sentinel.hosted.demo_links import CALL_CAP, hash_token, issue_link, try_spend_call

AZURITE_TABLES_URL = os.environ.get("AZURITE_TABLES_URL")
pytestmark = pytest.mark.skipif(not AZURITE_TABLES_URL, reason="needs Azurite (AZURITE_TABLES_URL)")
# Azurite's published development account; never a real one
DEV_ACCOUNT = "devstoreaccount1"
DEV_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def service() -> Iterator[TableServiceClient]:
    assert AZURITE_TABLES_URL is not None
    client = TableServiceClient(
        AZURITE_TABLES_URL, credential=AzureNamedKeyCredential(DEV_ACCOUNT, DEV_KEY)
    )
    yield client
    for table in list(client.list_tables()):
        client.delete_table(table.name)


def _prefix() -> str:
    return f"t{uuid.uuid4().hex[:8]}"


def test_table_link_round_trip_keeps_utc(service: TableServiceClient) -> None:
    store = TableLinkStore(service, table_prefix=_prefix())
    token, link = issue_link(store, "Acme - Jane", NOW)
    stored = store.get(hash_token(token))
    assert stored is not None
    assert stored.expires_at == link.expires_at and stored.expires_at.tzinfo is not None
    assert stored.first_used_at is None and stored.label == "Acme - Jane"
    assert [x.link_id for x in store.list_all()] == [link.link_id]
    assert store.revoke(link.link_id)
    assert try_spend_call(store, token, NOW).state == "revoked"


def test_table_last_call_race_lets_exactly_one_through(service: TableServiceClient) -> None:
    store = TableLinkStore(service, table_prefix=_prefix())
    token, _ = issue_link(store, "a", NOW)
    for _ in range(CALL_CAP - 1):
        try_spend_call(store, token, NOW)
    stale = store.get(hash_token(token))
    assert stale is not None
    first = store.replace_if_unchanged(stale, stale.etag)
    second = store.replace_if_unchanged(stale, stale.etag)
    assert (first, second) == (True, False)


def test_table_spend_add_and_month_rows(service: TableServiceClient) -> None:
    store = TableSpendStore(service, table_prefix=_prefix())
    assert store.spent_usd("2026-10") == 0.0
    store.add_usd("2026-10", 0.25)
    assert store.add_usd("2026-10", 0.5) == pytest.approx(0.75)
    assert store.spent_usd("2026-11") == 0.0
    assert store.mark_cap_alert_sent("2026-10")
    assert not store.mark_cap_alert_sent("2026-10")
