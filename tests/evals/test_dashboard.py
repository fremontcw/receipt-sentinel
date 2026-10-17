from dataclasses import replace

from receipt_sentinel.evals.dashboard import render_dashboard_html
from receipt_sentinel.evals.run_history import HistoryRecord, unscored_night
from tests.evals.test_run_history import night

REPO = "https://github.com/o/r"
HEADLINE = "Receipt Sentinel: catching LLM regressions before users do"


def test_story_first_order() -> None:
    page = render_dashboard_html([night(0.98)], repo_url=REPO)
    positions = [
        page.index(HEADLINE),
        page.index("Nightly model run"),
        page.index('class="badge'),
        page.index("<svg"),
        page.index("<table"),
    ]
    assert positions == sorted(positions)


def test_empty_history_renders_placeholder() -> None:
    page = render_dashboard_html([], repo_url=REPO)
    assert HEADLINE in page and "No nightly runs yet" in page


def test_unscored_newest_shows_reason() -> None:
    history: list[HistoryRecord] = [night(0.98), unscored_night("no API key", run_url=None)]
    page = render_dashboard_html(history, repo_url=REPO)
    assert "Not scored" in page and "no API key" in page


def test_hostile_strings_escaped_and_bad_links_dropped() -> None:
    hostile: list[HistoryRecord] = [
        replace(night(0.98), model_id="<script>x</script>", run_url="javascript:alert(1)"),
        unscored_night("<img onerror=1>", run_url=None),
        replace(night(0.97), run_url="https://github.com/o/r/actions/runs/1"),
    ]
    page = render_dashboard_html(hostile, repo_url=REPO)
    assert "<script" not in page and "<img" not in page and "javascript:" not in page
    assert '<a href="https://github.com/o/r/actions/runs/1"' in page


def test_no_script_and_baseline_change_marked() -> None:
    history: list[HistoryRecord] = [night(0.98, baseline="b1"), night(0.97, baseline="b1"),
                                    night(0.99, baseline="b2"), night(0.99, baseline="b2")]
    page = render_dashboard_html(history, repo_url=REPO)
    assert "<script" not in page
    assert page.count('class="baseline-change"') == 1


def test_single_night_trend_is_visible() -> None:
    # A one-point polyline draws nothing: day one of the public page must still show a mark.
    page = render_dashboard_html([night(0.98)], repo_url=REPO)
    assert "<circle" in page
