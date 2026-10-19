from pathlib import Path
from unittest.mock import MagicMock

import anthropic
import pytest

from receipt_sentinel import cli
from receipt_sentinel.cli import main
from receipt_sentinel.evals import eval_commands
from receipt_sentinel.extract.extracted_document import OcrUnavailable


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


def test_cli_record_skip(tmp_path: Path) -> None:
    history = tmp_path / "h.jsonl"
    argv = ["evals", "record-skip", "--history", str(history), "--reason", "rejected key"]
    assert main(argv) == 0
    assert "rejected key" in history.read_text()


def test_cli_reports_missing_tesseract_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_tesseract(*args: object, **kwargs: object) -> None:
        raise OcrUnavailable()

    monkeypatch.setattr(eval_commands, "extract_fixture_cases", no_tesseract)
    assert main(["evals", "run", "--fake", "--out", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "brew install tesseract" in err
    assert "Traceback" not in err
