"""build_redaction_review

Builds the keyboard page the author uses to check each real receipt's redaction: the
original from gitignored `_raw_receipts/email/<case_id>.txt` beside the redacted golden case,
removed text highlighted, labels underneath. Enter approves, X rejects; the last answer
opens a save dialog. Output goes to gitignored `runs/redaction_review/review.html`.

Usage: uv run python scripts/build_redaction_review.py

Status:
    None
Issues:
    None
Todo:
    None
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from receipt_sentinel.evals.golden_case import GOLDEN_DIR, load_golden_cases

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "_raw_receipts" / "email"
OUT_PATH = REPO_ROOT / "runs" / "redaction_review" / "review.html"
TEMPLATE_PATH = Path(__file__).with_name("redaction_review_page.html")
DATA_SLOT = "__CASES_JSON__"
TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def removed_segments(original: str, redacted: str) -> list[dict[str, object]]:
    """Split `original` into runs, marking the ones redaction took out."""
    # Word-level: a character diff matches stray letters of a name against "[REDACTED]"
    before, after = TOKEN.findall(original), TOKEN.findall(redacted)
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    segments: list[dict[str, object]] = []
    for tag, start, end, _, _ in matcher.get_opcodes():
        if end > start:
            segments.append({"text": "".join(before[start:end]), "removed": tag != "equal"})
    return segments


def build_review_cases(
    raw_dir: Path = RAW_DIR, golden_dir: Path = GOLDEN_DIR
) -> list[dict[str, object]]:
    """One review item per real case that still has its original on disk."""
    items = []
    for case in load_golden_cases(golden_dir):
        raw_path = raw_dir / f"{case.id}.txt"
        if case.source != "real_redacted" or not raw_path.exists():
            continue
        items.append(
            {
                "id": case.id,
                "notes": case.notes,
                "email_date": case.email_date.isoformat(),
                "original": removed_segments(raw_path.read_text(), case.receipt_text),
                "redacted": case.receipt_text,
                "expected": case.expected.model_dump(mode="json"),
            }
        )
    return items


def render_review_page(items: list[dict[str, object]]) -> str:
    """The page with the cases embedded as an inert JSON block."""
    # Escape what HTML parsing cares about so receipt text can't end the data block
    data = json.dumps(items, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return TEMPLATE_PATH.read_text().replace(DATA_SLOT, data)


if __name__ == "__main__":
    cases = build_review_cases()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render_review_page(cases))
    print(f"{len(cases)} cases -> {OUT_PATH}")
