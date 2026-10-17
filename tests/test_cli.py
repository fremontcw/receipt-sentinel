from pathlib import Path
from unittest.mock import MagicMock

import anthropic
import pytest

from receipt_sentinel import cli
from receipt_sentinel.cli import main


def test_cli_evals_run_fake(tmp_path: Path) -> None:
    assert main(["evals", "run", "--fake", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "report.html").exists()


def test_cli_compare_models_fake(tmp_path: Path) -> None:
    assert main(["evals", "compare-models", "--fake", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "report.html").exists()


def test_cli_rejected_request_exits_two_with_readable_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def rejected(*args: object, **kwargs: object) -> int:
        raise anthropic.BadRequestError(
            message="API key is not scoped to a workspace",
            response=MagicMock(status_code=400),
            body=None,
        )

    monkeypatch.setattr(cli, "command_evals_run", rejected)
    # Exit 2, not 1: CI must tell a broken setup apart from a CRITICAL regression.
    assert main(["evals", "run", "--out", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "HTTP 400" in err and "not scoped to a workspace" in err
    assert "Traceback" not in err
