from receipt_sentinel.cli import main


def test_cli_main_returns_zero() -> None:
    assert main() == 0
