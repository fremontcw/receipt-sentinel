import json
import re

from receipt_sentinel.checklist.checklist_proposal import build_checklist_proposal
from receipt_sentinel.checklist.checklist_review_page import render_review_page
from receipt_sentinel.ingest.ingest_pipeline import IngestReport
from tests.checklist.proposal_builders import ingested

HOSTILE = "</script><script>alert(1)</script>"


def _page(vendor: str = "Blue Bottle Coffee") -> str:
    report = IngestReport(receipts=(ingested(vendor),), skipped=(), unknown_senders=())
    return render_review_page(build_checklist_proposal(report, month="2026-08", conn=None))


def test_page_is_self_contained() -> None:
    page = _page()
    assert not re.search(r"""(src|href)\s*=\s*["']?https?://""", page)
    assert "<link" not in page


def test_hostile_vendor_cannot_break_out_of_the_json_block() -> None:
    page = _page(HOSTILE)
    assert "</script><script>alert(1)" not in page
    block = re.search(
        r'<script type="application/json" id="proposal">(.*?)</script>', page, re.DOTALL
    )
    assert block is not None
    assert json.loads(block.group(1))["items"][0]["receipt"]["record"]["vendor"] == HOSTILE


def test_page_never_uses_innerhtml_for_data() -> None:
    assert "innerHTML" not in _page()


def test_decisions_file_shape_is_documented_in_page() -> None:
    page = _page()
    assert '"user-reviewed"' in page
    assert "sentinel checklist confirm" in page and "--decisions" in page


def test_each_save_gets_its_own_file_name() -> None:
    # The browser renames a second save "(1)"; a fixed name made the printed command stale
    assert "decisions-${proposal.proposal_id}-${stamp}.json" in _page()


def test_key_repeat_and_duplicate_enter_are_guarded() -> None:
    page = _page()
    assert "event.repeat" in page
    assert "isDuplicate(" in page


def test_save_cannot_download_twice_until_reopened() -> None:
    # Enter stays on the focused Save button after saving; a second press re-downloaded
    page = _page()
    save_body = page[page.index("function save()"):page.index("$(\"accept\").onclick")]
    open_body = page[page.index("function openSave()"):page.index("function save()")]
    assert '$("save").disabled = true' in save_body
    assert '$("save").disabled = false' in open_body
