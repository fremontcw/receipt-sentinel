"""render_golden_fixtures

Renders selected golden cases as the files receipts really arrive as (text PDF, scanned
PDF, phone photo) and writes each as a new fixture case. Deterministic: re-running rewrites
identical files. Run: uv run python scripts/render_golden_fixtures.py

Status:
    None
Issues:
    None
Todo:
    None
"""

from collections.abc import Callable

from receipt_sentinel.evals.golden_case import GOLDEN_DIR, Difficulty, GoldenCase, load_golden_cases
from receipt_sentinel.evals.synthetic_fixture_renderer import (
    render_native_pdf,
    render_photo_jpeg,
    render_scanned_pdf,
)
from receipt_sentinel.evals.synthetic_receipt_generator import golden_case_to_yaml


def _photo(text: str) -> bytes:
    return render_photo_jpeg(text, rotate_degrees=2.5, blur_radius=0.6, exif_orientation=6)


# kind → (file extension, renderer, notes, source case ids)
FIXTURE_KINDS: dict[str, tuple[str, Callable[[str], bytes], str, tuple[str, ...]]] = {
    "pdf_native": (
        "pdf",
        render_native_pdf,
        "PDF with a text layer",
        (
            "synthetic_0001_dining_easy",
            "synthetic_0003_dining_medium",
            "synthetic_0046_dining_hard",
            "adversarial_0001_override",
        ),
    ),
    "pdf_scanned": (
        "pdf",
        render_scanned_pdf,
        "scanned PDF, OCR",
        (
            "synthetic_0010_transport_easy",
            "synthetic_0017_utilities_easy",
            "synthetic_0022_groceries_hard",
        ),
    ),
    "photo": (
        "jpg",
        _photo,
        "phone photo (tilted, soft focus, EXIF-rotated), OCR",
        (
            "synthetic_0002_home_easy",
            "synthetic_0033_health_easy",
            "synthetic_0042_transport_easy",
            "adversarial_0001_override",
        ),
    ),
}


def _fixture_case(source: GoldenCase, kind: str, extension: str, notes: str) -> GoldenCase:
    case_id = f"fixture_{kind}_{source.id.removeprefix('synthetic_')}"
    is_ocr = kind != "pdf_native"
    keep_difficulty = source.difficulty is Difficulty.ADVERSARIAL or not is_ocr
    return source.model_copy(
        update={
            "id": case_id,
            "difficulty": source.difficulty if keep_difficulty else Difficulty.HARD,
            "notes": f"{notes}; rendered from {source.id}",
            "fixture": f"fixtures/{case_id}.{extension}",
        }
    )


def main() -> int:
    """Render every fixture file and write its case YAML."""
    by_id = {case.id: case for case in load_golden_cases() if not case.fixture}
    (GOLDEN_DIR / "fixtures").mkdir(exist_ok=True)
    written = 0
    for kind, (extension, render, notes, source_ids) in FIXTURE_KINDS.items():
        for source_id in source_ids:
            case = _fixture_case(by_id[source_id], kind, extension, notes)
            (GOLDEN_DIR / f"fixtures/{case.id}.{extension}").write_bytes(render(case.receipt_text))
            (GOLDEN_DIR / "cases" / f"{case.id}.yaml").write_text(golden_case_to_yaml(case))
            written += 1
    print(f"wrote {written} fixture cases to {GOLDEN_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
