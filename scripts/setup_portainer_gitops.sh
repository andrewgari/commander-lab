#!/usr/bin/env bash
# One-time setup: creates the commander-lab Portainer Git stack and wires the
# Gitea webhook so every push to gitea main triggers a Portainer rebuild+redeploy.
#
# Requires:
#   PORTAINER_TOKEN   - Portainer API access token (My Account -> Access Tokens)
#   GITEA_TOKEN       - Gitea API token with repo admin scope (already scoped 'all'
#                        works, e.g. the 'automation' token created for this repo)
#
# Usage:
#   PORTAINER_TOKEN=... GITEA_TOKEN=... ./scripts/setup_portainer_gitops.sh
set -euo pipefail

PORTAINER_URL="${PORTAINER_URL:-http://192.168.50.3:9009}"
GITEA_URL="${GITEA_URL:-http://192.168.50.3:3004}"
GITEA_OWNER="${GITEA_OWNER:-andrewgari}"
GITEA_REPO="${GITEA_REPO:-commander-lab}"
STACK_NAME="${STACK_NAME:-commander-lab}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
# Portainer runs in its own container, so it can't resolve the "gitea" container
# name unless they share a Docker network. Use Tower's LAN IP instead, which is
# reachable from any container via the host's published port.
REPO_URL="http://192.168.50.3:3004/${GITEA_OWNER}/${GITEA_REPO}.git"
GIT_BRANCH="${GIT_BRANCH:-main}"

: "${PORTAINER_TOKEN:?Set PORTAINER_TOKEN}"
: "${GITEA_TOKEN:?Set GITEA_TOKEN}"

echo "Resolving Portainer endpoint id..."
ENDPOINT_ID=$(curl -sk "${PORTAINER_URL}/api/endpoints" -H "X-API-Key: ${PORTAINER_TOKEN}" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['Id'])")
echo "Endpoint: ${ENDPOINT_ID}"

echo "Reading ARCHIDEKT env values from local .env (if present)..."
ARCHIDEKT_USERNAME="${ARCHIDEKT_USERNAME:-$(grep -E '^ARCHIDEKT_USERNAME=' .env 2>/dev/null | cut -d= -f2-)}"
ARCHIDEKT_SESSION="${ARCHIDEKT_SESSION:-$(grep -E '^ARCHIDEKT_SESSION=' .env 2>/dev/null | cut -d= -f2-)}"
ARCHIDEKT_CSRF="${ARCHIDEKT_CSRF:-$(grep -E '^ARCHIDEKT_CSRF=' .env 2>/dev/null | cut -d= -f2-)}"

echo "Creating Portainer Git-repository stack '${STACK_NAME}'..."
CREATE_RESP=$(curl -sk -X POST \
  "${PORTAINER_URL}/api/stacks/create/standalone/repository?endpointId=${ENDPOINT_ID}" \
  -H "X-API-Key: ${PORTAINER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"${STACK_NAME}\",
    \"repositoryURL\": \"${REPO_URL}\",
    \"repositoryReferenceName\": \"refs/heads/${GIT_BRANCH}\",
    \"composeFile\": \"${COMPOSE_FILE}\",
    \"repositoryAuthentication\": false,
    \"autoUpdate\": {\"webhook\": true},
    \"env\": [
      {\"name\": \"ARCHIDEKT_USERNAME\", \"value\": \"${ARCHIDEKT_USERNAME}\"},
      {\"name\": \"ARCHIDEKT_SESSION\", \"value\": \"${ARCHIDEKT_SESSION}\"},
      {\"name\": \"ARCHIDEKT_CSRF\", \"value\": \"${ARCHIDEKT_CSRF}\"}
    ]
  }")
echo "${CREATE_RESP}" | python3 -m json.tool

WEBHOOK_ID=$(echo "${CREATE_RESP}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('AutoUpdate',{}).get('Webhook',''))")
if [[ -z "${WEBHOOK_ID}" ]]; then
  echo "Could not extract webhook id from stack response. Check output above." >&2
  exit 1
fi
WEBHOOK_URL="${PORTAINER_URL}/api/stacks/webhooks/${WEBHOOK_ID}"
echo "Portainer redeploy webhook: ${WEBHOOK_URL}"

echo "Registering Gitea webhook to call Portainer on push to ${GIT_BRANCH}..."
curl -sk -X POST \
  "${GITEA_URL}/api/v1/repos/${GITEA_OWNER}/${GITEA_REPO}/hooks" \
  -H "Authorization: token ${GITEA_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"type\": \"gitea\",
    \"config\": {\"url\": \"${WEBHOOK_URL}\", \"content_type\": \"json\", \"http_method\": \"post\"},
    \"events\": [\"push\"],
    \"branch_filter\": \"${GIT_BRANCH}\",
    \"active\": true
  }" | python3 -m json.tool

echo "Done. Push to gitea main will now rebuild+redeploy the commander-lab stack."
