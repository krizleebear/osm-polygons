#!/usr/bin/env bash
set -euo pipefail

# Run the Python unit test suite in an isolated stdlib-only venv (--without-pip),
# exposing only `osmium` (which is present in the `osmium-tool` CI container),
# so accidental third-party imports such as PyYAML fail locally just like in CI.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$(mktemp -d /tmp/osm-polygons-stdlib-venv.XXXXXX)"
trap 'rm -rf "$VENV_DIR"' EXIT

python3 -m venv --without-pip "$VENV_DIR"

VENV_SITE="$("$VENV_DIR/bin/python" -c "import sysconfig; print(sysconfig.get_path('purelib'))")"
OSMIUM_DIR="$(python3 -c "import os, osmium; print(os.path.dirname(osmium.__file__))" 2>/dev/null || true)"
if [ -n "$OSMIUM_DIR" ] && [ -d "$OSMIUM_DIR" ]; then
  ln -s "$OSMIUM_DIR" "${VENV_SITE}/osmium"
fi

PYTHONPATH="${REPO_ROOT}/scripts" "$VENV_DIR/bin/python" -m unittest discover -s "${REPO_ROOT}/scripts" -p 'test_*.py' "$@"
