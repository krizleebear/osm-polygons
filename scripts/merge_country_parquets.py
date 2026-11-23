#!/usr/bin/env python3
"""Merge per-region temporary GeoParquet files into per-country GeoParquet outputs.

Replaces the per-country inline DuckDB loop that re-read every temp Parquet file
for every country code. Performs one column-pruned discovery scan and one footer
metadata scan per layer, then executes all per-country COPY statements inside a
single DuckDB batch session reading only the temp file(s) matching each country.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

COUNTRY_CODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

LAYER_CONFIGS = {
    "admin-polygons": {
        "output_prefix": "admin-polygons",
        "dataset": "OpenStreetMap Administrative Polygons",
        "dedup_expr": (
            "ROW_NUMBER() OVER (PARTITION BY osm_type, osm_id "
            "ORDER BY admin_level, (center_lat IS NULL), (parent_osm_id IS NULL), ST_Area(geom) DESC)"
        ),
        "order_by": "admin_level, bbox_miny, bbox_minx",
    },
    "places": {
        "output_prefix": "osm-places",
        "dataset": "OpenStreetMap Places",
        "dedup_expr": "ROW_NUMBER() OVER (PARTITION BY osm_id ORDER BY place_type)",
        "order_by": "place_type, lat, lon",
    },
    "facilities": {
        "output_prefix": "osm-facilities",
        "dataset": "OpenStreetMap Facilities",
        "dedup_expr": "ROW_NUMBER() OVER (PARTITION BY osm_type, osm_id ORDER BY feature_class)",
        "order_by": (
            "continent, country_code, "
            "ST_Hilbert(ST_PointOnSurface(geom), ST_Extent(ST_Extent_Agg(geom) OVER ()))"
        ),
    },
    "landuse": {
        "output_prefix": "osm-landuse",
        "dataset": "OpenStreetMap Landuse",
        "dedup_expr": "ROW_NUMBER() OVER (PARTITION BY osm_type, osm_id ORDER BY landuse)",
        "order_by": (
            "continent, country_code, "
            "ST_Hilbert(ST_PointOnSurface(geom), ST_Extent(ST_Extent_Agg(geom) OVER ()))"
        ),
    },
    "postal-codes": {
        "output_prefix": "osm-postal-codes",
        "dataset": "OpenStreetMap Postal Codes",
        "dedup_expr": "ROW_NUMBER() OVER (PARTITION BY osm_type, osm_id ORDER BY postal_code)",
        "order_by": (
            "continent, country_code, "
            "ST_Hilbert(ST_PointOnSurface(geom), ST_Extent(ST_Extent_Agg(geom) OVER ()))"
        ),
    },
}


def sql_quote(value: str) -> str:
    """Wrap a string in SQL single quotes with internal quotes doubled."""
    return "'" + str(value).replace("'", "''") + "'"


def validate_country_code(cc: str) -> str:
    """Validate country_code against empty values, path traversal, and control characters."""
    if cc is None:
        raise ValueError("Country code must not be None")
    cleaned = str(cc).strip()
    if not cleaned:
        raise ValueError("Country code must not be empty")
    if "\x00" in cleaned or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise ValueError(f"Invalid control character in country code: {cc!r}")
    if "/" in cleaned or "\\" in cleaned or ".." in cleaned:
        raise ValueError(f"Path traversal characters not allowed in country code: {cc!r}")
    if not COUNTRY_CODE_RE.fullmatch(cleaned):
        raise ValueError(f"Invalid country code format: {cc!r}")
    return cleaned


def _list_parquet_files(input_dir: str) -> list[str]:
    if not os.path.isdir(input_dir):
        return []
    return sorted(glob.glob(os.path.join(input_dir, "*.parquet")))


def _run_duckdb_json(query: str) -> list[dict]:
    res = subprocess.run(
        ["duckdb", "-no-init", "-json", "-c", query],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"DuckDB query failed (exit {res.returncode}): {res.stderr.strip() or res.stdout.strip()}"
        )
    stdout = res.stdout.strip()
    if not stdout:
        return []
    return json.loads(stdout)


def discover_country_files(input_dir: str) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Discover per-country source Parquet files and raw feature counts in one scan."""
    parquet_files = _list_parquet_files(input_dir)
    if not parquet_files:
        return {}, {}

    glob_pattern = sql_quote(os.path.join(input_dir, "*.parquet"))
    query = (
        "SELECT country_code, filename, COUNT(*) AS cnt "
        f"FROM read_parquet({glob_pattern}, filename=true) "
        "WHERE country_code IS NOT NULL AND country_code != '' "
        "GROUP BY country_code, filename "
        "ORDER BY country_code, filename;"
    )
    rows = _run_duckdb_json(query)

    country_files: dict[str, list[str]] = {}
    country_counts: dict[str, int] = {}
    for row in rows:
        cc = validate_country_code(row["country_code"])
        norm_path = os.path.normpath(row["filename"])
        cnt = int(row["cnt"])
        files_list = country_files.setdefault(cc, [])
        if norm_path not in files_list:
            files_list.append(norm_path)
        country_counts[cc] = country_counts.get(cc, 0) + cnt

    for cc in country_files:
        country_files[cc].sort()

    return country_files, country_counts


