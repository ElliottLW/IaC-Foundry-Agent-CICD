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
    python src/scripts/deploy-agent.py --env dev|test|prod [--agent <dir>] [--dry-run]

Required env vars (set by Terraform outputs in CI):
    FOUNDRY_PROJECT_ENDPOINT   - Foundry project endpoint URL
    OPENAI_DEPLOYMENT_NAME     - GPT deployment name (e.g. gpt-4o)
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml  # type: ignore
from dotenv import load_dotenv # type: ignore
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


_ENDPOINT_API_VERSION = "2025-05-15-preview"
_ENDPOINT_FEATURE_HEADER = "AgentEndpoints=V1Preview"


def activate_agent_endpoint(client: "AIProjectClient", agent_name: str, dry_run: bool = False) -> str:
    """
    Ensure the agent's stable Responses endpoint is active by PATCHing its
    agent_endpoint routing configuration via the Foundry data-plane API.

    Proven working endpoint (no separate publish/Agent-Application step needed):
      POST {foundry_endpoint}/agents/{agent_name}/endpoint/protocols/openai/responses
            ?api-version=2025-05-15-preview
      Header: Foundry-Features: AgentEndpoints=V1Preview

    Returns the invocation URL.
    """
    endpoint_base = client._config.endpoint.rstrip("/")  # e.g. https://.../api/projects/aip-...
    invoke_url = (
        f"{endpoint_base}/agents/{agent_name}/endpoint/protocols/openai/responses"
        f"?api-version={_ENDPOINT_API_VERSION}"
    )

    patch_body = json.dumps({
        "agent_endpoint": {
            "version_selector": {
                "version_selection_rules": [
                    {"agent_version": "@latest", "traffic_percentage": 100, "type": "FixedRatio"}
                ]
            }
        }
    })

    log.info("Activating agent endpoint for '%s'...", agent_name)
    if not dry_run:
        from azure.core.rest import HttpRequest
        req = HttpRequest(
            method="PATCH",
            url=f"{endpoint_base}/agents/{agent_name}",
            params={"api-version": _ENDPOINT_API_VERSION},
            headers={
                "Content-Type": "application/json",
                "Foundry-Features": _ENDPOINT_FEATURE_HEADER,
            },
            content=patch_body,
        )
        resp = client.send_request(req)
        resp.raise_for_status()
        log.info("Agent endpoint active. Invoke via: POST %s", invoke_url)

    return invoke_url


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

    # ── Activate the stable Responses endpoint ─────────────────────────────────
    invoke_url = activate_agent_endpoint(client, name, dry_run=args.dry_run)
    print(f"  Endpoint: POST {invoke_url}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"agent_id={agent.id}\n")
            f.write(f"agent_name={agent.name}\n")
            f.write(f"agent_version={agent.version}\n")
            f.write(f"invoke_url={invoke_url}\n")


if __name__ == "__main__":
    main()
