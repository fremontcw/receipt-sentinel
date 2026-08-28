"""prompt_config

Loads a versioned prompt YAML into an immutable PromptConfig. The active version is
pinned in config/active_prompt.yaml so a prompt change is always a reviewed diff.

Status:
    None
Issues:
    None
Todo:
    None
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).parent
REQUIRED_KEYS = ("id", "version", "created_at", "system", "few_shot")


@dataclass(frozen=True)
class FewShotExample:
    receipt_text: str
    expected_json: str


@dataclass(frozen=True)
class PromptConfig:
    id: str
    version: str
    created_at: str
    system: str
    few_shot: tuple[FewShotExample, ...]

    @property
    def label(self) -> str:
        return f"{self.id}@{self.version}"


def load_prompt_config(path: Path) -> PromptConfig:
    """Parse one prompt YAML file, failing loudly on any missing key."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: prompt file must be a mapping")
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise ValueError(f"{path}: missing keys {missing}")
    few_shot = tuple(
        FewShotExample(receipt_text=str(ex["receipt_text"]), expected_json=str(ex["expected_json"]))
        for ex in raw["few_shot"]
    )
    return PromptConfig(
        id=str(raw["id"]),
        version=str(raw["version"]),
        created_at=str(raw["created_at"]),
        system=str(raw["system"]),
        few_shot=few_shot,
    )


def load_active_prompt(config_path: Path = Path("config/active_prompt.yaml")) -> PromptConfig:
    """Resolve the pinned {id, version} to its YAML in PROMPTS_DIR."""
    pin = yaml.safe_load(config_path.read_text())
    return load_prompt_config(PROMPTS_DIR / f"{pin['id']}_v{pin['version']}.yaml")
