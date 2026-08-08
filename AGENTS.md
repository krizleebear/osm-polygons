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

