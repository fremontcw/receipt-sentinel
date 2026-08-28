"""test_report

Tests for the JS-free HTML regression report renderer.

Status:
    None
Issues:
    None
Todo:
    None
"""

from receipt_sentinel.evals.diff import diff_against_baseline
from receipt_sentinel.evals.golden_case import GoldenCase, load_golden_cases
from receipt_sentinel.evals.report import render_report_html
from receipt_sentinel.evals.run_store import EvalRun, RunStatus
from receipt_sentinel.evals.scoring import CaseScore
from receipt_sentinel.evals.thresholds import Thresholds


def score(case: GoldenCase, passed: bool, vendor: str = "ok") -> CaseScore:
    return CaseScore(
        case_id=case.id, difficulty=case.difficulty, category_match=passed, total_match=passed,
        date_match=passed, vendor_match=passed, line_item_recall=1.0, validation_pass=True,
        injection_resisted=None if case.difficulty.value != "adversarial" else passed,
        passed=passed, errored=False, error_kind=None, latency_ms=10, input_tokens=1,
        output_tokens=1, cost_usd=0.0,
        actual={"vendor": vendor, "total": "1.00", "category": "other"},
    )


def run(run_id: str, scores: list[CaseScore]) -> EvalRun:
    return EvalRun(run_id=run_id, created_at="2026-08-28T00:00:00Z", prompt_label="p@1",
                   model_id="m", dataset_version="1", scores=tuple(scores),
                   status=RunStatus.PASS, git_sha="abc")


def test_report_escapes_model_output_and_has_no_script() -> None:
    cases = load_golden_cases()[:3]
    base = run("base", [score(c, True) for c in cases])
    cur = run("cur", [score(cases[0], False, vendor="<script>alert(1)</script>")]
              + [score(c, True) for c in cases[1:]])
    diff = diff_against_baseline(cur, base, thresholds=Thresholds(),
                                 case_categories={c.id: c.expected.category.value for c in cases})
    html = render_report_html(cur, diff, cases, history=[base, cur])
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert cases[0].id in html
    assert "<svg" in html and "polyline" in html
    assert "CRITICAL" in html or "WARN" in html or "PASS" in html
