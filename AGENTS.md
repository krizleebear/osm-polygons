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
   - Heavy simplification steps must be partitioned by continent in the `simplify` stage using continent matrix jobs (`africa`, `asia`, `europe`, `australia_oceania`, `central_america`, `north_america`, `south_america`, `canada`, `us`, `russia`).
   - Each continent job filters its respective `REGIONS` list from the downloaded export artifacts and creates a continental archive (`simplified-$(CONTINENT).tar.gz`).


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
7. **Container Dependency Invariance (No Runtime Package Install):**
   - Pipeline container images must pre-install all required execution binaries (e.g., Python 3). Pipeline steps must never attempt dynamic runtime package installation (`apt-get install`) or grant root privileges (`--user 0:0`).
8. **Language Preference Hierarchy for Scripts & Tools:**
   - Select implementation languages based on the available execution environment following the priority hierarchy: **Java > Python > Bash > others**. Do not introduce unapproved secondary languages (such as Perl) outside this hierarchy.
9. **Local Clean-Room Container Verification:**
   - Before committing pipeline modifications or scripts, verify execution inside the local Docker container environment to prevent missing-dependency failures in CI runners.
10. **Workspace Boundary Scoping:**
   - Limit all grep and file searches strictly to active workspace directories (`osm-polygons`, `osm-tools`, `docker-osmium-tool`) without traversing parent directories.



