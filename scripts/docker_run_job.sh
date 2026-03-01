#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <job-config-path> [output-dir] [image-tag]"
  exit 1
fi

CONFIG_PATH="$1"
OUTPUT_DIR="${2:-runs/docker-run}"
IMAGE_TAG="${3:-auto-optimize/devperf:latest}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Optional provider key pass-throughs
ENV_FLAGS=()
for k in FIREWORKS_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY; do
  if [ -n "${!k:-}" ]; then
    ENV_FLAGS+=("-e" "$k=${!k}")
  fi
done

docker run --rm \
  -v "$REPO_ROOT:/workspace" \
  -w /workspace \
  "${ENV_FLAGS[@]}" \
  "$IMAGE_TAG" \
  bash -lc "python3 -m venv venv && source venv/bin/activate && pip install -e . && auto-optimize-run $CONFIG_PATH --output-dir $OUTPUT_DIR"