def discover_osm_timestamps(input_dir: str) -> dict[str, str]:
    """Read osm_data_timestamp from Parquet KV_METADATA across all temp files in one scan."""
    parquet_files = _list_parquet_files(input_dir)
    if not parquet_files:
        return {}

    glob_pattern = sql_quote(os.path.join(input_dir, "*.parquet"))
    query = (
        "SELECT file_name, value::VARCHAR AS ts "
        f"FROM parquet_kv_metadata({glob_pattern}) "
        "WHERE key = 'osm_data_timestamp';"
    )
    rows = _run_duckdb_json(query)

    timestamps: dict[str, str] = {}
    for row in rows:
        file_name = row.get("file_name")
        ts = (row.get("ts") or "").strip()
        if not file_name or not ts:
            continue
        norm_path = os.path.normpath(file_name)
        prev = timestamps.get(norm_path)
        if prev is None or ts > prev:
            timestamps[norm_path] = ts
    return timestamps


def resolve_country_timestamp(
    files: list[str], file_timestamps: dict[str, str], fallback_global_ts: str
) -> str:
    """Resolve the newest osm_data_timestamp across a country's source files."""
    candidates = [
        file_timestamps[os.path.normpath(path)]
        for path in files
        if file_timestamps.get(os.path.normpath(path))
    ]
    return max(candidates) if candidates else fallback_global_ts


def build_copy_statement(
    layer: str,
    country_code: str,
    source_files: list[str],
    output_path: str,
    exported_at: str,
    osm_data_timestamp: str,
) -> str:
    """Build a DuckDB COPY statement reading only the source files relevant to one country."""
    if layer not in LAYER_CONFIGS:
        raise ValueError(f"Unsupported layer: {layer!r}")
    if not source_files:
        raise ValueError(f"No source files provided for {layer} country {country_code!r}")

    cc = validate_country_code(country_code)
    cfg = LAYER_CONFIGS[layer]
    normalized_files = sorted({os.path.normpath(p) for p in source_files})
    if len(normalized_files) == 1:
        source_expr = sql_quote(normalized_files[0])
    else:
        source_expr = "[" + ", ".join(sql_quote(p) for p in normalized_files) + "]"

    return (
        "COPY (\n"
        "  SELECT * EXCLUDE (rn) FROM (\n"
        f"    SELECT *, {cfg['dedup_expr']} AS rn\n"
        f"    FROM read_parquet({source_expr})\n"
        f"    WHERE country_code = {sql_quote(cc)}\n"
        "  ) WHERE rn = 1\n"
        f"  ORDER BY {cfg['order_by']}\n"
        f") TO {sql_quote(output_path)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 5000,\n"
        "  KV_METADATA {\n"
        "    'source': 'OpenStreetMap',\n"
        "    'origin': 'OpenStreetMap (https://www.openstreetmap.org)',\n"
        f"    'dataset': {sql_quote(cfg['dataset'])},\n"
        "    'attribution': '© OpenStreetMap contributors',\n"
        "    'attribution_url': 'https://www.openstreetmap.org/copyright',\n"
        "    'license': 'ODbL-1.0 (https://opendatacommons.org/licenses/odbl/)',\n"
        "    'license_url': 'https://opendatacommons.org/licenses/odbl/',\n"
        "    'copyright': 'Data © OpenStreetMap contributors, licensed under Open Data Commons Open Database License 1.0 (ODbL)',\n"
        "    'schema': 'https://github.com/krizleebear/osm-polygons',\n"
        "    'schema_url': 'https://github.com/krizleebear/osm-polygons',\n"
        "    'compiler': 'osm-polygons (https://github.com/krizleebear/osm-polygons)',\n"
        f"    'country_code': {sql_quote(cc)},\n"
        f"    'exported_at': {sql_quote(exported_at)},\n"
        f"    'osm_data_timestamp': {sql_quote(osm_data_timestamp)}\n"
        "  }\n"
        ");\n"
    )


