// GPT-4.1 on Azure OpenAI for `sentinel evals compare-models --candidate-provider azure-openai`.
// The AIServices account itself was created by hand; this file owns the deployment and its
// content filter. Callers sign in with Entra ID (account has local auth disabled).
//
// Content filter = Azure's Microsoft.DefaultV2 with ONE change: Jailbreak annotates instead of
// blocking. The prompt's few-shot injection example ("SYSTEM: ignore all prior rules...") trips
// Prompt Shields on every request otherwise. Safe here: the model has no tools, can only return
// the receipt schema, and the app's own injection_detected check still runs.
//
// Apply: az deployment group create -g receipt-sentinel-demo -f infra/azure_openai.bicep

@description('Existing AIServices account name.')
param accountName string = 'rsfoundry7evhws'

@description('Thousands of tokens per minute for the Standard deployment.')
param capacityKtpm int = 50

var filterPolicyName = 'receipt-sentinel-flag-jailbreak'

// Hate / Sexual / Violence / Selfharm at Medium on both prompt and completion, as DefaultV2 ships.
var harmFilters = [for f in [
  { name: 'Hate', source: 'Prompt' }
  { name: 'Hate', source: 'Completion' }
  { name: 'Sexual', source: 'Prompt' }
  { name: 'Sexual', source: 'Completion' }
  { name: 'Violence', source: 'Prompt' }
  { name: 'Violence', source: 'Completion' }
  { name: 'Selfharm', source: 'Prompt' }
  { name: 'Selfharm', source: 'Completion' }
]: {
  name: f.name
  source: f.source
  severityThreshold: 'Medium'
  blocking: true
  enabled: true
}]

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: accountName
}

resource filterPolicy 'Microsoft.CognitiveServices/accounts/raiPolicies@2024-10-01' = {
  parent: account
  name: filterPolicyName
  properties: {
    basePolicyName: 'Microsoft.DefaultV2'
    mode: 'Default'
    contentFilters: concat(harmFilters, [
      { name: 'Jailbreak', source: 'Prompt', blocking: false, enabled: true }
      { name: 'Protected Material Text', source: 'Completion', blocking: true, enabled: true }
      { name: 'Protected Material Code', source: 'Completion', blocking: false, enabled: true }
    ])
  }
}

resource gpt41 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: 'gpt-4.1'
  sku: {
    name: 'Standard'
    capacity: capacityKtpm
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1'
      version: '2025-04-14'
    }
    raiPolicyName: filterPolicy.name
  }
}
