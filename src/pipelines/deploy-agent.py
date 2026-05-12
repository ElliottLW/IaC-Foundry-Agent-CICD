"""
deploy-agent.py

Deploys (or updates) a Foundry Agent using the azure-ai-projects SDK v2.

Agent config lives alongside Terraform in versioned YAML files:

    src/agents/{agent}/
        dev.yaml          ← flat config per environment
        test.yaml
        prod.yaml
        instructions.md   ← shared system prompt (can be overridden per env via instructions_file)

Usage:
    python deploy-agent.py --env dev|test|prod [--agent <dir>] [--dry-run]

Required env vars (set by Terraform outputs in CI):
    FOUNDRY_PROJECT_ENDPOINT   - Foundry project endpoint URL
    OPENAI_DEPLOYMENT_NAME     - GPT deployment name (e.g. gpt-4o)
"""

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

_AGENTS_ROOT   = Path(__file__).parent.parent.parent / "src" / "agents"
_DEFAULT_AGENT = "azure-ai-portugal-agent"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        log.error("Required environment variable '%s' is not set.", name)
        sys.exit(1)
    return value


def load_config(agent_dir: Path, env: str) -> dict:
    config_file = agent_dir / f"{env}.yaml"
    if not config_file.is_file():
        log.error("Config not found: %s", config_file)
        sys.exit(1)
    config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    log.info("Loaded config from '%s'.", config_file)
    return config


def load_instructions(agent_dir: Path, config: dict) -> str:
    instructions_file = agent_dir / config.get("instructions_file", "instructions.md")
    if not instructions_file.is_file():
        log.error("Instructions file not found: %s", instructions_file)
        sys.exit(1)
    instructions = instructions_file.read_text(encoding="utf-8").strip()

    guardrails_file = config.get("guardrails_file")
    if guardrails_file:
        guardrails_path = agent_dir / guardrails_file
        if guardrails_path.is_file():
            guardrails = guardrails_path.read_text(encoding="utf-8").strip()
            instructions = f"{instructions}\n\n---\n\n{guardrails}"
            log.info("Appended guardrails from '%s'.", guardrails_path)
        else:
            log.warning("Guardrails file '%s' not found — skipping.", guardrails_path)

    return instructions


_ARM_API_VERSION = "2025-10-01-preview"
_ARM_BASE = "https://management.azure.com"


def publish_agent_application(
    credential,
    subscription_id: str,
    resource_group: str,
    account_name: str,
    project_name: str,
    agent_name: str,
    agent_version: str,
    dry_run: bool = False,
) -> None:
    """
    Create or update the Agent Application and Deployment for a prompt agent.
    Uses the Azure Resource Manager management plane API.

    Agent Application endpoint once published:
      POST {foundry_endpoint}/applications/{agent_name}/protocols/openai/v1/responses
    """
    token = credential.get_token("https://management.azure.com/.default").token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    base = (
        f"{_ARM_BASE}/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/projects/{project_name}"
    )
    app_url = f"{base}/applications/{agent_name}?api-version={_ARM_API_VERSION}"
    dep_url = (
        f"{base}/applications/{agent_name}/agentdeployments/default"
        f"?api-version={_ARM_API_VERSION}"
    )

    app_body = json.dumps({
        "properties": {
            "agents": [{"agentName": agent_name}],
            "authorizationPolicy": {"AuthorizationScheme": "Default"},
        }
    }).encode()

    dep_body = json.dumps({
        "properties": {
            "deploymentType": "Managed",
            "protocols": [{"protocol": "Responses", "version": "1.0"}],
            "agents": [{"agentName": agent_name, "agentVersion": str(agent_version)}],
        }
    }).encode()

    def arm_put(url: str, body: bytes, label: str) -> dict:
        req = urllib.request.Request(url, data=body, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="replace")
            raise RuntimeError(f"{label} failed [{exc.code}]: {err_body}") from exc

    log.info("Upserting Agent Application '%s'...", agent_name)
    if not dry_run:
        result = arm_put(app_url, app_body, "Agent Application")
        state = result.get("properties", {}).get("provisioningState", "unknown")
        log.info("Agent Application provisioningState: %s", state)

    log.info("Upserting Agent Deployment for '%s' → version %s...", agent_name, agent_version)
    if not dry_run:
        result = arm_put(dep_url, dep_body, "Agent Deployment")
        state = result.get("properties", {}).get("provisioningState", "unknown")
        log.info("Agent Deployment provisioningState: %s", state)

    log.info(
        "Agent published. Invoke via: POST %s/applications/%s/protocols/openai/v1/responses",
        base.replace(f"{_ARM_BASE}/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.CognitiveServices/accounts/{account_name}", ""),
        agent_name,
    )


