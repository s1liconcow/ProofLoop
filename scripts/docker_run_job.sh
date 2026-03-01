#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <job-config-path> [output-dir] [image-tag]"
  exit 1
fi

CONFIG_PATH="$1"
OUTPUT_DIR="${2:-runs/docker-run}"
IMAGE_TAG="${3:-proofloop/devperf:latest}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Optional provider/env pass-throughs
ENV_FLAGS=()

# Pass any exported API key (e.g. OPENAI_API_KEY, FIREWORKS_API_KEY, GEMINI_API_KEY, etc.)
while IFS='=' read -r key _; do
  if [[ "$key" == *_API_KEY ]] && [ -n "${!key:-}" ]; then
    ENV_FLAGS+=("-e" "$key=${!key}")
  fi
done < <(env)

# Common provider/model endpoint knobs often needed by CLIs.
for k in \
  OPENAI_BASE_URL OPENAI_API_BASE OPENAI_MODEL \
  ANTHROPIC_BASE_URL ANTHROPIC_MODEL \
  FIREWORKS_BASE_URL FIREWORKS_MODEL \
  GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENAI_USE_VERTEXAI \
  AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_KEY AZURE_OPENAI_API_VERSION \
  OPENCODE_PROVIDER OPENCODE_MODEL OPENCODE_BASE_URL \
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_REGION; do
  if [ -n "${!k:-}" ]; then
    ENV_FLAGS+=("-e" "$k=${!k}")
  fi
done

docker run --rm \
  -v "$REPO_ROOT:/workspace" \
  -w /workspace \
  "${ENV_FLAGS[@]}" \
  "$IMAGE_TAG" \
  bash -lc "python3 -m venv venv && source venv/bin/activate && pip install -e . && proofloop-run $CONFIG_PATH --output-dir $OUTPUT_DIR"
