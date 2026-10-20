"""checklist_review_page

The checklist review page: one receipt at a time, Enter accepts, X rejects, S skips,
Backspace goes back; the last answer opens a save dialog that downloads the decisions file
`checklist confirm` reads. Self-contained (no network); every value is rendered as text.

Status:
    None
Issues:
    None
Todo:
    None
"""

import json
from pathlib import Path

from receipt_sentinel.checklist.checklist_proposal import ChecklistProposal, proposal_to_json

_TEMPLATE_PATH = Path(__file__).with_name("checklist_review_page.html")
_PROPOSAL_SLOT = "__PROPOSAL_JSON__"


def render_review_page(proposal: ChecklistProposal) -> str:
    """The review page with the proposal embedded as an inert JSON block."""
    return _TEMPLATE_PATH.read_text().replace(_PROPOSAL_SLOT, _inert_json(proposal))


def _inert_json(proposal: ChecklistProposal) -> str:
    # Model-derived strings (a vendor of "</script><script>…") must not end the data block
    # or start markup: escape the characters HTML parsing cares about as JSON \u escapes
    text = json.dumps(proposal_to_json(proposal), ensure_ascii=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
