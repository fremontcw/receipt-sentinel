# Hosted demo: one-time setup

What you do once to put the demo online. The design is in
`docs/superpowers/specs/2026-10-20-hosted-azure-demo-design.md`. Commands are for macOS/Linux
with the Azure CLI (`brew install azure-cli`) and `gh` signed in to the repo.

Nothing here costs money until step 4 runs the app, and then it scales to zero when idle.

## 1. Sign in and make a resource group

```bash
az login
az group create --name receipt-sentinel-demo --location eastus
```

## 2. Create everything except the app

Pick the budget's start month once (this month's 1st) and use the same value on every later
run, or Azure rejects the re-run:

```bash
export BUDGET_START=2026-11-01   # first day of the current month; write it down
az deployment group create --resource-group receipt-sentinel-demo \
  --template-file infra/main.bicep \
  --parameters alertEmail=YOU@EXAMPLE.COM imageTag=none deployApp=false \
    budgetStartDate=$BUDGET_START \
  --query properties.outputs
```

Keep the outputs open; later steps use them.

## 3. Put the Anthropic key in Key Vault

1. In the Anthropic console, create a workspace called `receipt-sentinel-demo`, set its
   **monthly spend limit to $10**, and create an API key in it. Use this key only for the demo.
2. Give yourself permission to write the secret, then store it (the vault uses Azure RBAC):

```bash
VAULT=<keyVaultName output>
az role assignment create --role "Key Vault Secrets Officer" \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --scope "$(az keyvault show --name "$VAULT" --query id -o tsv)"
az keyvault secret set --vault-name "$VAULT" --name anthropic-api-key --value "$(pbpaste)"
```

(Copy the key to the clipboard first; this keeps it out of your shell history.)

## 4. Start the app

The `deploy-hosted` workflow pushes an image to GHCR after every green run on `main`. Make
that package public once (GitHub → your profile → Packages → `receipt-sentinel` → Package
settings → Change visibility → Public), then deploy with the latest `main` commit:

```bash
SHA=$(git rev-parse origin/main)
az deployment group create --resource-group receipt-sentinel-demo \
  --template-file infra/main.bicep \
  --parameters alertEmail=YOU@EXAMPLE.COM imageTag=$SHA deployApp=true \
    budgetStartDate=$BUDGET_START \
  --query properties.outputs.appFqdn
```

Open `https://<appFqdn>`. You should see the demo in free mode.

## 5. Let GitHub deploy from now on

Set these repository **variables** (not secrets; they are identifiers) from the step 2 outputs:

```bash
gh variable set AZURE_CLIENT_ID --body <githubClientId>
gh variable set AZURE_TENANT_ID --body <tenantId>
gh variable set AZURE_SUBSCRIPTION_ID --body <subscriptionId>
gh variable set AZURE_RESOURCE_GROUP --body receipt-sentinel-demo
```

From then on, every green `main` updates the app to that commit.

## 6. Your own domain (optional, recommended for the resume link)

Buy the domain, then add two DNS records at your registrar:

| Type | Name | Value |
|---|---|---|
| CNAME | `demo` | `<appFqdn>` |
| TXT | `asuid.demo` | `<customDomainVerificationId>` |

When they resolve (`dig demo.YOURDOMAIN.dev CNAME`), add the domain and its free certificate:

```bash
ENV=$(az containerapp show -g receipt-sentinel-demo -n receipt-sentinel --query properties.managedEnvironmentId -o tsv)
az containerapp hostname add -g receipt-sentinel-demo -n receipt-sentinel --hostname demo.YOURDOMAIN.dev
az containerapp hostname bind -g receipt-sentinel-demo -n receipt-sentinel \
  --hostname demo.YOURDOMAIN.dev --environment "$ENV" --validation-method CNAME
CERT=$(az containerapp env certificate list -g receipt-sentinel-demo -n "${ENV##*/}" \
  --managed-certificates-only --query "[0].id" -o tsv)
```

Then add `customDomain=demo.YOURDOMAIN.dev customDomainCertificateId=$CERT` to the step 4
command whenever you re-run it, so the template keeps the binding.

## 7. Issue personal links

Give yourself access to the link table once, then issue links from this repo:

```bash
az role assignment create --role "Storage Table Data Contributor" \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --scope "$(az storage account show -n <storageAccountName> --query id -o tsv)"
uv sync --extra hosted
export SENTINEL_TABLES_ENDPOINT=<tablesEndpoint>
export SENTINEL_PUBLIC_URL=https://demo.YOURDOMAIN.dev   # or https://<appFqdn>
uv run sentinel demo-link issue "Acme - Jane, eng manager"
uv run sentinel demo-link list
uv run sentinel demo-link revoke "Acme - Jane, eng manager"
```

Paste the printed link into your email. The first time that person uses the real model, an
email from `DoNotReply@...azurecomm.net` reaches `alertEmail` (check spam the first time).

## If something looks wrong

- The app won't start (step 4 fails, or the revision shows "Failed"): the Key Vault secret
  `anthropic-api-key` is missing or your identity's access hasn't propagated yet. Check step 3,
  wait a few minutes, and re-run step 4.
- Rebuilding from scratch: Key Vault keeps a deleted vault for 7 days under the same name, so
  re-creating the resource group fails on the vault. Purge it first:
  `az keyvault purge --name <keyVaultName>`.
- "Personal links are unavailable": the app's identity can't reach Table Storage yet (role
  assignments take a few minutes the first time).
- Budget emails come from Azure Cost Management at 50% and 100% of $15.
