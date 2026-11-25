# AGENTS.md — Development Guidelines for `osm-polygons`

Guidelines and invariants for `osm-polygons`. Detailed runbooks live in `.agents/skills/` (`duckdb-geoparquet`, `azure-pipeline-ops`, `geofabrik-index-sync`).

---

## Architecture & Pipeline Conventions (`polygon-export-pipeline.yml`)

1. **Upstream Dependency (`osm-tools`):**
   - `osm-polygons` relies on `mirror.gcr.io/krizleebear/osm-tools:master-<SHA>` (providing `/simplify.sh` via `CoverageSimplifier`).
   - Whenever `osm-tools` `master` changes, immediately update the commit SHA in `polygon-export-pipeline.yml` and `polygon-release-pipeline.yml`.
2. **Full Region Export Invariance (4-Way Sync):**
   - Never disable or comment out region entries in the `export` job matrix unless explicitly instructed.
   - Keep all configured region definitions (currently 197) synchronized across `polygon-export-pipeline.yml` (`export` matrix and `simplify` `REGIONS`/`ARTIFACT_PATTERNS`), `osm-download-pipeline.yml`, and `scripts/countries.json` (SSOT for `index.json` and expected `admin_level`s). See `geofabrik-index-sync` skill.
3. **Continental Simplification Partitioning:**
   - Partition `simplify` matrix jobs by continent / heavy regions (`africa`, `asia`, `france`, `germany`, `great_britain`, `ireland`, `europe_rest`, `australia_oceania`, `central_america`, `north_america`, `south_america`, `canada`, `us`, `russia`) to prevent runner timeouts.
4. **2 GiB GitHub Release Limit & Partitioning:**
   - GitHub Releases enforce a hard 2 GiB limit per asset; keep heavy regions (e.g. `russia`) partitioned into separate Parquet datasets (`admin-polygons-russia.parquet`).
   - Integrated `release` stage (`v3.8.$(Date:yyyyMMdd)$(Rev:.r)`) and standalone `polygon-release-pipeline.yml` generate `index.json` from `scripts/countries.json`.

---

## Spatial, DuckDB & Data Invariants

*(See `duckdb-geoparquet` skill for SQL templates, metadata keys, and `httpfs` diagnostic queries.)*

1. **Bounded Hilbert Sorting & Dateline-Safe Anchors:**
   - All polygon, linestring, and facility exports MUST sort with bounded Hilbert indexing:
     `ORDER BY continent, country_code, ST_Hilbert(ST_PointOnSurface(geom), ST_Extent(ST_Extent_Agg(geom) OVER ()))`
   - **Never call unbounded `ST_Hilbert(geom)`** (corrupts Float32 bit-interleaving and Row-Group locality).
   - **Never use `ST_Centroid` for anchors or Hilbert keys** (antimeridian-split features like Taveuni get misplaced centroids; always use `ST_PointOnSurface`).
2. **Uniform BBox Schema, Row Groups & Provenance:**
   - Include `bbox_minx`, `bbox_miny`, `bbox_maxx`, `bbox_maxy` (`DOUBLE`) and `ROW_GROUP_SIZE 5000` (or `50000` for high-volume layers).
   - Embed standard OSM/ODbL provenance keys in Parquet `KV_METADATA` on all exports and post-processing rewrites.
3. **DuckDB Template & Buffer Rules:**
   - Use `sed` token substitution (`__INPUT_PBF__`, `__OUTPUT_PARQUET__`) for `COPY ... TO` paths (never `getvariable()`).
   - Configure generous `maximum_object_size` headroom for sprawling multipolygons in JSONL/GeoJSON ingestion.
   - Deduplicate multi-extract consolidations via `ROW_NUMBER() OVER (PARTITION BY osm_type, osm_id ORDER BY admin_level)` (polygons) and `PARTITION BY osm_id` (places).
4. **Osmium & PBF Extraction Rules:**
   - Scan large raw PBFs only ONCE (combined `osmium tags-filter` for admin boundaries + place nodes, then delete raw PBF immediately).
   - Always pass `--config=osmium-export-config.json` to `osmium export` so `osm_id` is preserved.
   - Drive geometry simplification strictly by intrinsic geometric complexity (vertex count/scale), never by semantic domain categories (`feature_class`, `area_type`).
5. **Stream Formats & Country Ground Truth:**
   - `*.admin-polygons.geojsonseq`: RFC 8142 GeoJSON Feature objects. `*.places.jsonl` / `*.facilities.jsonl`: flattened tabular JSONL for direct DuckDB `read_json()` ingestion (never misname `.jsonl` as `.geojsonseq`).
   - Verify `admin_level` completeness against `scripts/countries.json` (`levels`), not hardcoded L4 assumptions (e.g. `IS`, `CY`, `XK`, `LV`, `JM` use L5).

---

## Git, Security & CI Verification

*(See `azure-pipeline-ops` skill for `azure_pipeline.py`, YAML conditions, and Docker/mirror rules.)*

1. **Pre-Commit Verification (Mandatory):**
   - Validate pipeline YAML before committing:
     `python3 scripts/validate_azure_yaml.py <pipeline.yml>` (or via Docker `python:3-alpine` + `pyyaml`).
   - Run full unit tests in an isolated stdlib-only venv (matching the `osmium-tool` CI container):
     `./scripts/run_tests.sh` (or `PYTHONPATH=scripts python3 -m unittest discover -s scripts -p 'test_*.py'`).
   - Verify scripts/changes inside the local Docker container environment; keep complex validation in standalone testable Python CLIs (`scripts/validate_*.py`), not long inline Bash loops.
2. **Git Workflow & Explain-First Rule:**
   - Check `git diff origin/master` before committing; use Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`); test major pipeline refactors on feature branches first.
   - **Explain First, Commit Second:** Always present the diagnosis, rationale, and exact changes in visible response text before requesting/running `git commit`, `git push`, or pipeline triggers.
   - **Devcontainer `safe.directory`:** Use `git -c safe.directory=/app <command>` if Git reports dubious ownership; never edit host-mounted `~/.gitconfig` or use `sudo`.
3. **Runtime, Security & Logging Standards:**
   - Never escalate to root (`--user 0:0`, `sudo`) or install packages at runtime (`apt-get install`).
   - **Transparent Failure Policy:** Never mask missing artifacts, swallow errors, or silently fall back to external URLs; fail explicitly with clear English diagnostics.
   - **Language Hierarchy:** **Java > Python > Bash > others** (no unapproved languages like Perl).
   - Limit searches/greps strictly to active workspace directories (`osm-polygons`, `osm-tools`, `docker-osmium-tool`).
   - Prefer long-form CLI flags (`--continue-at -`) and evidence-based diagnostics (`duckdb` + `httpfs` against live releases) over assumptions.
