# GitOps Deploy: Gitea -> Portainer

Automated deploy pipeline: push to the Gitea remote, Portainer detects the
push via webhook, pulls the repo, rebuilds the image, and redeploys the
`commander-lab` stack. No manual SSH/build steps required after setup.

## How it works

1. `git push gitea main` pushes to the self-hosted Gitea repo
   (`git.lan` / `192.168.50.3:3004`, owner `andrewgari`, repo `commander-lab`).
2. Gitea fires a `push` webhook (configured on the repo, filtered to `main`)
   at a Portainer stack-webhook URL.
3. Portainer's Git-stack (`repositoryURL` pointing at the same Gitea repo,
   `autoUpdate.webhook: true`) receives the call, re-clones `main`, and runs
   `docker compose up -d --build` using `docker-compose.prod.yml`.
4. `commander-lab-app` and `commander-lab-redis` are recreated in place.

This uses Portainer's native Git-stack auto-update feature — Portainer builds
the image itself from the Dockerfile in the repo. There is no separate
registry push step and no webhook-receiver script to maintain.

## One-time setup

Requires the array/Docker/Gitea/Portainer to all be up on Tower (`mdState`
must be `STARTED`, `DOCKER_ENABLED=yes` in Unraid Settings > Docker).

1. Generate a Portainer API access token:
   Portainer UI (`http://192.168.50.3:9009`) -> user icon (top right) ->
   My Account -> Access Tokens -> Add access token. Copy the token string
   (shown once).
2. A Gitea API token already exists for this repo (`andrewgari`, token name
   `automation`, scope `all`). Generate a new one if needed:
   ```bash
   ssh tower "docker exec -u git gitea gitea admin user generate-access-token \
     --username andrewgari --token-name automation --scopes all"
   ```
3. Run the setup script from the repo root:
   ```bash
   PORTAINER_TOKEN=<portainer_token> GITEA_TOKEN=<gitea_token> \
     ./scripts/setup_portainer_gitops.sh
   ```
   This creates the `commander-lab` stack in Portainer (Git repository type,
   pointed at `docker-compose.prod.yml`, webhook auto-update enabled), reads
   `ARCHIDEKT_USERNAME` / `ARCHIDEKT_SESSION` / `ARCHIDEKT_CSRF` from your
   local `.env` (or pass them as env vars to the script) to seed the stack's
   environment variables, and registers a Gitea webhook on `push` to `main`
   pointing at the Portainer stack's webhook URL.
4. Verify: `git push gitea main` (or `make deploy-push`), then watch
   Portainer's stack "Updates" log or `ssh tower "docker ps --format
   '{{.Names}}\t{{.Status}}' | grep commander-lab"` — the app container should
   restart with a fresh image within ~30-60s of the push.

Re-running `setup_portainer_gitops.sh` is safe to do again if credentials
were wrong the first time, but it will error with a name conflict if the
stack already exists — delete the existing stack in the Portainer UI first
if you need to recreate it.

## Day-to-day usage

```bash
git add -A && git commit -m "..."
git push gitea main        # or: make deploy-push
```

That's it — Portainer rebuilds and redeploys automatically. GitHub (`origin`)
pushes are unaffected; they only run the existing GitHub Actions CI
(`.github/workflows/ci.yml`), which lints/tests and (on its own) builds/pushes
to GHCR — that is a separate, independent path and does not touch Tower.

## Manual fallback

The old build-locally-on-Tower-and-push-to-the-local-registry path still
works if the Portainer webhook path is ever broken:

```bash
make deploy       # runs scripts/deploy_tower.sh
```

## Files

- `docker-compose.prod.yml` — production compose file Portainer builds from.
  Differs from `docker-compose.yml` (local dev) by dropping the Redis host
  port publish, dropping `--reload`, and adding Unraid UI labels.
- `scripts/setup_portainer_gitops.sh` — one-time Portainer stack + Gitea
  webhook provisioning script.
- `scripts/deploy_tower.sh` — legacy manual remote-build-and-push script,
  kept as a fallback.

## Troubleshooting

- **Portainer stack shows "unable to pull repository"**: confirm the repo
  is public in Gitea, or add `repositoryAuthentication: true` with a Gitea
  token to the stack (edit in Portainer UI: Stack > Git repository info).
- **Webhook fires but nothing redeploys**: check Portainer's stack "Updates"
  tab for the last webhook call's timestamp/status. A 404 there usually means
  the webhook id in the Gitea hook config is stale (stack was deleted and
  recreated) — rerun the setup script or update the Gitea hook URL manually.
- **Docker/Gitea/Portainer all down after an Unraid reboot**: the array must
  be started from the Unraid web UI (`Main` tab) before anything comes back.
  See the `unraid-portainer-deployment` skill for the full recovery runbook.
