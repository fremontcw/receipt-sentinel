"""test_prompt_config

Tests for the prompt config loader.

Status:
    None
Issues:
    None
Todo:
    None
"""

from pathlib import Path

import pytest

from receipt_sentinel.prompts.prompt_config import (
    PROMPTS_DIR,
    PromptConfig,
    load_active_prompt,
    load_prompt_config,
)


def test_load_v1_prompt() -> None:
    cfg = load_prompt_config(PROMPTS_DIR / "receipt_categorizer_v1.yaml")
    assert cfg.id == "receipt_categorizer"
    assert cfg.version == "1"
    assert cfg.label == "receipt_categorizer@1"
    assert "UNTRUSTED EVIDENCE" in cfg.system
    assert len(cfg.few_shot) == 2
    assert cfg.few_shot[1].expected_json.strip().startswith("{")


def test_load_active_prompt_resolves_pinned_version(tmp_path: Path) -> None:
    active = tmp_path / "active_prompt.yaml"
    active.write_text('id: receipt_categorizer\nversion: "1"\n')
    cfg = load_active_prompt(active)
    assert isinstance(cfg, PromptConfig)
    assert cfg.version == "1"


def test_missing_required_key_raises(tmp_path: Path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("id: x\nversion: '1'\n")
    with pytest.raises(ValueError, match="missing"):
        load_prompt_config(bad)
