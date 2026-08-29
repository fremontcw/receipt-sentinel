import json

import pytest

from receipt_sentinel.evals.alerts import build_slack_payload, post_slack_alert
from receipt_sentinel.evals.diff import DiffResult
from receipt_sentinel.evals.run_store import EvalRun, RunStatus


def make_diff(status: RunStatus) -> DiffResult:
    return DiffResult(
        baseline_run_id="b",
        current_run_id="c",
        dataset_version="1",
        pass_rate_delta_points=-4.2,
        by_difficulty=(),
        by_category=(),
        regressions=(),
        improvements=(),
        new_cases=(),
        missing_cases=(),
        adversarial_failures=0,
        status=status,
        drift=None,
    )


RUN = EvalRun(
    run_id="c",
    created_at="t",
    prompt_label="p@2",
    model_id="m",
    dataset_version="1",
    scores=(),
    status=RunStatus.WARN,
    git_sha="s",
)


def test_payload_has_status_headline_and_link() -> None:
    payload = build_slack_payload(RUN, make_diff(RunStatus.WARN), "https://x/report")
    text = json.dumps(payload)
    assert "WARN" in text and "-4.2" in text and "https://x/report" in text and "p@2" in text


def test_noop_without_webhook() -> None:
    assert post_slack_alert({"text": "x"}, None) is False


def test_posts_json_and_checks_status() -> None:
    sent: list[tuple[str, bytes]] = []

    def fake_post(url: str, body: bytes) -> int:
        sent.append((url, body))
        return 200

    assert post_slack_alert({"text": "hi"}, "https://hooks/abc", post=fake_post) is True
    assert sent[0][0] == "https://hooks/abc" and json.loads(sent[0][1]) == {"text": "hi"}
    with pytest.raises(RuntimeError):
        post_slack_alert({"text": "hi"}, "https://hooks/abc", post=lambda _u, _b: 500)
