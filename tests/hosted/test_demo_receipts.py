import hashlib

from receipt_sentinel.evals.synthetic_receipt_generator import (
    generate_synthetic_cases,
    golden_case_to_yaml,
)
from receipt_sentinel.hosted.demo_receipts import (
    SAMPLE_RECEIPTS,
    STAMP,
    random_receipt,
    receipt_image,
    sample_receipt,
)

# sha256 of the golden set's synthetic cases (seed 2026, count 48), taken before the
# generator refactor: the hosted demo must not change the golden set
GOLDEN_SYNTHETIC_SHA256 = "6204b151930fa120f5a73fdd00c7bb428464b954c67039eecbd6b8ba21a131f1"


def test_golden_generator_output_is_unchanged() -> None:
    cases = generate_synthetic_cases(seed=2026, count=48)
    digest = hashlib.sha256("".join(golden_case_to_yaml(c) for c in cases).encode()).hexdigest()
    assert digest == GOLDEN_SYNTHETIC_SHA256


def test_random_receipt_is_deterministic_and_stamped() -> None:
    a, b = random_receipt(42), random_receipt(42)
    assert a == b
    assert a.receipt_id == "random-42"
    lines = a.text.strip().splitlines()
    assert lines[0] == STAMP and lines[-1] == STAMP


def test_random_receipts_cover_photo_and_injection() -> None:
    receipts = [random_receipt(seed) for seed in range(200)]
    assert any(r.is_photo for r in receipts)
    assert any(r.expected.injection_detected for r in receipts)
    assert any(not r.is_photo and not r.expected.injection_detected for r in receipts)


def test_samples_resolve_and_photo_sample_is_a_photo() -> None:
    assert set(SAMPLE_RECEIPTS) == {"clean", "tip", "prompt_injection", "tilted_photo"}
    assert sample_receipt("tilted_photo").is_photo
    assert sample_receipt("prompt_injection").expected.injection_detected
    assert sample_receipt("clean").text.strip().splitlines()[0] == STAMP


def test_receipt_image_media_types() -> None:
    assert receipt_image(sample_receipt("clean"))[1] == "image/png"
    assert receipt_image(sample_receipt("tilted_photo"))[1] == "image/jpeg"


def test_real_receipts_are_the_redacted_golden_cases() -> None:
    from receipt_sentinel.evals.golden_case import load_golden_cases
    from receipt_sentinel.hosted.demo_receipts import (
        REAL_LINE_WIDTH,
        REAL_STAMP,
        real_receipt_names,
    )

    golden = {c.id: c for c in load_golden_cases() if c.source == "real_redacted"}
    assert real_receipt_names() == tuple(sorted(golden))
    for name in real_receipt_names():
        receipt = sample_receipt(name)
        lines = receipt.text.strip().splitlines()
        assert lines[0] == REAL_STAMP and lines[-1] == REAL_STAMP
        assert receipt.expected == golden[name].expected and not receipt.is_photo
        assert max(len(line) for line in lines) <= REAL_LINE_WIDTH
        assert STAMP not in receipt.text  # "NOT A REAL RECEIPT" would be false here