def build_batch_sql(
    layer: str,
    country_files: dict[str, list[str]],
    output_dir: str,
    exported_at: str,
    file_timestamps: dict[str, str],
) -> str:
    """Build a single DuckDB batch SQL script emitting one GeoParquet per country."""
    if layer not in LAYER_CONFIGS:
        raise ValueError(f"Unsupported layer: {layer!r}")

    cfg = LAYER_CONFIGS[layer]
    global_ts_candidates = [ts for ts in file_timestamps.values() if ts]
    fallback_global_ts = max(global_ts_candidates) if global_ts_candidates else exported_at

    parts = ["LOAD spatial;\n"]
    for cc in sorted(country_files.keys()):
        validated_cc = validate_country_code(cc)
        files = country_files[cc]
        output_path = os.path.join(output_dir, f"{cfg['output_prefix']}-{validated_cc}.parquet")
        country_osm_ts = resolve_country_timestamp(files, file_timestamps, fallback_global_ts)
        parts.append(f".print Writing {output_path} ({len(files)} source file(s)) ...\n")
        parts.append(
            build_copy_statement(
                layer=layer,
                country_code=validated_cc,
                source_files=files,
                output_path=output_path,
                exported_at=exported_at,
                osm_data_timestamp=country_osm_ts,
            )
        )
    return "".join(parts)


def run_batch_sql(batch_sql: str) -> None:
    """Execute a DuckDB batch script via temp file stdin with -no-init -bail."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sql", encoding="utf-8", delete=False
        ) as tmp_fp:
            tmp_fp.write(batch_sql)
            tmp_path = tmp_fp.name

        with open(tmp_path, "r", encoding="utf-8") as sql_in:
            with subprocess.Popen(
                ["duckdb", "-no-init", "-bail"],
                stdin=sql_in,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            ) as proc:
                assert proc.stdout is not None
                for line in proc.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"DuckDB batch execution failed with exit code {rc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def merge_layer(
    layer: str,
    input_dir: str,
    output_dir: str,
    exported_at: str,
    continent: str = "",
    require_non_empty: bool = False,
) -> int:
    """Discover per-country temp Parquet files for a layer and merge them in one DuckDB session."""
    if layer not in LAYER_CONFIGS:
        raise ValueError(f"Unsupported layer: {layer!r}")

    parquet_files = _list_parquet_files(input_dir)
    continent_label = f" for continent '{continent}'" if continent else ""

    if not parquet_files:
        if require_non_empty:
            print("****************************************************************", file=sys.stderr)
            print(
                f" ERROR: No GeoParquet files were produced{continent_label} in '{input_dir}'!",
                file=sys.stderr,
            )
            print("****************************************************************", file=sys.stderr)
            return 1
        print(f"[INFO] No temp {layer} Parquet files in '{input_dir}'{continent_label}. Skipping.")
        return 0

    country_files, country_counts = discover_country_files(input_dir)
    if not country_files:
        if require_non_empty:
            print("****************************************************************", file=sys.stderr)
            print(
                f" ERROR: No country features found in {layer} Parquet files{continent_label}!",
                file=sys.stderr,
            )
            print("****************************************************************", file=sys.stderr)
            return 1
        print(
            f"[INFO] Temp {layer} Parquet files in '{input_dir}' contain 0 country rows. Skipping."
        )
        return 0

    file_timestamps = discover_osm_timestamps(input_dir)
    os.makedirs(output_dir, exist_ok=True)

    total_raw = sum(country_counts.values())
    print(
        f"[INFO] Merging {layer}{continent_label}: "
        f"{len(parquet_files)} temp file(s), {len(country_files)} country/countries, "
        f"{total_raw} raw feature(s)."
    )
    summary = ", ".join(f"{cc}={country_counts[cc]}" for cc in sorted(country_counts))
    print(f"[INFO] Raw feature counts ({layer}): {summary}")

    batch_sql = build_batch_sql(
        layer=layer,
        country_files=country_files,
        output_dir=output_dir,
        exported_at=exported_at,
        file_timestamps=file_timestamps,
    )
    run_batch_sql(batch_sql)
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Merge per-region temporary GeoParquet files into per-country GeoParquet files."
    )
    parser.add_argument(
        "--layer",
        required=True,
        choices=tuple(LAYER_CONFIGS.keys()),
        help="Dataset layer to merge",
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Directory containing temporary per-region *.parquet files",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Destination directory for per-country *.parquet files",
    )
    parser.add_argument(
        "--exported-at",
        required=True,
        help="ISO-8601 UTC timestamp for exported_at KV_METADATA",
    )
    parser.add_argument(
        "--continent",
        default="",
        help="Optional continent/partition name for diagnostic logging",
    )
    parser.add_argument(
        "--require-non-empty",
        action="store_true",
        help="Exit non-zero if no temp Parquet files or country rows are found",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return merge_layer(
            layer=args.layer,
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            exported_at=args.exported_at,
            continent=args.continent,
            require_non_empty=args.require_non_empty,
        )
    except Exception as exc:
        print(f"ERROR: Failed merging layer '{args.layer}': {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
