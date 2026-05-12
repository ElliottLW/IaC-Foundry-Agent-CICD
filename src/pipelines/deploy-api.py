#!/usr/bin/env python3
"""
deploy-api.py  —  Import an APIM API from OpenAPI spec + apply policy + attach to product.

Usage:
    python deploy-api.py --api foundry-agents --env dev
    python deploy-api.py --api foundry-chat-models --env prod
    python deploy-api.py --env dev          # deploys ALL apis under src/apis/

Required environment variables (set by the GitHub Actions workflow):
    APIM_NAME            e.g. apim-lw-ai-dev
    RESOURCE_GROUP_NAME  e.g. rg-lw-ai-platform-dev
    AZURE_SUBSCRIPTION_ID
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


APIS_DIR = Path(__file__).parent.parent / "apis"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=False, text=True)


def az(*args, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["az", *args]
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True, capture_output=capture, text=True)
    return result


def deploy_api(api_dir: Path, env: str, apim: str, rg: str, sub_id: str) -> None:
    config_path  = api_dir / "config.yaml"
    openapi_path = api_dir / "openapi.yaml"
    policy_path  = api_dir / f"policy.{env}.xml"

    if not config_path.exists():
        print(f"  ⚠️  No config.yaml in {api_dir} — skipping")
        return

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    api_id       = cfg["api_id"]
    display_name = cfg["display_name"]
    description  = cfg.get("description", "")
    path         = cfg["path"]
    product_id   = cfg.get("product_id")
    backend_url  = cfg["backends"][env]

    print(f"\n{'='*60}")
    print(f"  API:         {api_id}")
    print(f"  Environment: {env}")
    print(f"  Backend:     {backend_url}")
    print(f"{'='*60}")

    # ── 1. Import / upsert the OpenAPI spec ──────────────────────────────────
    print("\n[1/3] Importing OpenAPI spec …")
    az(
        "apim", "api", "import",
        "--resource-group", rg,
        "--service-name",   apim,
        "--api-id",         api_id,
        "--display-name",   display_name,
        "--description",    description,
        "--path",           path,
        "--specification-format", "OpenApi",
        "--specification-path",   str(openapi_path),
        "--service-url",    backend_url,
        "--protocols",      "https",
    )
    print("  ✅ OpenAPI spec imported")

    # ── 2. Apply policy XML ───────────────────────────────────────────────────
    print(f"\n[2/3] Applying policy from {policy_path.name} …")
    if not policy_path.exists():
        print(f"  ⚠️  Policy file {policy_path} not found — skipping policy step")
    else:
        policy_xml = policy_path.read_text()
        body = json.dumps({
            "properties": {
                "format": "xml",
                "value":  policy_xml,
            }
        })
        url = (
            f"https://management.azure.com/subscriptions/{sub_id}"
            f"/resourceGroups/{rg}"
            f"/providers/Microsoft.ApiManagement/service/{apim}"
            f"/apis/{api_id}/policies/policy"
            f"?api-version=2022-08-01"
        )
        az("rest", "--method", "put", "--url", url, "--body", body,
           "--headers", "Content-Type=application/json")
        print("  ✅ Policy applied")

    # ── 3. Attach to product ─────────────────────────────────────────────────
    if product_id:
        print(f"\n[3/3] Attaching to product '{product_id}' …")
        result = az(
            "apim", "product", "api", "check",
            "--resource-group", rg,
            "--service-name",   apim,
            "--product-id",     product_id,
            "--api-id",         api_id,
            capture=True,
        )
        already_linked = result.returncode == 0

        if already_linked:
            print("  ℹ️  API already in product — no change needed")
        else:
            az(
                "apim", "product", "api", "add",
                "--resource-group", rg,
                "--service-name",   apim,
                "--product-id",     product_id,
                "--api-id",         api_id,
            )
            print("  ✅ API added to product")
    else:
        print("\n[3/3] No product_id in config — skipping product attachment")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy APIM APIs from src/apis/")
    parser.add_argument("--env", required=True, choices=["dev", "test", "prod"])
    parser.add_argument("--api", default=None,
                        help="Specific API folder name (omit to deploy all)")
    args = parser.parse_args()

    apim    = os.environ.get("APIM_NAME")
    rg      = os.environ.get("RESOURCE_GROUP_NAME")
    sub_id  = os.environ.get("AZURE_SUBSCRIPTION_ID")

    missing = [v for v, k in [("APIM_NAME", apim), ("RESOURCE_GROUP_NAME", rg),
                               ("AZURE_SUBSCRIPTION_ID", sub_id)] if k is None]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # Narrow types after the None-check above
    assert apim is not None
    assert rg is not None
    assert sub_id is not None

    if args.api:
        api_dirs = [APIS_DIR / args.api]
        if not api_dirs[0].is_dir():
            print(f"❌ API directory not found: {api_dirs[0]}", file=sys.stderr)
            sys.exit(1)
    else:
        api_dirs = sorted(d for d in APIS_DIR.iterdir() if d.is_dir())

    print(f"Deploying {len(api_dirs)} API(s) to {apim} ({args.env})")

    errors = []
    for api_dir in api_dirs:
        try:
            deploy_api(api_dir, args.env, apim, rg, sub_id)
        except subprocess.CalledProcessError as e:
            print(f"\n❌ Failed deploying {api_dir.name}: {e}")
            errors.append(api_dir.name)

    print(f"\n{'='*60}")
    if errors:
        print(f"❌ {len(errors)} API(s) failed: {', '.join(errors)}")
        sys.exit(1)
    else:
        print(f"✅ All {len(api_dirs)} API(s) deployed successfully")


if __name__ == "__main__":
    main()
