// Receipt Sentinel hosted demo (docs/superpowers/specs/2026-10-20-hosted-azure-demo-design.md).
// One resource group: Container App (scale to zero, one replica), Table Storage, Key Vault,
// email alerts, a $15 budget, and a GitHub OIDC identity that may only update the app.
// No secret values in here: the Anthropic key is set in Key Vault by hand (infra/hosted_demo_setup.md).
// Built-in role ids checked against learn.microsoft.com built-in-roles pages, 2026-10-20.

@description('Azure region for every resource.')
param location string = resourceGroup().location

@description('Where alert emails and budget notices go.')
param alertEmail string

@description('Image tag (git SHA) to run; the deploy workflow updates it later.')
param imageTag string

@description('Create the container app. First run: false, then set the Key Vault secret and re-run with true.')
param deployApp bool = false

@description('Custom domain, e.g. demo.example.dev; empty to use the default azurecontainerapps.io address.')
param customDomain string = ''

@description('Managed certificate id for customDomain (output of the hostname bind step); empty until then.')
param customDomainCertificateId string = ''

@description('GitHub repository allowed to deploy, owner/name.')
param githubRepo string = 'fremontcw/receipt-sentinel'

@description('The repository as it appears in GitHub OIDC tokens, owner@ownerId/name@repoId. GitHub puts these immutable ids in the token subject, so a renamed or re-created repo cannot deploy.')
param githubOidcRepo string = 'fremontcw@20915353/receipt-sentinel@1349754397'

@description('First day of the budget period, YYYY-MM-01. Pass the same value on every run: Azure will not move the start date of a budget.')
param budgetStartDate string

var prefix = 'rsdemo${uniqueString(resourceGroup().id)}'
var image = 'ghcr.io/${githubRepo}:${imageTag}'
var anthropicSecretName = 'anthropic-api-key'
var roles = {
  storageTableDataContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  containerAppsContributor: '358470bc-b998-42bd-ab17-a7e34c199c0f'
}

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-app'
  location: location
}

resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-github'
  location: location
}

resource githubFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployIdentity
  name: 'github-main'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubOidcRepo}:ref:refs/heads/main'
    audiences: ['api://AzureADTokenExchange']
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take(prefix, 24)
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource linksTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'demolinks'
}

resource spendTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'demospend'
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take('${prefix}kv', 24)
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource emailService 'Microsoft.Communication/emailServices@2023-04-01' = {
  name: '${prefix}-email'
  location: 'global'
  properties: { dataLocation: 'United States' }
}

resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' = {
  parent: emailService
  name: 'AzureManagedDomain'
  location: 'global'
  properties: { domainManagement: 'AzureManaged', userEngagementTracking: 'Disabled' }
}

resource communication 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: '${prefix}-acs'
  location: 'global'
  properties: {
    dataLocation: 'United States'
    linkedDomains: [emailDomain.id]
  }
}

// No built-in "send email" role; these are the permissions Microsoft's ACS SMTP/Entra guide lists
resource emailSenderRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'receipt-sentinel-email-sender')
  properties: {
    roleName: 'Receipt Sentinel email sender (${prefix})'
    description: 'Send email through the demo Communication Services resource.'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: [
          'Microsoft.Communication/CommunicationServices/Read'
          'Microsoft.Communication/CommunicationServices/Write'
          'Microsoft.Communication/EmailServices/write'
        ]
      }
    ]
  }
}

resource tablesAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, appIdentity.id, roles.storageTableDataContributor)
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageTableDataContributor)
  }
}

resource secretAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, appIdentity.id, roles.keyVaultSecretsUser)
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
  }
}

resource emailAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: communication
  name: guid(communication.id, appIdentity.id, emailSenderRole.id)
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: emailSenderRole.id
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {}
}

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: 'receipt-sentinel'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
        customDomains: empty(customDomainCertificateId) ? [] : [
          {
            name: customDomain
            certificateId: customDomainCertificateId
            bindingType: 'SniEnabled'
          }
        ]
      }
      secrets: [
        {
          name: anthropicSecretName
          keyVaultUrl: '${vault.properties.vaultUri}secrets/${anthropicSecretName}'
          identity: appIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'receipt-sentinel'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'SENTINEL_HOSTED', value: '1' }
            { name: 'SENTINEL_TABLES_ENDPOINT', value: storage.properties.primaryEndpoints.table }
            { name: 'SENTINEL_ACS_ENDPOINT', value: 'https://${communication.properties.hostName}' }
            { name: 'SENTINEL_ALERT_FROM', value: 'DoNotReply@${emailDomain.properties.fromSenderDomain}' }
            { name: 'SENTINEL_ALERT_TO', value: alertEmail }
            { name: 'AZURE_CLIENT_ID', value: appIdentity.properties.clientId }
            { name: 'ANTHROPIC_API_KEY', secretRef: anthropicSecretName }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
  dependsOn: [tablesAccess, secretAccess, emailAccess, linksTable, spendTable]
}

resource deployAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployApp) {
  scope: app
  name: guid(resourceGroup().id, deployIdentity.id, roles.containerAppsContributor)
  properties: {
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.containerAppsContributor)
  }
}

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: 'receipt-sentinel-demo'
  properties: {
    category: 'Cost'
    amount: 15
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      half: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 50, contactEmails: [alertEmail] }
      full: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 100, contactEmails: [alertEmail] }
    }
  }
}

output appFqdn string = deployApp ? app!.properties.configuration.ingress.fqdn : ''
output keyVaultName string = vault.name
output storageAccountName string = storage.name
output tablesEndpoint string = storage.properties.primaryEndpoints.table
output customDomainVerificationId string = environment.properties.customDomainConfiguration.customDomainVerificationId
output githubClientId string = deployIdentity.properties.clientId
output tenantId string = subscription().tenantId
output subscriptionId string = subscription().subscriptionId
output resourceGroupName string = resourceGroup().name
