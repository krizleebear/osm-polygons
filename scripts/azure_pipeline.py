#!/usr/bin/env python3
"""
Azure DevOps Pipeline Automation CLI for osm-polygons.

Supports:
  - run: Queue a new pipeline build (supports template parameters like reuseExportArtifacts)
  - cancel: Cancel a running build
  - status: Check live status and timeline of a build
  - logs: Fetch failed step logs

Reads `AZURE_DEVOPS_PAT` from environment or local `.env` file.
"""

import os
import sys
import json
import base64
import argparse
import urllib.request
import urllib.error

ORG = "github0694"
PROJECT_ID = "f85b0393-1484-487a-b6d1-5120b54a70be"  # osm-tools
PIPELINE_DEF_ID = 4  # krizleebear.osm-polygons


def get_pat():
    """Retrieve PAT from environment or .env file."""
    pat = os.environ.get("AZURE_DEVOPS_PAT")
    if pat:
        return pat.strip()

    # Check .env in current or parent dirs
    env_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env")
    ]
    for p in env_paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("AZURE_DEVOPS_PAT="):
                        return line.split("=", 1)[1].strip("\"'")

    print("ERROR: AZURE_DEVOPS_PAT not found in environment or .env file.", file=sys.stderr)
    sys.exit(1)


def api_request(path, method="GET", body=None):
    """Execute authenticated Azure DevOps REST API call."""
    pat = get_pat()
    auth = base64.b64encode(f":{pat}".encode()).decode()
    url = f"https://dev.azure.com/{ORG}/{PROJECT_ID}/_apis/{path}"
    if "?" in url:
        url += "&api-version=7.0"
    else:
        url += "?api-version=7.0"

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json"
    }

    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content.strip() else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP Error {e.code} on {method} {url}: {err_body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)


def queue_pipeline(reuse_export=False, export_build_id="latest", branch="master"):
    """Queue a new run for the polygon-export pipeline."""
    template_params = {
        "reuseExportArtifacts": "true" if reuse_export else "false",
        "exportBuildId": str(export_build_id)
    }

    body = {
        "definition": {"id": PIPELINE_DEF_ID},
        "sourceBranch": f"refs/heads/{branch}" if not branch.startswith("refs/") else branch,
        "templateParameters": template_params
    }

    print(f"Queueing pipeline run (reuseExportArtifacts={template_params['reuseExportArtifacts']}, exportBuildId={template_params['exportBuildId']})...")
    res = api_request("build/builds", method="POST", body=body)
    build_id = res.get("id")
    web_url = res.get("_links", {}).get("web", {}).get("href")
    print(f"Successfully queued build #{build_id}!")
    print(f"Web URL: {web_url}")
    return build_id


def cancel_build(build_id):
    """Cancel a running build."""
    body = {"status": "Cancelling"}
    print(f"Cancelling build #{build_id}...")
    res = api_request(f"build/builds/{build_id}", method="PATCH", body=body)
    print(f"Build #{build_id} status updated to: {res.get('status')}")


def get_status(build_id):
    """Get status and stage/job breakdown of a build."""
    res = api_request(f"build/builds/{build_id}")
    status = res.get("status")
    result = res.get("result")
    print(f"Build #{build_id}: Status={status}, Result={result}")

    timeline = api_request(f"build/builds/{build_id}/Timeline")
    records = timeline.get("records", [])

    stages = [r for r in records if r.get("type") == "Stage"]
    print("\n--- Stages ---")
    for s in stages:
        print(f"  Stage: {s.get('name'):<40} | State: {s.get('state'):<10} | Result: {s.get('result')}")

    failed_jobs = [r for r in records if r.get("type") == "Job" and r.get("result") == "failed"]
    if failed_jobs:
        print("\n--- Failed Jobs ---")
        for j in failed_jobs:
            print(f"  Job: {j.get('name')}")


def main():
    parser = argparse.ArgumentParser(description="Azure DevOps Pipeline CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run command
    run_p = subparsers.add_parser("run", help="Queue a new pipeline run")
    run_p.add_argument("--reuse-export", action="store_true", help="Set reuseExportArtifacts=true")
    run_p.add_argument("--build-id", default="latest", help="exportBuildId (default: latest)")
    run_p.add_argument("--branch", default="master", help="Git branch (default: master)")

    # cancel command
    cancel_p = subparsers.add_parser("cancel", help="Cancel a build")
    cancel_p.add_argument("build_id", type=int, help="Build ID to cancel")

    # status command
    status_p = subparsers.add_parser("status", help="Get build status and timeline")
    status_p.add_argument("build_id", type=int, help="Build ID to inspect")

    args = parser.parse_args()

    if args.command == "run":
        queue_pipeline(reuse_export=args.reuse_export, export_build_id=args.build_id, branch=args.branch)
    elif args.command == "cancel":
        cancel_build(args.build_id)
    elif args.command == "status":
        get_status(args.build_id)


if __name__ == "__main__":
    main()
