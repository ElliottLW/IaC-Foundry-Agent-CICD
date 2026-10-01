# AI Foundry Agent & APIM Policy CI/CD

Demo for the **Midland Microsoft Meetup**, Birmingham.

GitOps pipeline for managing Azure AI Foundry agents and API Management policies as code — no infrastructure knowledge required.

The sample agent is **Spaghetti Junction** — a Midlands cloud copilot that untangles Azure and AI before the design looks like the M6. Dev, test, and prod each ship a different prompt, so the same question on stage comes back with a different shape. That difference is the point.

Foundry names: `spaghetti-junction-dev`, `spaghetti-junction-test`, `spaghetti-junction`. A deploy registers these as new agents. It will not rename an older agent still sitting in the project.

Changes to agent configuration or API policies are deployed automatically when you push to the corresponding branch.

### On stage

Ask all three environments the same thing:

> I've got a Foundry agent. Should APIM sit in front of it?

| Environment | What the room should see |
|-------------|--------------------------|
| **dev** | A `DEV` banner, the recommendation, and the option it rejected |
| **test** | A `TEST` banner, then a **Promote?** checklist a reviewer would actually use |
| **prod** | The decision first. No banner. Shorter. |

Every answer ends with a *Junction note* — the closer is in the prompt, so a one-line edit is a visible deploy.

---

## How it works

### Branch deploys (day-to-day)

```
dev branch  ──push──▶  deploy to dev  environment
test branch ──push──▶  deploy to test environment
main branch ──push──▶  deploy to prod environment
```

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| **Deploy Agent** | Push to `src/agents/**` | Deploys the agent definition to AI Foundry via the Agents data-plane SDK |
| **Deploy APIM Policies** | Push to `src/apim-policies/**` | Pushes XML policy files to Azure API Management |

Both can also be triggered manually via **Actions → Run workflow**.

### Promotion pipeline (controlled releases)

The **Promote Agent** workflow deploys an agent through all three environments in sequence, with a mandatory approval gate before each stage:

```
 deploy-dev ──(approve)──▶ deploy-test ──(approve)──▶ deploy-prod
```

Trigger it manually via **Actions → Promote Agent → Run workflow**. An optional agent name can be specified — leave blank to promote all agents. If any stage fails, all downstream stages are blocked automatically.

Approval gates are configured via **Settings → Environments** — each environment requires a reviewer to click **Review deployments → Approve** before the next stage proceeds.

---

## Repository structure

```
├── .github/
│   └── workflows/
│       ├── deploy-agent.yml        # triggers on push to src/agents/**
│       ├── deploy-apim-apis.yml    # triggers on push to src/apim-policies/**
│       └── promote-agent.yml       # manual promotion: dev → test → prod with approval gates
└── src/
    ├── agents/
    │   └── <agent-name>/           # one folder per agent
    │       ├── dev.yaml            # agent config for dev
    │       ├── test.yaml           # agent config for test
    │       ├── prod.yaml           # agent config for prod
    │       ├── instructions.md     # shared system prompt (default)
    │       ├── instructions.dev.md # optional per-env override
    │       ├── instructions.test.md
    │       ├── instructions.prod.md
    │       └── guardrails.md       # optional, auto-appended to instructions
    ├── apim-policies/
    │   ├── dev/
    │   │   ├── agents-api.xml      # Foundry agents API policy
    │   │   └── chat-api.xml        # Chat/completions API policy
    │   ├── test/
    │   │   ├── agents-api.xml
    │   │   └── chat-api.xml
    │   └── prod/
    │       ├── agents-api.xml      # stricter limits + IP filtering
    │       └── chat-api.xml
    └── scripts/
        ├── deploy-agent.py         # creates/updates agent versions in Foundry
        ├── deploy-api.py           # pushes APIM policies + configures diagnostics
        └── requirements.txt
```

---

## Prerequisites

- An Azure subscription with an AI Foundry project and API Management instance already provisioned
- An Azure app registration with federated credentials for GitHub Actions OIDC (see setup below)

---

## Setup

### 1. Create GitHub Environments

In **Settings → Environments**, create three environments: `dev`, `test`, `prod`.

Add required reviewers to each environment to enable approval gates in the **Promote Agent** workflow. The reviewer will be prompted to approve before each stage runs.

### 2. Add GitHub Secrets

Add these secrets at the **repository level** (Settings → Secrets → Actions):

| Secret | Value |
|--------|-------|
| `AZURE_CLIENT_ID` | App registration client ID |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

