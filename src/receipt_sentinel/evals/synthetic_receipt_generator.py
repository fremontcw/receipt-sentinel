"""synthetic_receipt_generator

Seeded generator of text-only synthetic receipts with ground-truth labels, covering tips,
discounts, refunds, tax lines, EUR, OCR noise and cropped headers.

Status:
    None
Issues:
    None
Todo:
    None
"""

import random
from datetime import date, timedelta
from decimal import Decimal

import yaml

from receipt_sentinel.categorize.receipt_record import Category, LineItem, ModelOutput
from receipt_sentinel.evals.golden_case import Difficulty, GoldenCase

VENDORS: dict[Category, list[str]] = {
    Category.GROCERIES: ["Trader Joe's", "Whole Foods Market", "Safeway"],
    Category.DINING: ["Chipotle", "Blue Bottle Coffee", "Sakura Sushi"],
    Category.TRANSPORT: ["Lyft", "Shell", "BART"],
    Category.UTILITIES: ["PG&E", "Comcast", "AT&T"],
    Category.SUBSCRIPTIONS: ["Netflix", "Spotify", "GitHub"],
    Category.HEALTH: ["CVS Pharmacy", "Walgreens", "Bay Dental"],
    Category.HOME: ["Home Depot", "IKEA", "Ace Hardware"],
    Category.OTHER: ["Amazon", "Target", "Etsy"],
}
ITEMS: dict[Category, list[str]] = {
    Category.GROCERIES: ["Bananas", "Oat milk", "Sourdough", "Eggs", "Spinach"],
    Category.DINING: ["Burrito bowl", "Latte", "Salmon roll", "Miso soup"],
    Category.TRANSPORT: ["Ride fare", "Unleaded 9.8 gal", "Clipper reload"],
    Category.UTILITIES: ["Electric service", "Internet 300Mbps", "Wireless plan"],
    Category.SUBSCRIPTIONS: ["Monthly plan", "Premium", "Team seat"],
    Category.HEALTH: ["Ibuprofen", "Vitamin D", "Cleaning"],
    Category.HOME: ["Wood screws", "LED bulb", "Paint roller"],
    Category.OTHER: ["USB cable", "Notebook", "Gift wrap"],
}
VARIANTS = ("plain", "tax", "tip", "discount", "refund", "eur", "ocr_noise", "cropped")
VARIANT_DIFFICULTY = {
    "plain": Difficulty.EASY,
    "tax": Difficulty.EASY,
    "tip": Difficulty.MEDIUM,
    "discount": Difficulty.MEDIUM,
    "refund": Difficulty.MEDIUM,
    "eur": Difficulty.HARD,
    "ocr_noise": Difficulty.HARD,
    "cropped": Difficulty.HARD,
}
OCR_SWAPS = {"0": "O", "O": "0", "1": "l", "l": "1", " ": ""}


def _money(rng: random.Random, low: int, high: int) -> Decimal:
    """Return a random dollar amount in [low, high] with two decimal places."""
    return Decimal(rng.randint(low * 100, high * 100)) / 100


def _line_items(rng: random.Random, category: Category, variant: str) -> list[LineItem]:
    """Build the line items for a receipt, applying the variant's extra line or sign flip."""
    names = rng.sample(ITEMS[category], k=min(len(ITEMS[category]), rng.randint(1, 4)))
    items = [LineItem(description=n, amount=_money(rng, 1, 60)) for n in names]
    if variant == "tax":
        items.append(LineItem(description="Tax", amount=_money(rng, 0, 5)))
    if variant == "tip":
        items.append(LineItem(description="Tip", amount=_money(rng, 1, 10)))
    if variant == "discount":
        items.append(LineItem(description="Discount", amount=-_money(rng, 1, 5)))
    if variant == "refund":
        items = [LineItem(description=f"Refund: {i.description}", amount=-i.amount) for i in items]
    return items


def _noise(rng: random.Random, line: str) -> str:
    """Mangle ~5% of a line's non-amount characters with common OCR misreads."""
    # Only mangle the description part; amounts stay exact so labels remain unambiguous
    head, _, amount = line.rpartition(" ")
    mangled = "".join(
        OCR_SWAPS.get(ch, ch) if ch in OCR_SWAPS and rng.random() < 0.05 else ch for ch in head
    )
    return f"{mangled} {amount}"


def render_receipt_text(
    rng: random.Random,
    vendor: str,
    when: date,
    items: list[LineItem],
    currency: str,
    variant: str,
) -> str:
    """Render a plain-text receipt for the given items, applying the variant's layout quirk."""
    symbol = "€" if currency == "EUR" else "$"
    lines = [vendor.upper(), when.strftime("%m/%d/%Y")]
    lines += [f"{i.description:<24}{symbol}{i.amount:>8}" for i in items]
    lines.append(f"{'TOTAL':<24}{symbol}{sum(i.amount for i in items):>8}")
    if variant == "cropped":
        lines = lines[1:] + [f"Thank you for shopping at {vendor}"]
    if variant == "ocr_noise":
        lines = [_noise(rng, line) for line in lines]
    return "\n".join(lines) + "\n"


def synthetic_case_for_variant(rng: random.Random, index: int, variant: str) -> GoldenCase:
    """Build one synthetic golden case with the given variant (one of VARIANTS).

    Args:
        rng: Seeded generator; the case is a pure function of its state.
        index: 1-based number used in the case id.
        variant: Layout quirk, one of VARIANTS.

    Returns:
        The case, with its ground-truth labels.
    """
    category = Category.DINING if variant == "tip" else rng.choice(list(Category))
    vendor = rng.choice(VENDORS[category])
    when = date(2026, 8, 1) + timedelta(days=rng.randint(0, 27))
    currency = "EUR" if variant == "eur" else "USD"
    items = _line_items(rng, category, variant)
    total = sum((i.amount for i in items), Decimal("0"))
    difficulty = VARIANT_DIFFICULTY[variant]
    return GoldenCase(
        id=f"synthetic_{index:04d}_{category.value}_{difficulty.value}",
        difficulty=difficulty,
        source="synthetic",
        notes=f"variant={variant}",
        email_date=when + timedelta(days=1),
        receipt_text=render_receipt_text(rng, vendor, when, items, currency, variant),
        expected=ModelOutput(
            vendor=vendor,
            total=total,
            currency=currency,
            date=when,
            category=category,
            line_items=items,
            confidence=1.0,
            injection_detected=False,
        ),
    )


def generate_synthetic_cases(seed: int, count: int) -> list[GoldenCase]:
    """Deterministically generate `count` synthetic golden cases from `seed`."""
    rng = random.Random(seed)  # noqa: S311 - deterministic test data, not security
    return [
        synthetic_case_for_variant(rng, index, VARIANTS[(index - 1) % len(VARIANTS)])
        for index in range(1, count + 1)
    ]


def golden_case_to_yaml(case: GoldenCase) -> str:
    """Serialize a golden case to YAML with Decimal amounts rendered as exact strings."""
    data = case.model_dump(mode="json")
    data["expected"]["total"] = str(case.expected.total)
    for item in data["expected"]["line_items"]:
        item["amount"] = str(Decimal(item["amount"]))
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
