---
name: azure-pipeline-ops
description: >-
  Use when editing Azure DevOps YAML pipelines, triggering/inspecting builds with
  scripts/azure_pipeline.py, or updating container image references.
---

# Azure DevOps Pipeline & Container Operations

## 1. CLI Automation (`scripts/azure_pipeline.py`)
- Queue build: `python3 scripts/azure_pipeline.py run --branch <branch_name> [--reuse-export] [--build-id <ID>]`
- Check status & timeline: `python3 scripts/azure_pipeline.py status <build_id>`
- Cancel build: `python3 scripts/azure_pipeline.py cancel <build_id>`
- Auth is automatically read from `AZURE_DEVOPS_PAT` in `.env` or environment variables.

## 2. Azure DevOps YAML Conventions
- Wrap template parameters in `condition:` expressions: `${{ eq(parameters.name, value) }}` (raw `parameters.name` fails). In Bash scripts, handle both `"false"` and `"False"`.
- Packaging/bundling stages aggregating parallel matrix jobs must use `condition: succeeded('<upstream_stage>')` (never parameterless `succeeded()` or `always()`).
- String parameters in manual run dialogs (e.g. `specificBuildId`, `exportBuildId`) must default to `'latest'` or `'auto'` (never empty string).
- Non-breaking data audits should emit `echo "##vso[task.logissue type=warning]..."` with `continueOnError: true`.
- Ensure all declared `PublishPipelineArtifact` target files exist on disk (touch empty fallback files if needed).
- Large external downloads (e.g. Geofabrik PBFs) must use long-form CLI flags, stall timeouts (`--speed-limit 10240 --speed-time 30`), `--continue-at -`, and lightweight progress heartbeats.
- Never run runner disk reclamation (`sudo rm -rf /usr/share/dotnet ...`) unconditionally across all jobs in a large matrix (slow and unnecessary for small regions). Gate it dynamically by comparing `df -Pk /` available space against `curl --head` `Content-Length` + a 6% reserve (above Azure DevOps's 5% warning threshold), falling back to cleanup only when headroom is insufficient or `Content-Length` is unavailable.
- Fast-fail `preflight` stage (`validate_azure_yaml.py` + `unittest`) must run before matrix-heavy stages (`dependsOn: preflight`).

## 3. Container & Upstream Image Rules
- Preserve `mirror.gcr.io/krizleebear/...` mirrors in pipeline definitions.
- After pushing `osm-tools` changes to `master`, immediately update `mirror.gcr.io/krizleebear/osm-tools:master-<SHA>` in `polygon-export-pipeline.yml` and `polygon-release-pipeline.yml`.
- Build images for `mirror.gcr.io` in Docker Schema 2 format (`docker build` or `docker buildx build --provenance=false`) to avoid `unknown blob` 404 errors from OCI provenance blobs.
- Job containers (`container: <image>`) must use `CMD ["/bin/bash"]`, never an exec-form `ENTRYPOINT` that exits on `sleep infinity`.