### 3. Add GitHub Environment Variables

Add these variables to **each environment** (Settings → Environments → select env → Variables):

#### For the Agent workflow

| Variable | Example | Description |
|----------|---------|-------------|
| `FOUNDRY_PROJECT_ENDPOINT` | `https://myaccount.services.ai.azure.com/api/projects/myproject` | AI Foundry project endpoint |
| `OPENAI_DEPLOYMENT_NAME` | `gpt-4o` | Name of the GPT deployment to use |

#### For the APIM Policy workflow

| Variable | Example | Description |
|----------|---------|-------------|
| `APIM_NAME` | `apim-lw-ai-dev` | API Management instance name |
| `RESOURCE_GROUP_NAME` | `rg-lw-ai-dev` | Resource group containing the APIM instance |

### 4. Configure Azure OIDC

Your app registration needs federated credentials for each branch/environment combination.

```bash
# Run once per environment (dev / test / prod)
az ad app federated-credential create \
  --id <app-registration-object-id> \
  --parameters '{
    "name": "github-env-dev",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<your-org>/<your-repo>:environment:dev",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

The app registration needs these RBAC roles on the resource group:
- **Cognitive Services OpenAI Contributor** — to manage agent definitions
- **API Management Service Contributor** — to update APIM policies

---

## Adding a new agent

No pipeline changes are needed. The Deploy Agent workflow automatically discovers every folder under `src/agents/` and deploys each one.

### 1. Create the agent folder

```
src/agents/<your-agent-name>/
  dev.yaml
  test.yaml
  prod.yaml
  instructions.md      ← shared system prompt (required)
  guardrails.md        ← optional, auto-appended to instructions if present
```

Use `spaghetti-junction` as a reference.

### 2. Create the environment YAML files

Each YAML file configures the agent for one environment:

```yaml
# src/agents/my-new-agent/dev.yaml
name: my-new-agent-dev              # agent name registered in Foundry
display_name: "My New Agent [DEV]"
description: "What this agent does"
model: gpt-4o
instructions_file: instructions.md  # optional — defaults to instructions.md
```

> The `name` field is what gets registered in Foundry and exposed through APIM. Use the `<agent-name>-<env>` convention to keep environments separate.

### 3. Write the system prompt

Add your agent's instructions to `instructions.md`. You can use per-environment overrides by naming the file `instructions.dev.md`, `instructions.test.md`, or `instructions.prod.md` and referencing it in the YAML.

### 4. Push and deploy

```bash
git add src/agents/my-new-agent/
git commit -m "feat: add my-new-agent"
git push origin dev          # triggers Deploy Agent → dev
```

Or trigger a single agent manually via **Actions → Deploy Agent → Run workflow**, specifying the agent folder name in the "Agent to deploy" input.

### 5. Required Azure RBAC for new environments

The GitHub Actions service principal needs **Azure AI User** on the Foundry account for each environment it deploys to:

```bash
az role assignment create \
  --assignee-object-id <github-sp-object-id> \
  --assignee-principal-type ServicePrincipal \
  --role "Azure AI User" \
  --scope "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account>"
```

---

## Making changes

### Update agent behaviour

Edit `src/agents/<agent-name>/<env>.yaml` or the instructions file, then push to the relevant branch. The Deploy Agent workflow runs automatically.

```yaml
# src/agents/spaghetti-junction/prod.yaml
name: "spaghetti-junction"
display_name: "Spaghetti Junction"
description: "Midlands cloud copilot for the Midland Microsoft Meetup in Birmingham."
instructions_file: instructions.md
```

### Update API policies

Edit the XML files in `src/apim-policies/<env>/`, then push. The Deploy APIM Policies workflow runs automatically.

**To block an IP in prod:**
```xml
<!-- src/apim-policies/prod/chat-api.xml -->
<ip-filter action="forbid">
  <address>1.2.3.4</address>   <!-- add this line -->
</ip-filter>
```

**To tighten the rate limit:**
```xml
<rate-limit-by-key calls="30" renewal-period="60" ... />
```

Push to `main` → policy is live in ~30 seconds, with a full Git audit trail.

---

## Local development

```bash
# Install dependencies
pip install -r src/scripts/requirements.txt

# Copy and fill in the example env file
cp src/scripts/.env.example src/scripts/.env

# Run a dry-run deploy
python src/scripts/deploy-agent.py --env dev --agent spaghetti-junction --dry-run
```

The `.env` file is gitignored — it is for local use only.
