# AI Foundry Agent & APIM Policy CI/CD

Demo for the **Midland Microsoft Meetup**, Birmingham.

GitOps pipeline for managing Azure AI Foundry agents and API Management policies as code — no infrastructure knowledge required.

The sample agent is **Spaghetti Junction** — a Midlands cloud copilot that untangles Azure and AI before the design looks like the M6. Dev, test, and prod each ship a different prompt, so the same question on stage comes back with a different shape. That difference is the point.

Foundry names: `spaghetti-junction-dev`, `spaghetti-junction-test`, `spaghetti-junction`. A deploy registers these as new agents. It will not rename an older agent still sitting in the project.

A push does not deploy by itself. The workflow has to start, then a reviewer approves the GitHub environment. On `main`, only the API workflow starts from a push, and that push targets prod. This subscription only has dev, so the stage path is a pull request into `dev`, then **Review deployments → Approve**.

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
| **Deploy Agent** | **Actions → Deploy Agent → Run workflow** on `main`. The `dev` branch also runs it on a push to `src/agents/**`. | Creates a new agent version in Foundry and activates the Responses endpoint |
| **Deploy APIM APIs** | Push to `src/apis/**` on `dev`, `test`, or `main`, or **Run workflow** | Imports the OpenAPI spec, applies `policy.<env>.xml`, and attaches the product |

`dev` deploys dev, `test` deploys test, and `main` deploys prod. Do not merge a demo change to `main` to update the live gateway. Prod is not in this subscription, and `main` would apply `policy.prod.xml`, not the dev file.

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
│       ├── deploy-agent.yml        # manual on main; push to src/agents/** on dev
│       ├── deploy-apim-apis.yml    # push to src/apis/**, or manual
│       └── promote-agent.yml       # manual promotion: dev → test → prod with approval gates
└── src/
    ├── agents/
    │   └── spaghetti-junction/
    │       ├── dev.yaml            # name, instructions file, starter prompts
    │       ├── test.yaml
    │       ├── prod.yaml           # uses instructions.md, not a prod-only file
    │       ├── instructions.md     # shared prompt, used by prod
    │       ├── instructions.dev.md
    │       ├── instructions.test.md
    │       └── guardrails.md       # appended at deploy time
    ├── apis/
    │   ├── foundry-agents/         # gateway path /agents
    │   │   ├── config.yaml
    │   │   ├── openapi.yaml
    │   │   └── policy.dev.xml      # also policy.test.xml and policy.prod.xml
    │   └── foundry-chat-models/    # gateway path /chat
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

| Variable | Example (dev) | Description |
|----------|---------|-------------|
| `FOUNDRY_PROJECT_ENDPOINT` | `https://lw-ai-foundry-dev.services.ai.azure.com/api/projects/aip-lw-ai-dev` | AI Foundry project endpoint. No trailing slash. |
| `OPENAI_DEPLOYMENT_NAME` | `gpt-4o` | Deployment name on the connected Azure OpenAI account, not the Foundry account name |

#### For the APIM Policy workflow

| Variable | Example (dev) | Description |
|----------|---------|-------------|
| `APIM_NAME` | `apim-lwtech-ai-dev` | API Management instance name |
| `RESOURCE_GROUP_NAME` | `rg-lw-ai-platform-dev` | Resource group containing the APIM instance |

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

The app registration needs these RBAC roles. Assign by role definition ID. The old names (`Azure AI User`, `Azure AI Owner`) were renamed and are not reliable:

- **Foundry User** (`53ca6127-db72-4b80-b1b0-d745d6d5456d`) on the Foundry account — deploy and update agent versions
- **API Management Service Contributor** on the APIM instance — import APIs and policies
- **Foundry Owner** (`c883944f-8b7b-4483-af10-35834be79c4a`) on the Foundry account — APIM managed identity. The agent endpoint checks the control-plane action `Microsoft.CognitiveServices/accounts/AIServices/agents/write`. Foundry Agent Consumer only grants `endpoints/interact` and is not enough for this call. Do not use Cognitive Services OpenAI User on the Foundry account.

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

The GitHub Actions service principal needs **Foundry User** on the Foundry account. The APIM managed identity needs **Foundry Owner** on that same account. Assign by role ID, not the retired Azure AI User name:

```bash
# Pipeline identity — create and update agent versions
az role assignment create \
  --assignee-object-id <github-sp-object-id> \
  --assignee-principal-type ServicePrincipal \
  --role 53ca6127-db72-4b80-b1b0-d745d6d5456d \
  --scope "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account>"

# APIM managed identity — agent endpoint currently requires control-plane agents/write
az role assignment create \
  --assignee-object-id <apim-mi-object-id> \
  --assignee-principal-type ServicePrincipal \
  --role c883944f-8b7b-4483-af10-35834be79c4a \
  --scope "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account>"
```

---

## Making changes

### Update agent behaviour

Edit `src/agents/<agent-name>/<env>.yaml` or the instructions file, then push to the relevant branch. The Deploy Agent workflow runs automatically.

```yaml
# src/agents/spaghetti-junction/prod.yaml
name: "spaghetti-junction"
display_name: "Spaghetti Junction". On `main`, run **Actions → Deploy Agent** and choose the environment. A push to `dev` that changes `src/agents/**` starts that workflow itself, then waits for the dev environment approval
description: "Midlands cloud copilot for the Midland Microsoft Meetup in Birmingham."
instructions_file: instructions.md
```

### Update API policies

Edit the XML files in `src/apim-policies/<env>/`, then push. The Deploy APIM Policies workflow runs automatically.

**To block an IP in prod:**
```xml
<!-- src/apim-policies/prod/chat-api.xml -->
<ip-f`src/apis/<api>/policy.<env>.xml`, then merge to the branch for that environment. The workflow applies that file only. Merging the dev policy to `main` does not change dev.

**To allow one IP and refuse everyone else:**
```xml
<!-- src/apis/foundry-agents/policy.dev.xml -->
<ip-filter action="allow">
  <address>154.49.80.45</address>
</ip-filter>
```

`action="allow"` is the allow-list. `action="forbid"` only blocks the addresses you name.

The deploy still waits for the environment approval. It is not live on push
```bash
# Install dependencies
pip install -r src/scripts/requirements.txt

pip install -r src/scripts/requirements.txt

# FOUNDRY_PROJECT_ENDPOINT and OPENAI_DEPLOYMENT_NAME, or a gitignored .env
python src/scripts/deploy-agent.py --env dev --agent spaghetti-junction --dry-run
```

There is no `.env.example` in the repo. Use `py -3` on Windows if `python` is the Store alias.

### Call the dev gateway

Import [postman/foundry-agents.postman_collection.json](postman/foundry-agents.postman_collection.json) and [postman/dev.postman_environment.json](postman/dev.postman_environment.json). Put the `ai-platform` subscription key in `subscriptionKey`. Do not commit it. `postman/dev.local.postman_environment.json` is gitignored for that reason.

Send **Invoke spaghetti-junction-dev**. In the response body, filter with `$.output[0].content[0].text`. The Visualize tab shows the same text. The header to point at is `Ocp-Apim-Subscription-Key`. APIM adds the Foundry token. Do not send `Authorization`