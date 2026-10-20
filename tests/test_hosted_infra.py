"""test_hosted_infra

Text checks on the hosted demo's Azure setup (infra/main.bicep) and its deploy workflow.
CI also compiles the Bicep (`az bicep build`); nothing here touches Azure.

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
BICEP = (REPO_ROOT / "infra" / "main.bicep").read_text()
DEPLOY = REPO_ROOT / ".github" / "workflows" / "deploy-hosted.yml"


def test_bicep_caps_replicas_at_one_and_scales_to_zero() -> None:
    assert "scale: { minReplicas: 0, maxReplicas: 1 }" in BICEP


def test_bicep_has_no_secret_values() -> None:
    assert "@secure()" not in BICEP  # nothing secret is passed in
    assert "allowSharedKeyAccess: false" in BICEP
    secret_block = BICEP[BICEP.index("secrets: [") : BICEP.index("template: {")]
    assert "keyVaultUrl" in secret_block and "value:" not in secret_block
    assert re.search(r"name: 'ANTHROPIC_API_KEY', secretRef: ", BICEP)


def test_bicep_federated_subject_is_main_only() -> None:
    assert "subject: 'repo:${githubRepo}:ref:refs/heads/main'" in BICEP
    assert BICEP.count("federatedIdentityCredentials@") == 1


def test_bicep_deploy_identity_is_scoped_to_the_app() -> None:
    deploy_access = BICEP[BICEP.index("resource deployAccess") :]
    assert deploy_access.split("properties")[0].count("scope: app") == 1


def test_deploy_workflow_uses_oidc_and_pins_sha() -> None:
    workflow = DEPLOY.read_text()
    assert "id-token: write" in workflow
    assert "secrets.AZURE" not in workflow
    for uses in re.findall(r"uses:\s*(\S+)", workflow):
        assert re.search(r"@[0-9a-f]{40}$", uses), f"unpinned action {uses}"
    assert "github.event.workflow_run.head_sha" in workflow
    assert ":latest" not in workflow


def test_deploy_skips_without_azure_vars() -> None:
    workflow = DEPLOY.read_text()
    assert "if: vars.AZURE_CLIENT_ID != ''" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    # OIDC token only where the Azure login happens, package write only where the push happens
    image_job, deploy_job = workflow.split("  deploy:")
    assert "id-token: write" not in image_job and "id-token: write" in deploy_job
    assert "packages: write" in image_job and "packages: write" not in deploy_job
