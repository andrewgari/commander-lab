#!/usr/bin/env bash
set -euo pipefail

TOWER_HOST="tower"
REMOTE_BUILD_DIR="/tmp/commander-lab-build"
IMAGE_TAG="${1:-latest}"

echo "Pushing latest commits to Gitea..."
git push gitea main

echo "Building Docker image on Tower (${TOWER_HOST})..."
ssh "${TOWER_HOST}" "rm -rf ${REMOTE_BUILD_DIR} && \
  git clone http://127.0.0.1:3004/CovaDax/commander-lab.git ${REMOTE_BUILD_DIR} && \
  cd ${REMOTE_BUILD_DIR} && \
  docker build -t commander-lab:${IMAGE_TAG} -t commander-lab:latest ."

echo "Cleaning up temporary build artifacts..."
ssh "${TOWER_HOST}" "rm -rf ${REMOTE_BUILD_DIR}"

echo "Docker image commander-lab:${IMAGE_TAG} successfully built and available on Tower!"
