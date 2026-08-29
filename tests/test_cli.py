from pathlib import Path

from receipt_sentinel.cli import main


def test_cli_evals_run_fake(tmp_path: Path) -> None:
    assert main(["evals", "run", "--fake", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "report.html").exists()
