"""test_walkthrough_doc

Keeps docs/walkthrough.md runnable: every make target and repo path it names exists, and
its timed sections fit in three minutes.

Status:
    None
Issues:
    None
Todo:
    None
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WALKTHROUGH = REPO_ROOT / "docs" / "walkthrough.md"
LAST_SECTION_LATEST_START_SECONDS = 170  # 2:50, leaving 10 s to close


def test_walkthrough_make_targets_exist() -> None:
    targets = set(re.findall(r"^([\w-]+):", (REPO_ROOT / "Makefile").read_text(), re.M))
    used = set(re.findall(r"make ([\w-]+)", WALKTHROUGH.read_text()))
    assert used
    assert used <= targets


def test_walkthrough_repo_paths_exist() -> None:
    # runs/ is created by the demos, so it can't exist in a fresh clone
    paths = [
        p
        for p in re.findall(r"`([\w./-]+/[\w./-]+)`", WALKTHROUGH.read_text())
        if not p.startswith(("runs/", "http"))
    ]
    assert paths
    assert [p for p in paths if not (REPO_ROOT / p).exists()] == []


def test_walkthrough_fits_three_minutes() -> None:
    stamps = re.findall(r"^## (\d):(\d\d)", WALKTHROUGH.read_text(), re.M)
    assert stamps[0] == ("0", "00")
    minutes, seconds = stamps[-1]
    assert int(minutes) * 60 + int(seconds) <= LAST_SECTION_LATEST_START_SECONDS
