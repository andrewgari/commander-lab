# Prod deploy: GitHub main -> Portainer Git stack (Tower)

Verified 2026-10-12 (AN-0, kanban t_b54f852f) by deploying main 7181416.

## What prod actually is

- Host: Tower (Unraid, 192.168.50.3). Portainer UI/API: http://192.168.50.3:9009
- Portainer stack id 124, name `commander-lab`, type Git repository:
  - repository: https://github.com/andrewgari/commander-lab (GitHub, NOT Gitea)
  - ref: `refs/heads/main`, compose file: `docker-compose.prod.yml`
  - auto-update: webhook only (no polling interval). A trigger re-pulls and rebuilds.
- Containers: `commander-lab-app` (built from the repo Dockerfile, host port
  8020 -> 8000) and `commander-lab-redis` (redis:alpine, volume `redis_data`).
- Live site: http://192.168.50.3:8020 (Tower's port 8000 is a different service).
- Portainer checks out each deployed commit to
  `/mnt/user/appdata/portainer/compose/124/<full-sha>/` on Tower. That dir name
  (and the container label in step 5) is the ground truth for which sha is live.

**Nothing fires the webhook automatically.** GitHub has no repo webhook (it
can't reach the LAN anyway) and the Gitea mirror
(`git@git.lan:CovaDax/commander-lab`) is not wired to this stack. Merging to
main does NOT deploy; you have to trigger the webhook yourself.

## Deploy procedure

1. Merge to main through a PR (`env -u GITHUB_TOKEN gh pr merge <n> --merge`)
   once the required "Test & Lint" check is green. The pre-push hook blocks
   direct pushes to main.
2. Wait about 60-90s for the Release workflow to push its
   `chore: bump version to vX.Y.Z [skip ci]` commit to main, and deploy after
   that so the image carries the bumped `version.py`. Confirm with
   `git fetch origin && git log --oneline -1 origin/main`.
3. If the deploy includes data migrations, back up Redis first (see below).
4. Trigger the stack webhook. It's an unauthenticated LAN endpoint; the id is
   stored on Tower (root-only) at `/mnt/user/appdata/commander-lab/portainer-webhook`:

   ```bash
   make deploy-push
   # equivalent to:
   WH=$(ssh tower cat /mnt/user/appdata/commander-lab/portainer-webhook)
   curl -fsS -X POST http://192.168.50.3:9009/api/stacks/webhooks/$WH   # HTTP 204
   ```

   Portainer clones main, rebuilds the app image, and recreates both
   containers. Redis restarts too, but its data persists in `redis_data`.
   The whole thing takes about 30-60s.
5. Verify the live sha:

   ```bash
   ssh tower 'docker inspect commander-lab-app --format \
     "{{index .Config.Labels \"com.docker.compose.project.working_dir\"}} {{.Created}}"'
   # -> /data/compose/124/<sha of origin/main> <fresh timestamp>
   ```

6. Smoke test at http://192.168.50.3:8020: /instances (TOTAL ~5608),
   /inventory (has rows), and a deck page (/deck/Holy%20Hellfire shows CARDS 100).

The version badge (`/api/version`, sidebar) is NOT proof of which build is
live. It comes from a cached GitHub releases lookup (Redis keys
`github_latest_version` / `github_latest_version_stale`) and can lag behind the
image's `version.py`. Use the working_dir label from step 5 instead.

## Redis backup (before any migration)

```bash
ssh tower 'set -e
  docker exec commander-lab-redis redis-cli BGSAVE
  sleep 5; docker exec commander-lab-redis redis-cli INFO persistence | grep rdb_last_bgsave_status  # ok
  D=/mnt/user/appdata/commander-lab-backups; mkdir -p $D
  docker cp commander-lab-redis:/data/dump.rdb $D/dump-$(date +%Y%m%d-%H%M%S).rdb
  ls -la $D'
```

`/mnt/user/backups` is not a directory on Tower, so don't put backups there.
To restore: stop `commander-lab-app`, stop `commander-lab-redis`, `docker cp`
the rdb to `commander-lab-redis:/data/dump.rdb`, then start redis and the app.

## Running migrations / one-off scripts against prod

`scripts/` ships inside the image and `REDIS_URL` already points at prod Redis,
so you can run scripts in the app container. Do a dry run, then `--apply`, then
another dry run to confirm there's nothing left to change:

```bash
ssh tower 'docker exec commander-lab-app python scripts/dedupe_instances.py'          # dry run
ssh tower 'docker exec commander-lab-app python scripts/dedupe_instances.py --apply'
```

History: on 2026-10-12, right after deploying 7181416, `dedupe_instances.py`
removed 5608 duplicates (11216 -> 5608, batch 2026-09-25) and
`remove_orphan_decks.py` removed `test-deck`. Pre-migration backup:
`/mnt/user/appdata/commander-lab-backups/dump-pre-AN0-20261012-013505.rdb`.

## Rollback

Portainer always builds the tip of main, so to roll back code, revert on main
(PR + merge) and trigger the webhook again. To roll back data, restore an rdb
backup as described above.

## Files

- `docker-compose.prod.yml`: the compose file Portainer builds from. Compared
  with `docker-compose.yml` (local dev), it drops the Redis host port publish
  and `--reload`, and adds Unraid UI labels.
- `Makefile` `deploy-push`: triggers the stack 124 webhook.

## Legacy / stale paths (do not use)

- Gitea -> Portainer webhook (`scripts/setup_portainer_gitops.sh`,
  `make gitops-setup`): this was the plan in an earlier version of this doc,
  but stack 124 doesn't use it. The Gitea mirror's main is stale (908b4cd as of
  2026-10-12), and pushing to Gitea deploys nothing.
- `make deploy` / `scripts/deploy_tower.sh`: the old build-on-Tower and
  push-to-local-registry (192.168.50.3:5000) path, which bypasses stack 124.
- GHCR image from `.github/workflows/ci.yml` ("Build & Push Docker Image"):
  it's built on main pushes, but prod builds from source and never pulls it.

## Troubleshooting

- **Webhook returns 404**: the stack was deleted and recreated, so its webhook
  id changed. In the Portainer UI, open Stacks > commander-lab > GitOps
  updates, copy the new webhook URL, and update
  `/mnt/user/appdata/commander-lab/portainer-webhook` on Tower.
- **Webhook returns 204 but the working_dir sha doesn't change**: check the
  stack's deployment status in the Portainer UI. A build failure leaves the
  old containers running.
- **Docker/Portainer all down after an Unraid reboot**: start the array from
  the Unraid web UI (`Main` tab) first. The `unraid-portainer-deployment`
  skill has the full recovery runbook.