def main():
    load_dotenv()  # no-op in CI; loads .env for local dev

    parser = argparse.ArgumentParser(description="Deploy a Foundry agent")
    parser.add_argument("--env",   default=os.environ.get("ENVIRONMENT", "dev"))
    parser.add_argument("--agent", default=os.environ.get("AGENT_CONFIG_DIR", _DEFAULT_AGENT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    agent_dir = Path(args.agent) if Path(args.agent).is_absolute() else _AGENTS_ROOT / args.agent

    config        = load_config(agent_dir, args.env)
    name          = config["name"]
    display_name  = config.get("display_name", name)
    description   = config.get("description", display_name)
    instructions  = load_instructions(agent_dir, config)
    model         = config.get("model") or require_env("OPENAI_DEPLOYMENT_NAME")
    endpoint      = require_env("FOUNDRY_PROJECT_ENDPOINT")
    starter_prompts = config.get("starter_prompts", [])

    print(f"\n  Deploying '{name}' ({display_name}) → {args.env.upper()}  |  model: {model}\n")

    if args.dry_run:
        print("  DRY RUN — no changes made.")
        return

    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True)

    metadata = {
        "welcomeMessage": display_name,
        "environment":    args.env,
        "deployed_by":    "ci-cd-pipeline",
        "version":        os.environ.get("GITHUB_SHA", "local"),
    }
    if starter_prompts:
        metadata["starterPrompts"] = "\n".join(starter_prompts[:3])

    agent  = client.agents.create_version(
        agent_name=name,
        definition=PromptAgentDefinition(model=model, instructions=instructions),
        description=description or None,
        metadata=metadata,
    )

    print(f"  Done. Agent ID: {agent.id}  (version {agent.version})")

    # ── Publish as Agent Application ──────────────────────────────────────────
    subscription_id  = os.environ.get("AZURE_SUBSCRIPTION_ID")
    resource_group   = os.environ.get("FOUNDRY_RESOURCE_GROUP")
    account_name     = os.environ.get("FOUNDRY_ACCOUNT_NAME")
    project_name_arm = os.environ.get("FOUNDRY_PROJECT_NAME")

    arm_vars_present = all([subscription_id, resource_group, account_name, project_name_arm])
    if arm_vars_present:
        try:
            publish_agent_application(
                credential=credential,
                subscription_id=subscription_id,
                resource_group=resource_group,
                account_name=account_name,
                project_name=project_name_arm,
                agent_name=name,
                agent_version=agent.version,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            # Non-fatal: the agent version is deployed even if publish fails.
            # Publishing may fail if the Agent Applications feature is not yet
            # available in the target region (preview roll-out).
            log.warning("Agent Application publish failed (non-fatal): %s", exc)
            log.warning("Re-run the pipeline or publish manually via the Foundry portal.")
    else:
        log.info(
            "Skipping Agent Application publish — set FOUNDRY_RESOURCE_GROUP, "
            "FOUNDRY_ACCOUNT_NAME, FOUNDRY_PROJECT_NAME to enable."
        )

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"agent_id={agent.id}\n")
            f.write(f"agent_name={agent.name}\n")
            f.write(f"agent_version={agent.version}\n")


if __name__ == "__main__":
    main()
