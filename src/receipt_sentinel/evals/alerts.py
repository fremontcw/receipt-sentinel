"""alerts

Slack incoming-webhook notification for eval runs. No-op when the webhook is unset.

Status:
    None
Issues:
    None
Todo:
    None
"""

import json
import urllib.request
from collections.abc import Callable

from receipt_sentinel.evals.diff import DiffResult
from receipt_sentinel.evals.run_store import EvalRun

STATUS_EMOJI = {"PASS": "✅", "WARN": "⚠️", "CRITICAL": "🛑", "PROVIDER_UNSTABLE": "🌩️"}


def build_slack_payload(
    current: EvalRun, diff: DiffResult, report_url: str | None
) -> dict[str, object]:
    emoji = STATUS_EMOJI[diff.status.value]
    lines = [
        f"{emoji} *Receipt Sentinel evals: {diff.status.value}*",
        f"prompt `{current.prompt_label}` · model `{current.model_id}` · "
        f"dataset v{current.dataset_version}",
        f"pass rate {current.pass_rate:.1%} ({diff.pass_rate_delta_points:+.1f} pts "
        f"vs baseline)",
        f"regressions {len(diff.regressions)} · improvements {len(diff.improvements)} · "
        f"adversarial failures {diff.adversarial_failures}",
        f"cost ${current.total_cost_usd:.3f} · p50 {current.p50_latency_ms} ms",
    ]
    if diff.drift and diff.drift.drifting:
        lines.append(
            f"drift: {diff.drift.gap_points:.1f} pts below baseline over "
            f"{diff.drift.window_runs} runs"
        )
    if report_url:
        lines.append(f"<{report_url}|Open report>")
    return {"text": "\n".join(lines)}


def _urllib_post(url: str, body: bytes) -> int:
    if not url.startswith("https://"):
        raise RuntimeError("Slack webhook must be https")
    request = urllib.request.Request(  # noqa: S310
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - https enforced above
        return int(response.status)


def post_slack_alert(
    payload: dict[str, object],
    webhook_url: str | None,
    *,
    post: Callable[[str, bytes], int] | None = None,
) -> bool:
    if not webhook_url:
        return False
    status = (post or _urllib_post)(webhook_url, json.dumps(payload).encode())
    if not 200 <= status < 300:
        raise RuntimeError(f"Slack webhook returned {status}")
    return True
