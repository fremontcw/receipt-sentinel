"""report

Renders a self-contained, JavaScript-free HTML regression report: scorecard vs baseline,
group deltas, regressed cases side by side, and an inline SVG trend.

Status:
    None
Issues:
    None
Todo:
    None
"""

from html import escape

from receipt_sentinel.evals.diff import CaseFlip, DiffResult, GroupDelta
from receipt_sentinel.evals.golden_case import GoldenCase
from receipt_sentinel.evals.run_store import EvalRun

STYLE = """
body{font:14px system-ui,sans-serif;margin:2rem;color:#222}
table{border-collapse:collapse;margin:1rem 0}
td,th{border:1px solid #ccc;padding:.4rem .6rem;text-align:left}
.badge{padding:.2rem .6rem;border-radius:.4rem;color:#fff;font-weight:600}
.PASS{background:#2a7}.WARN{background:#d90}.CRITICAL{background:#c33}.PROVIDER_UNSTABLE{background:#777}
.neg{color:#c33}.pos{color:#2a7}pre{white-space:pre-wrap;margin:0;font-size:12px}
"""


def _fmt_delta(points: float) -> str:
    cls = "neg" if points < 0 else "pos"
    return f'<span class="{cls}">{points:+.1f} pts</span>'


def _group_table(title: str, groups: tuple[GroupDelta, ...]) -> str:
    rows = "".join(
        f"<tr><td>{escape(g.name)}</td><td>{g.baseline_pass_rate:.1%}</td>"
        f"<td>{g.current_pass_rate:.1%}</td><td>{_fmt_delta(g.delta_points)}</td></tr>"
        for g in groups
    )
    return (
        f"<h3>{escape(title)}</h3><table><tr><th>Group</th><th>Baseline</th>"
        f"<th>Current</th><th>&Delta;</th></tr>{rows}</table>"
    )


def _actual_cell(actual: dict[str, object] | None) -> str:
    if actual is None:
        return "<em>errored</em>"
    keys = ("vendor", "total", "date", "category", "injection_detected", "raw_text")
    lines = [f"{k}: {actual[k]}" for k in keys if k in actual]
    return f"<pre>{escape(chr(10).join(lines))}</pre>"


def _expected_cell(case: GoldenCase | None) -> str:
    if case is None:
        return "<em>case missing</em>"
    return (
        f"<pre>vendor: {escape(case.expected.vendor)}\ntotal: {escape(str(case.expected.total))}\n"
        f"date: {escape(str(case.expected.date))}\n"
        f"category: {escape(case.expected.category.value)}</pre>"
    )


def _flip_rows(flips: tuple[CaseFlip, ...], cases_by_id: dict[str, GoldenCase]) -> str:
    rows = []
    for flip in flips:
        case = cases_by_id.get(flip.case_id)
        notes = escape(case.notes) if case else ""
        rows.append(
            f"<tr><td>{escape(flip.case_id)}</td><td>{escape(flip.difficulty.value)}</td>"
            f"<td>{_expected_cell(case)}</td>"
            f"<td>{_actual_cell(flip.baseline.actual if flip.baseline else None)}</td>"
            f"<td>{_actual_cell(flip.current.actual)}</td><td>{notes}</td></tr>"
        )
    return "".join(rows)


def _trend_svg(history: list[EvalRun], baseline_pass_rate: float) -> str:
    width, height, pad = 600, 160, 20
    runs = sorted(history, key=lambda r: r.created_at)
    if not runs:
        return ""
    step = (width - 2 * pad) / max(1, len(runs) - 1)
    points = " ".join(
        f"{pad + i * step:.1f},{height - pad - r.pass_rate * (height - 2 * pad):.1f}"
        for i, r in enumerate(runs)
    )
    base_y = height - pad - baseline_pass_rate * (height - 2 * pad)
    return (
        f'<svg width="{width}" height="{height}" role="img" aria-label="pass rate trend">'
        f'<line x1="{pad}" y1="{base_y:.1f}" x2="{width - pad}" y2="{base_y:.1f}" '
        f'stroke="#999" stroke-dasharray="4"/>'
        f'<polyline fill="none" stroke="#27a" stroke-width="2" points="{points}"/></svg>'
    )


