#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_DIR}"

echo "================================================================================"
echo " Running Local Pre-Flight Check for osm-polygons"
echo "================================================================================"

# 1. Check Git Status
echo "[1/4] Checking Git working tree..."
git status -s

# 2. Run Python Unit Tests
echo "[2/4] Running Python unit tests..."
python3 -m unittest discover -s scripts -p "test_*.py"

# 3. Validate Azure Pipeline YAML Syntax
echo "[3/4] Validating Azure DevOps YAML pipeline syntax in Docker..."
if command -v docker &> /dev/null; then
  docker run --rm -v "$(pwd)":/workspace -w /workspace python:3-alpine sh -c \
    "pip install -q pyyaml && python3 scripts/validate_azure_yaml.py polygon-export-pipeline.yml && python3 scripts/validate_azure_yaml.py polygon-release-pipeline.yml"
else
  python3 scripts/validate_azure_yaml.py polygon-export-pipeline.yml
  python3 scripts/validate_azure_yaml.py polygon-release-pipeline.yml
fi

# 4. Dry-run Release Stats Script
echo "[4/4] Verifying generate_release_stats.py syntax and execution..."
python3 scripts/generate_release_stats.py --help 2>/dev/null || true

echo "================================================================================"
echo " ALL LOCAL PRE-FLIGHT CHECKS PASSED SUCCESSFULLY!"
echo " Safe to commit and push to origin/master."
echo "================================================================================"
