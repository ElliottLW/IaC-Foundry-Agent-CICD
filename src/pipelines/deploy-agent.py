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


def main():
    load_dotenv()  # no-op in CI; loads .env for local dev

    parser = argparse.ArgumentParser(description="Deploy a Foundry agent")
    parser.add_argument("--env",   default=os.environ.get("ENVIRONMENT", "dev"))
    parser.add_argument("--agent", default=os.environ.get("AGENT_CONFIG_DIR", _DEFAULT_AGENT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    agent_dir = Path(args.agent) if Path(args.agent).is_absolute() else _AGENTS_ROOT / args.agent

    config       = load_config(agent_dir, args.env)
    name         = config["name"]          # slug: alphanumeric + hyphens, ≤63 chars
    display_name = config.get("display_name", name)
    description  = config.get("description", display_name)
    instructions = load_instructions(agent_dir, config)
    model        = config.get("model") or require_env("OPENAI_DEPLOYMENT_NAME")
    endpoint     = require_env("FOUNDRY_PROJECT_ENDPOINT")

    print(f"\n  Deploying '{name}' ({display_name}) → {args.env.upper()}  |  model: {model}\n")

    if args.dry_run:
        print("  DRY RUN — no changes made.")
        return

    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential(), allow_preview=True)
    agent  = client.agents.create_version(
        agent_name=name,
        definition=PromptAgentDefinition(model=model, instructions=instructions),
        description=description or None,
        metadata={
            "display_name":  display_name,
            "environment":   args.env,
            "deployed_by":   "ci-cd-pipeline",
            "version":       os.environ.get("GITHUB_SHA", "local"),
        },
    )

    print(f"  Done. Agent ID: {agent.id}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"agent_id={agent.id}\n")
            f.write(f"agent_name={agent.name}\n")


if __name__ == "__main__":
    main()
