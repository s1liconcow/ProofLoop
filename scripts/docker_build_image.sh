#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="${1:-proofloop/devperf:latest}"

docker build -t "$IMAGE_TAG" -f docker/devperf/Dockerfile .
echo "Built image: $IMAGE_TAG"
