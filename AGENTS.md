# AGENTS.md — Development Guidelines for `osm-polygons`

To ensure consistent pipeline execution, full geographical coverage, and clean Git workflows in `osm-polygons`, developers and AI agents must adhere to the following rules:

---

## Architecture & Project Relationships

- **Upstream Dependency (`osm-tools`):**
  `osm-polygons` relies on the Docker container built by `osm-tools` (`krizleebear/osm-tools:latest` or `mirror.gcr.io/krizleebear/osm-tools:latest`). This container provides `/simplify.sh` (powered by `CoverageSimplifier` in `osm-tools`).
- **Data Flow:**
  1. `osmium-tool` filters OSM PBF extracts into administrative boundary GeoJSON sequences (`.geojsonseq`).
  2. `osm-tools` container runs `/simplify.sh` to produce topology-preserved simplified polygons.
  3. Artifacts are bundled globally for downstream spatial geocoding indices.

---

## Pipeline Architecture & Conventions (`polygon-export-pipeline.yml`)

1. **Full Region Export Invariance:**
   - Never disable or comment out region entries in the `export` job matrix unless explicitly instructed.
   - The export matrix must retain all 169 region definitions across Africa, Asia, Europe, Oceania, Central America, North America, and South America following Geofabrik hierarchy.

2. **Continental Simplification Partitioning:**
   - Heavy simplification steps must be partitioned by continent / heavy regions in the `simplify` stage using matrix jobs (`africa`, `asia`, `france`, `germany`, `great_britain`, `ireland`, `europe_rest`, `australia_oceania`, `central_america`, `north_america`, `south_america`, `canada`, `us`, `russia`).
   - Dedicated heavyweight jobs exist for `ireland` (over 65,000 features due to ~61,800 historic townlands mapped as `admin_level=10`), `great_britain` (~12,500 parishes/wards), `france` (~35,000 communes), `germany` (~11,000 municipalities), `canada`, `us`, and `russia` to prevent runner timeouts and pipeline bottlenecks.
   - Each job filters its respective `REGIONS` list from the downloaded export artifacts and creates an archive (`simplified-$(CONTINENT).tar.gz`).


3. **Global Artifact Packaging:**
   - The `package` stage depends on `simplify`, extracts all continental archives into a unified `simplified/` folder, and publishes a single global artifact (`admin-polygons-simplified` containing `simplified.tar.gz`).

4. **Standalone Manual GitHub Release Pipeline (`polygon-release-pipeline.yml`):**
   - Releases are triggered manually on-demand (`trigger: none`).
   - Downloads the latest build artifacts, packages global (`simplified-all.tar.gz`), continental (`simplified-*.tar.gz`), and individual country (`.geojsonseq`) files, and publishes them as GitHub Release assets.

---


## Git Workflow & Cleanliness

1. **Diff Verification against Remote:**
   - Before committing or pushing, verify `git diff origin/master` to ensure no local test comments, commented-out matrix entries, or temporary scratch files are staged.
