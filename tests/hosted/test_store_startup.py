import pytest

from receipt_sentinel.hosted import azure_table_stores
from receipt_sentinel.hosted.azure_table_stores import TableLinkStore, TableSpendStore


class OfflineService:
    """A Table service that is unreachable: any network call raises."""

    def create_table_if_not_exists(self, name: str) -> object:
        raise ConnectionError("table storage unreachable")

    def get_table_client(self, name: str) -> str:
        return name


def test_stores_start_without_touching_the_network() -> None:
    # A cold start during a storage blip must give free mode, not a dead site
    TableLinkStore(OfflineService())  # type: ignore[arg-type]
    TableSpendStore(OfflineService())  # type: ignore[arg-type]


def test_table_client_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class Recorder:
        def __init__(self, endpoint: str, **kwargs: object) -> None:
            seen.update(kwargs)

    monkeypatch.setenv("SENTINEL_TABLES_ENDPOINT", "https://x.table.core.windows.net")
    monkeypatch.setattr(azure_table_stores, "TableServiceClient", Recorder)
    monkeypatch.setattr(azure_table_stores, "DefaultAzureCredential", lambda: None)
    azure_table_stores.table_service_from_env()
    assert seen["retry_total"] == 1
    assert seen["connection_timeout"] <= 3 and seen["read_timeout"] <= 3  # type: ignore[operator]
