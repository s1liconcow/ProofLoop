#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$REPO_ROOT/examples/kvstore-perf.yaml"
OUTPUT_DIR="${1:-$REPO_ROOT/runs/kvstore-$(date +%Y%m%d-%H%M%S)}"
LOCK_DIR="${PROOFLOOP_KVSTORE_LOCK_DIR:-/tmp/proofloop-kvstore-locks}"

cd "$REPO_ROOT"

if [ ! -f venv/bin/activate ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi
source venv/bin/activate

if ! command -v proofloop-run &>/dev/null; then
  echo "Installing proofloop..."
  pip install -e . -q
fi

echo "Config:     $CONFIG"
echo "Output dir: $OUTPUT_DIR"
echo ""

mkdir -p "$LOCK_DIR"

proofloop-run "$CONFIG" --output-dir "$OUTPUT_DIR"
