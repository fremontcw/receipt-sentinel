"""dashboard

Renders the public, numbers-only run-history page (GitHub Pages) from the nightly history:
story first (what this is and how it works), then last night's status, the pass-rate
trend, and the night-by-night table. Plain HTML + inline SVG, no JavaScript.

Status:
    None
Issues:
    None
Todo:
    None
"""

from html import escape
from pathlib import Path

from receipt_sentinel.evals.report import STYLE
from receipt_sentinel.evals.run_history import (
    HistoryRecord,
    ScoredNight,
    UnscoredNight,
    load_history,
)

HEADLINE = "Receipt Sentinel: catching LLM regressions before users do"
PIPELINE_STEPS = (
    "Labelled receipts",
    "Nightly model run",
    "Diff vs baseline",
    "Issue / blocked PR",
)
TREND_NIGHTS = 30
TABLE_NIGHTS = 90
SAFE_LINK_PREFIX = "https://github.com/"
KNOWN_STATUSES = {"PASS", "WARN", "CRITICAL"}

DASHBOARD_STYLE = STYLE + """
body{max-width:960px;margin:2rem auto;padding:0 16px}
.pipeline{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;margin:1rem 0}
.step{border:1px solid #ccc;border-radius:.4rem;padding:.4rem .6rem}
.status{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;padding:.8rem;
border:1px solid #ddd;border-radius:.5rem}
.NOT_SCORED{background:#777}.drifting{color:#c33;font-weight:600}
.table-wrap{overflow-x:auto}svg{max-width:100%;height:auto}
.muted{color:#666}a{overflow-wrap:anywhere}
@media (max-width:600px){body{margin:1rem auto}td,th{padding:.3rem .4rem}}
"""


def _link(url: str | None, text: str) -> str:
    """Link only to GitHub — anything else (javascript:, other hosts) renders as text."""
    if url and url.startswith(SAFE_LINK_PREFIX):
        return f'<a href="{escape(url)}">{escape(text)}</a>'
    return escape(text)


def _badge(status: str) -> str:
    css = status if status in KNOWN_STATUSES else "NOT_SCORED"
    return f'<span class="badge {css}">{escape(status)}</span>'


def _pipeline_strip() -> str:
    steps = '<span aria-hidden="true">&rarr;</span>'.join(
        f'<span class="step">{escape(s)}</span>' for s in PIPELINE_STEPS
    )
    return f'<div class="pipeline">{steps}</div>'


def _status_bar(newest: HistoryRecord) -> str:
    when = escape(newest.created_at[:16].replace("T", " ")) + " UTC"
    if isinstance(newest, UnscoredNight):
        return (
            f'<div class="status">{_badge("Not scored")}<span>{escape(newest.reason)}</span>'
            f'<span class="muted">{when}</span></div>'
        )
    drift = '<span class="drifting">drifting</span>' if newest.drifting else ""
    return (
        f'<div class="status">{_badge(newest.status)}{drift}'
        f"<span><b>{newest.pass_rate:.1%}</b> of {newest.case_count} receipts correct</span>"
        f'<span class="muted">{escape(newest.model_id)} · {when}</span></div>'
    )


