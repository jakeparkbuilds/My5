#!/usr/bin/env bash
# Build Lambda deployment ZIPs for the worker and API layers.
#
# Why --platform / --only-binary: we build on macOS but deploy to Amazon Linux 2023
# (x86_64). Using --platform manylinux_2_17_x86_64 forces pip to download the
# pre-built Linux wheels (not macOS .so files). Pure-Python packages (boto3, fastapi,
# mangum, etc.) use py3-none-any wheels which are platform-independent and download
# correctly under --platform as well.
#
# Output: infra/my5_worker_lambda.zip and infra/my5_api_lambda.zip
# Run from the repo root: bash scripts/build_lambdas.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$REPO_ROOT/build"
INFRA="$REPO_ROOT/infra"

PLATFORM="manylinux_2_17_x86_64"
PYVER="311"
ABI="cp${PYVER}"

# ── Helper ────────────────────────────────────────────────────────────────────
cleanup_pyc() {
  local dir="$1"
  find "$dir" -name "*.pyc" -delete 2>/dev/null || true
  find "$dir" -name "__pycache__" -type d | while IFS= read -r d; do rm -rf "$d"; done
}

# ── 1. Worker Lambda ──────────────────────────────────────────────────────────
# Contents: my5/ (engine + handlers) + numpy + boto3
# Handler: my5.aws.worker_handler.handler
echo "==> Building worker Lambda..."
WORKER_BUILD="$BUILD/worker"
rm -rf "$WORKER_BUILD" && mkdir -p "$WORKER_BUILD"

pip3 install "numpy>=1.26.0" "boto3>=1.43.30" \
  --platform "$PLATFORM" \
  --python-version "$PYVER" \
  --implementation cp \
  --abi "$ABI" \
  --only-binary=:all: \
  --quiet \
  --target "$WORKER_BUILD"

cp -r "$REPO_ROOT/src/my5" "$WORKER_BUILD/my5"
cleanup_pyc "$WORKER_BUILD"

(cd "$WORKER_BUILD" && zip -qr "$INFRA/my5_worker_lambda.zip" .)
echo "    my5_worker_lambda.zip: $(du -sh "$INFRA/my5_worker_lambda.zip" | cut -f1)"

# ── 2. API Lambda ─────────────────────────────────────────────────────────────
# Contents: my5/ + api/ + fastapi + uvicorn + mangum + boto3
# Handler: api.main.handler  (Mangum wraps the FastAPI ASGI app)
echo "==> Building API Lambda..."
API_BUILD="$BUILD/api"
rm -rf "$API_BUILD" && mkdir -p "$API_BUILD"

pip3 install "numpy>=1.26.0" "fastapi>=0.138.0" "uvicorn>=0.49.0" "mangum>=0.17.0" "boto3>=1.43.30" \
  --platform "$PLATFORM" \
  --python-version "$PYVER" \
  --implementation cp \
  --abi "$ABI" \
  --only-binary=:all: \
  --quiet \
  --target "$API_BUILD"

cp -r "$REPO_ROOT/src/my5" "$API_BUILD/my5"
cp -r "$REPO_ROOT/api" "$API_BUILD/api"
cleanup_pyc "$API_BUILD"

(cd "$API_BUILD" && zip -qr "$INFRA/my5_api_lambda.zip" .)
echo "    my5_api_lambda.zip:    $(du -sh "$INFRA/my5_api_lambda.zip" | cut -f1)"

echo "==> Done. Both ZIPs written to infra/."