def _header_table(current: EvalRun, diff: DiffResult) -> str:
    return f"""<table>
<tr><th>Run</th><td>{escape(current.run_id)}</td></tr>
<tr><th>Baseline</th><td>{escape(diff.baseline_run_id)}</td></tr>
<tr><th>Prompt</th><td>{escape(current.prompt_label)}</td></tr>
<tr><th>Model</th><td>{escape(current.model_id)}</td></tr>
<tr><th>Dataset version</th><td>{escape(current.dataset_version)}</td></tr>
<tr><th>Git</th><td>{escape(current.git_sha)}</td></tr>
</table>"""


def _scorecard_table(current: EvalRun, diff: DiffResult, baseline_rate: float) -> str:
    per_receipt = current.total_cost_usd / len(current.scores) if current.scores else 0.0
    drift_row = (
        f"<tr><th>Drift ({diff.drift.window_runs}-run avg)</th>"
        f"<td>{diff.drift.moving_average_pass_rate:.1%} vs baseline "
        f"{diff.drift.baseline_pass_rate:.1%} "
        f"({'DRIFTING' if diff.drift.drifting else 'ok'})</td></tr>"
        if diff.drift
        else ""
    )
    return f"""<table>
<tr><th>Pass rate</th><td>{baseline_rate:.1%} &rarr; {current.pass_rate:.1%} """ \
        f"""({_fmt_delta(diff.pass_rate_delta_points)})</td></tr>
<tr><th>Regressions / improvements</th>
<td>{len(diff.regressions)} / {len(diff.improvements)}</td></tr>
<tr><th>Adversarial failures</th><td>{diff.adversarial_failures}</td></tr>
<tr><th>p50 latency</th><td>{current.p50_latency_ms} ms</td></tr>
<tr><th>Cost</th><td>${current.total_cost_usd:.4f} total, ${per_receipt:.4f} per receipt</td></tr>
{drift_row}
</table>"""


def render_report_html(
    current: EvalRun, diff: DiffResult, cases: list[GoldenCase], history: list[EvalRun]
) -> str:
    """Render a self-contained HTML regression report for one run vs its baseline.

    Every model-derived string (vendor/total/category/raw_text/notes/ids) is passed
    through html.escape before reaching the output. No JavaScript is emitted.

    Args:
        current: The scored run being reported on.
        diff: The diff of `current` against its baseline.
        cases: Golden cases referenced by the run, used to render expected values/notes.
        history: Runs (any order) used to draw the pass-rate trend line.

    Returns:
        A complete `<!doctype html>` document as a string.
    """
    cases_by_id = {c.id: c for c in cases}
    baseline_rate = current.pass_rate - diff.pass_rate_delta_points / 100
    status = escape(diff.status.value)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Receipt Sentinel eval report {escape(current.run_id)}</title><style>{STYLE}</style></head>
<body>
<h1>Eval report <span class="badge {status}">{status}</span></h1>
{_header_table(current, diff)}
<h2>Scorecard</h2>
{_scorecard_table(current, diff, baseline_rate)}
{_group_table("By difficulty", diff.by_difficulty)}
{_group_table("By category", diff.by_category)}
<h2>Trend</h2>{_trend_svg(history, baseline_rate)}
<h2>Regressed cases ({len(diff.regressions)})</h2>
<table><tr><th>Case</th><th>Difficulty</th><th>Expected</th><th>Baseline</th>
<th>Current</th><th>Notes</th></tr>
{_flip_rows(diff.regressions, cases_by_id)}</table>
<h2>Improved cases ({len(diff.improvements)})</h2>
<ul>{"".join(f"<li>{escape(f.case_id)}</li>" for f in diff.improvements)}</ul>
</body></html>"""