def _trend_svg(scored: list[ScoredNight]) -> str:
    """Pass rate per scored night, a dashed baseline per baseline segment, and a marker
    wherever the baseline changed (drift restarts there)."""
    width, height, pad = 600, 160, 20
    baselines = [n.pass_rate - n.delta_points / 100 for n in scored]
    floor = max(0.0, min([*(n.pass_rate for n in scored), *baselines]) - 0.05)

    def y(rate: float) -> float:
        return height - pad - (rate - floor) / (1 - floor or 1) * (height - 2 * pad)

    step = (width - 2 * pad) / max(1, len(scored) - 1)
    xs = [pad + i * step for i in range(len(scored))]
    parts: list[str] = []
    for i, night in enumerate(scored):
        if i and night.baseline_run_id != scored[i - 1].baseline_run_id:
            parts.append(
                f'<line class="baseline-change" x1="{xs[i]:.1f}" y1="{pad}" x2="{xs[i]:.1f}" '
                f'y2="{height - pad}" stroke="#d90" stroke-width="1"/>'
            )
        end = xs[i + 1] if i + 1 < len(scored) else xs[i] + (step if len(scored) == 1 else 0)
        parts.append(
            f'<line x1="{xs[i]:.1f}" y1="{y(baselines[i]):.1f}" x2="{end:.1f}" '
            f'y2="{y(baselines[i]):.1f}" stroke="#999" stroke-dasharray="4"/>'
        )
    points = " ".join(f"{x:.1f},{y(n.pass_rate):.1f}" for x, n in zip(xs, scored, strict=True))
    parts.append(f'<polyline fill="none" stroke="#27a" stroke-width="2" points="{points}"/>')
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" role="img" '
        f'aria-label="pass rate trend, last {len(scored)} scored nights">{"".join(parts)}</svg>'
    )


def _table_row(record: HistoryRecord) -> str:
    date = _link(record.run_url, record.created_at[:10])
    if isinstance(record, UnscoredNight):
        return f'<tr><td>{date}</td><td colspan="6">not scored: {escape(record.reason)}</td></tr>'
    drift = ' <span class="drifting">drifting</span>' if record.drifting else ""
    return (
        f"<tr><td>{date}</td><td>{escape(record.model_id)}</td><td>{record.pass_rate:.1%}</td>"
        f"<td>{record.delta_points:+.1f}</td><td>{record.regressions}</td>"
        f"<td>${record.cost_usd:.2f}</td><td>{_badge(record.status)}{drift}</td></tr>"
    )


def render_dashboard_html(history: list[HistoryRecord], *, repo_url: str) -> str:
    """Render the public dashboard. Every record string is escaped; links go to GitHub only.

    Args:
        history: Nightly records, oldest first (as `load_history` returns them).
        repo_url: Repository link for the footer.

    Returns:
        A complete `<!doctype html>` document.
    """
    head = (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Receipt Sentinel nightly evals</title><style>{DASHBOARD_STYLE}</style></head>"
        f"<body><h1>{escape(HEADLINE)}</h1>"
        f"<p>Every night an LLM re-reads a hand-labelled set of receipts. Its answers are "
        f"scored and compared with a blessed baseline; a real drop blocks merges and opens "
        f"an issue.</p>{_pipeline_strip()}"
    )
    footer = f'<p class="muted">Source: {_link(repo_url, "GitHub repository")}</p></body></html>'
    if not history:
        return f"{head}<p>No nightly runs yet.</p>{footer}"
    scored = [r for r in history if isinstance(r, ScoredNight)][-TREND_NIGHTS:]
    trend = _trend_svg(scored) if scored else "<p>No scored nights yet.</p>"
    rows = "".join(_table_row(r) for r in reversed(history[-TABLE_NIGHTS:]))
    return (
        f"{head}<h2>Last night</h2>{_status_bar(history[-1])}"
        f"<h2>Pass rate, last {len(scored)} scored nights</h2>{trend}"
        f'<p class="muted">Dashed line: the baseline. Orange marker: a new baseline '
        f"(drift is measured from there).</p>"
        f'<h2>Night by night</h2><div class="table-wrap"><table><tr><th>Night</th>'
        f"<th>Model</th><th>Pass</th><th>&Delta; pts</th><th>Regressions</th><th>Cost</th>"
        f"<th>Status</th></tr>{rows}</table></div>{footer}"
    )


def command_build_dashboard(history_path: Path, out_path: Path, *, repo_url: str) -> int:
    """Write the dashboard page from the history file (missing file = empty page)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    history = load_history(history_path)
    out_path.write_text(render_dashboard_html(history, repo_url=repo_url))
    print(f"dashboard ← {len(history)} nights → {out_path}")
    return 0
