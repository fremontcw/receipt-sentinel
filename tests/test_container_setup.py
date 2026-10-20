"""test_container_setup

Text checks on the container setup (Dockerfile, .dockerignore, Makefile targets) that need
no container runtime. scripts/container_smoke_test.sh runs the image itself, in CI.

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
SECRET_OR_PRIVATE = ("_raw_receipts", "credentials.json", "token.json", ".env", "data", "runs")


def _make_target(name: str) -> str:
    """Recipe lines of one Makefile target (the lines after `name:` up to the next blank)."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    match = re.search(rf"^{re.escape(name)}:.*\n((?:.+\n?)*)", makefile, re.M)
    assert match, f"no Makefile target {name}"
    return match.group(1)


def _dockerignore_lines() -> list[str]:
    text = (REPO_ROOT / ".dockerignore").read_text()
    return [line.strip() for line in text.splitlines() if line.strip() and line[0] != "#"]


def test_dockerignore_denies_by_default() -> None:
    lines = _dockerignore_lines()
    assert lines[0] == "*"
    assert all(line.startswith("!") for line in lines[1:])


def test_dockerignore_never_allowlists_secrets() -> None:
    allowed = [line[1:].strip("/") for line in _dockerignore_lines() if line.startswith("!")]
    assert allowed
    assert not [a for a in allowed if a.split("/")[0] in SECRET_OR_PRIVATE]


def test_container_targets_publish_on_loopback_only() -> None:
    recipe = _make_target("container-serve")
    assert "-p 127.0.0.1:8000:8000" in recipe
    assert not re.search(r"-p\s+8000", recipe)


def test_container_targets_pass_keys_by_name_only() -> None:
    for target in ("container-serve", "container-demo", "container-checklist-demo"):
        assert not re.search(r"-e\s+\w+=", _make_target(target))
    assert re.search(r"-e ANTHROPIC_API_KEY(\s|$)", _make_target("container-serve"))


def test_dockerfile_runs_as_non_root_and_pins_bases() -> None:
    text = (REPO_ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER 10001$", text, re.M)
    stages = set(re.findall(r"^FROM \S+ AS (\w+)", text, re.M))
    images = re.findall(r"^FROM (\S+)", text, re.M) + re.findall(r"COPY --from=(\S+)", text)
    external = [i for i in images if i not in stages]
    assert len(external) == 3 and all("@sha256:" in i for i in external)  # python x2, uv
