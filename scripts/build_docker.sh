#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="commander-lab"
TAG="${1:-latest}"

echo "Building Docker image ${IMAGE_NAME}:${TAG}..."
docker compose build --build-arg TAG="${TAG}" app

echo "Successfully built ${IMAGE_NAME}:${TAG}"