2. **Conventional Commits:**
   - Use conventional commit prefixes (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`).
3. **Container Registry Mirrors:**
   - Preserve container registry mirror configurations (`mirror.gcr.io/krizleebear/...`) in pipeline definitions.
4. **Feature Branch Pipeline Testing:**
   - Azure DevOps pipelines can be triggered directly from feature branches. For major pipeline refactorings or matrix tests, work and test on a dedicated feature branch first before merging to `master`.
5. **Transparent Failure Policy (No Hiding Errors / No Silent Fallbacks):**
   - Pipeline scripts and processing stages must never mask missing input artifacts, swallow errors, or execute silent fallbacks to raw external URLs. If an expected upstream artifact or file is missing, the script must fail explicitly with a clear, diagnostic error message detailing the missing file, root cause, and remediation steps.
6. **English Output Standard for CI/CD & Pipeline Logs:**
   - All user-facing log outputs, diagnostic error messages, pipeline notices, and CLI reports must be written strictly in clear, professional English to maintain consistency across international developer environments and automated CI/CD runners.
7. **Container Security & Dependency Invariance (No Root Elevation / No Runtime Package Install):**
   - Pipeline steps and container configurations must strictly run unprivileged and must NEVER escalate to root permissions (`--user 0:0` or `sudo`) to bypass container limitations. All required execution binaries (e.g., Python 3) must be pre-packaged directly in the container image, and pipeline steps must never perform dynamic package installation (`apt-get install`).
8. **Language Preference Hierarchy for Scripts & Tools:**
   - Select implementation languages based on the available execution environment following the priority hierarchy: **Java > Python > Bash > others**. Do not introduce unapproved secondary languages (such as Perl) outside this hierarchy.
9. **Local Clean-Room Container Verification:**
   - Before committing pipeline modifications or scripts, verify execution inside the local Docker container environment to prevent missing-dependency failures in CI runners.
10. **Workspace Boundary Scoping:**
   - Limit all grep and file searches strictly to active workspace directories (`osm-polygons`, `osm-tools`, `docker-osmium-tool`) without traversing parent directories.
11. **Mandatory Pipeline YAML Syntax Pre-Verification:**
   - Before committing modifications to pipeline definition files (`.yml`), validate full YAML structural parsing inside Docker using `scripts/validate_azure_yaml.py` (`docker run --rm -v $(pwd):/workspace -w /workspace python:3-alpine sh -c "pip install -q pyyaml && python3 scripts/validate_azure_yaml.py <pipeline.yml>"`). Commits with unverified YAML syntax are strictly prohibited.
12. **Azure DevOps Job Container Entrypoint Safety:**
   - Docker images intended for Azure DevOps job containers (`container: <image>`) must NOT define an exec-form `ENTRYPOINT` that exits on unknown arguments (such as `ENTRYPOINT ["/app/entrypoint.sh"]`), because Azure DevOps starts job containers with `sleep infinity`. Use `CMD ["/bin/bash"]` in the Dockerfile and invoke processing scripts explicitly in pipeline steps.
13. **Docker Schema 2 Manifest Requirement for `mirror.gcr.io`**:
    - Container images pushed to Docker Hub for `mirror.gcr.io` consumption must be built in Docker Schema 2 format (`application/vnd.docker.distribution.manifest.v2+json`) using standard `docker build` or `docker buildx build --provenance=false`. Modern OCI attestation/provenance blobs cause `unknown blob` 404 errors on `mirror.gcr.io`.
14. **DuckDB Script Template Substitution Invariant:**
    - DuckDB `COPY ... TO` statements require string literal paths. Do not attempt `getvariable()` inside `COPY TO`. Use `sed` token substitution (`__INPUT_PBF__`, `__OUTPUT_PARQUET__`) on SQL templates before piping into `duckdb`.
15. **Azure DevOps Parameter Condition Syntax**:
    - In Azure DevOps task/job `condition:` expressions, template parameters MUST be wrapped in `${{ eq(parameters.name, value) }}`. Raw `parameters.name` references outside `${{ }}` trigger `Unrecognized value: 'parameters'` errors.
    - In Bash scripts, handle both `"false"` and `"False"` because template expansion converts boolean false to `"False"`.
16. **Packaging Stage Dependency Safety (`condition: succeeded('simplify')`):**
    - Packaging and bundling stages (such as `stage: package`) that aggregate artifacts from upstream parallel matrix jobs MUST use `condition: succeeded('<stage>')` (e.g. `succeeded('simplify')`). Using parameterless `succeeded()` causes the stage to be skipped if any upstream stage (such as `export`) was skipped via conditional parameters (`reuseExportArtifacts`). Never use `condition: always()` on final bundling stages, as cancellation or upstream failure would trigger incomplete artifact archiving.
17. **Upstream Container Image Synchronization:**
    - Whenever modifications or optimizations are made to `osm-tools` and committed/pushed to `master`, downstream pipeline definitions (especially `resources.containers.osm-tools` in `polygon-export-pipeline.yml` and fallback commit definitions in `polygon-release-pipeline.yml`) MUST immediately and proactively be updated to reference the new commit SHA (`mirror.gcr.io/krizleebear/osm-tools:master-<SHA>`). Never leave downstream pipelines pointing to stale container versions.
18. **GeoJSONSeq Verification & Profiling with DuckDB:**
    - Use DuckDB's native vectorised `read_ndjson()` for fast ad-hoc inspection and quality verification on `.geojsonseq` stream files (e.g. `SELECT json_extract_string(properties, '$.admin_level') AS lvl, count(*) FROM read_ndjson('<file>.geojsonseq') GROUP BY 1`).
19. **GitHub Release 2 GiB Asset Size Limit & GeoParquet Partitioning:**
    - GitHub Releases enforce a strict hard limit of 2 GiB (2,147,483,648 bytes) per uploaded asset. Continental GeoParquet datasets (such as Europe with detailed sub-divisions) must be partitioned by heavy regions (e.g. isolating `russia` into `admin-polygons-russia.parquet`) to keep individual files safely under 2.0 GiB.
20. **Azure DevOps String Parameter Defaults (`latest` / `auto`):**
    - In Azure DevOps manual run dialogs, string parameters treat empty string values as invalid/required in the UI modal. String parameters (like `specificBuildId` or `exportBuildId`) must default to `'latest'` or `'auto'`, and conditions must support `'latest'`, `'auto'`, and custom build IDs.
21. **Osmium Export ID Configuration Invariant:**
    - `osmium export` omits `@id` attributes by default unless `--config=osmium-export-config.json` is explicitly passed. All place node and polygon export steps must provide the config to preserve `osm_id`.
22. **Direct DuckDB JSONL Stream Conversion:**
    - When converting structured `.places.jsonl` files to GeoParquet, stream directly using DuckDB `read_json()` / `read_json_auto()` without intermediate redundant Python filtering passes.
23. **Evidence-Based Issue Analysis & Remote DuckDB Diagnostics:**
    - Never assume an issue is fixed or make claims based solely on commit history, code reviews, or theoretical assumptions. Always gather concrete empirical evidence by directly querying the live release artifacts or test outputs.
    - Use DuckDB with `httpfs` to query remote GitHub Release assets directly (`duckdb -c "INSTALL httpfs; LOAD httpfs; SELECT ... FROM 'https://github.com/.../releases/download/.../....parquet'"`).
    - When reporting or investigating anomalies across upstream/downstream boundaries, provide reproducible SQL queries against the exact release dataset to eliminate ambiguity and immediately isolate root causes.
24. **Long-Form CLI Parameters & Download Resilience Invariant:**
    - Always use explicit, readable long-form parameters in pipeline scripts (e.g., `--continue-at -` instead of `-C -`).
    - Large external file downloads (such as Geofabrik PBF extracts) must specify stall timeouts (`--speed-limit 10240 --speed-time 30`), resume capabilities (`--continue-at -`), and emit lightweight background progress heartbeats to prevent silent runner hangs.
25. **Non-Breaking Pipeline Quality Audits (`##vso[task.logissue type=warning]`):**
    - Post-processing data audits (such as Level 2 completeness checks, feature deduplication verifications, or empty dataset checks) should emit native Azure DevOps warning annotations (`echo "##vso[task.logissue type=warning]..."`) and run with `continueOnError: true` unless hard-failing is strictly required. This highlights anomalies prominently in the Azure DevOps run summary without breaking long-running packaging pipelines.
26. **Modular & Testable Validation Tooling:**
    - Complex verification steps must never be written as long inline Bash loops inside pipeline YAML. Implement them as dedicated, standalone CLI tools in Python (e.g. `scripts/validate_parquet.py`) with accompanying unit test suites (`scripts/test_*.py`) runnable in the local Docker environment.
27. **Country-Specific Ground Truth Hierarchy Invariant:**
    - Administrative level completeness must be verified against the official OSM mapping standards defined per country in `scripts/countries.json` (`levels`). Never assume all sovereign nations define `admin_level=4` (e.g. Iceland, Cyprus, Kosovo, Latvia, and Jamaica officially use `admin_level=5` as their primary subnational level).
28. **1-Pass PBF Extraction & Pipeline Performance Invariant:**
    - Large raw PBF files (such as Brazil, Germany, USA 3-5 GB) must be scanned only ONCE. Use a single combined `osmium tags-filter` pass to extract both admin boundaries and place nodes into a combined compact PBF, delete the heavy raw input PBF immediately, and perform downstream splits and hierarchy scans (`scripts/extract_region.py`) on the small extract.
29. **Multi-Extract Feature Deduplication Invariant:**
    - When consolidating multiple regional Geofabrik extracts into a single per-country GeoParquet file (e.g. Spain + Canary Islands), DuckDB consolidation queries must deduplicate shared parent boundaries using `ROW_NUMBER() OVER (PARTITION BY osm_type, osm_id ORDER BY admin_level)` for polygons, and `ROW_NUMBER() OVER (PARTITION BY osm_id ORDER BY place_type)` for place nodes.
30. **Guaranteed Artifact Existence Invariant:**
    - Azure DevOps `PublishPipelineArtifact` tasks fail with runner warnings if the target file does not exist on disk. Export jobs and post-processing steps must ensure all declared artifact targets (such as `validation.json`) exist (touching empty fallback files if necessary) to keep build results 100% clean and green.
31. **Fast-Fail CI Preflight Invariant:**
    - Never launch long-running or matrix-heavy pipeline stages without an initial fast (<30s) preflight stage (`stage: preflight`). The preflight stage must validate pipeline YAML parsing (`scripts/validate_azure_yaml.py`) and run the full Python unit test suite (`PYTHONPATH=scripts python3 -m unittest discover -s scripts -p 'test_*.py'`). All downstream stages (`export`, `parquet`, `package`) must declare `dependsOn: preflight` to fail immediately on script regressions before runner compute is consumed.
32. **Azure DevOps Pipeline CLI Automation (`scripts/azure_pipeline.py`):**
    - Use `scripts/azure_pipeline.py` to trigger, inspect, and manage Azure DevOps pipeline builds:
      - Queue new build: `python3 scripts/azure_pipeline.py run --branch <branch_name> [--reuse-export] [--build-id <ID>]`
      - Check build status & timeline: `python3 scripts/azure_pipeline.py status <build_id>`
      - Cancel running build: `python3 scripts/azure_pipeline.py cancel <build_id>`
    - Authentication is automatically read from `AZURE_DEVOPS_PAT` in `.env` or environment variables.
33. **Stream File Format Conventions (`.geojsonseq` vs `.jsonl`):**
    - `*.admin-polygons.geojsonseq`: Strictly adheres to RFC 8142 (GeoJSON Text Sequences) where each line is a full standard GeoJSON Feature object (`{"type": "Feature", "geometry": {...}, "properties": {...}}`). Designed for direct consumption by spatial tools (GDAL/OGR, QGIS, `CoverageSimplifier`).
    - `*.places.jsonl` and `*.facilities.jsonl`: Formatted as newline-delimited flattened tabular records (`{"osm_id": ..., "country_code": ..., "name": ..., "geom_json": "...", "tags": "..."}`). Designed as high-throughput, columnar-ready ETL streams for direct vectorized ingestion via DuckDB `read_json(columns={...})`. Tabular JSONL streams must retain `.jsonl` and never be misnamed `.geojsonseq` as they lack GeoJSON Feature envelope wrappers.






