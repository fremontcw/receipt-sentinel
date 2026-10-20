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
import subprocess
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


# Re-denied after the allowlist: bytecode, and the allowlist with real sender addresses
REDENIED_AFTER_ALLOWLIST = ("**/__pycache__", "config/allowlist.yaml")


def test_dockerignore_denies_by_default() -> None:
    lines = _dockerignore_lines()
    assert lines[0] == "*"
    assert all(line.startswith("!") or line in REDENIED_AFTER_ALLOWLIST for line in lines[1:])


def test_dockerignore_redenies_bytecode_and_real_allowlist() -> None:
    lines = _dockerignore_lines()
    last_allow = max(i for i, line in enumerate(lines) if line.startswith("!"))
    assert sorted(lines[last_allow + 1 :]) == sorted(REDENIED_AFTER_ALLOWLIST)


def test_dockerignore_never_allowlists_secrets() -> None:
    allowed = [line[1:].strip("/") for line in _dockerignore_lines() if line.startswith("!")]
    assert allowed
    assert not [a for a in allowed if a.split("/")[0] in SECRET_OR_PRIVATE]


def test_container_targets_publish_on_loopback_only() -> None:
    recipe = _make_target("container-serve")
    assert "-p 127.0.0.1:$(PORT):8000" in recipe
    assert not re.search(r"-p\s+(?!127\.0\.0\.1:)", recipe)


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


def _makefile_variable(name: str) -> str:
    match = re.search(rf"^{name} = (.*(?:\\\n.*)*)", (REPO_ROOT / "Makefile").read_text(), re.M)
    assert match, f"no Makefile variable {name}"
    return match.group(1)


def test_container_runs_as_the_host_user_on_podman_too() -> None:
    # keep-id alone leaves the image's USER 10001 in place: it can't write a runs/ owned by you
    podman_flags = re.search(r"\$\(filter podman,\$\(CONTAINER\)\),([^,]*),", _makefile_variable(
        "CONTAINER_USER"))
    assert podman_flags
    assert "--userns=keep-id" in podman_flags.group(1)
    assert "--user $$(id -u):$$(id -g)" in podman_flags.group(1)


def test_demo_targets_count_crit_only_with_a_fresh_report() -> None:
    # A crash also exits 1; only a report written by this run means "CRITICAL as intended"
    for target in ("demo", "container-demo"):
        recipe = _make_target(target)
        assert "rm -rf runs/demo" in recipe
        assert "-f runs/demo/report.html" in recipe


def test_container_targets_create_runs_before_mounting_it() -> None:
    assert "-v" not in _make_target("container-serve")  # serve writes nothing
    for target in ("container-demo", "container-checklist-demo", "container-checklist-confirm"):
        recipe = _make_target(target)
        assert "$(CONTAINER_RUN)" in recipe
        assert recipe.index("mkdir -p runs") < recipe.index("$(CONTAINER_RUN)")


def test_container_checklist_demo_starts_from_an_empty_store() -> None:
    # A second take would otherwise see r02 as already confirmed, with a new proposal id
    assert "rm -rf runs/checklist-demo" in _make_target("container-checklist-demo")


def test_smoke_test_starts_the_api_through_the_make_target() -> None:
    # So it runs the API as users do: their uid, their flags
    script = (REPO_ROOT / "scripts" / "container_smoke_test.sh").read_text()
    assert "make container-serve" in script
    assert "$(SERVE_FLAGS)" in _make_target("container-serve")


def test_confirm_target_names_missing_variables() -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["make", "-s", "container-checklist-confirm", "CONTAINER=echo"],  # noqa: S607
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "PROPOSAL=" in result.stdout + result.stderr


def test_image_records_the_git_sha_for_reports() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert re.search(r"^ENV SENTINEL_GIT_SHA=\$GIT_SHA$", dockerfile, re.M)
    assert "--build-arg GIT_SHA=" in _make_target("container-build")
