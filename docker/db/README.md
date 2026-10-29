# Commander Lab PostgreSQL foundation (slice 0)

`docker/db/` is container infrastructure, not application persistence code.
The latter belongs in `src/server/infra/db/` after the repo reorg.

## Contract

- PostgreSQL **17** (official Debian bookworm image, pinned by digest),
  pgvector **0.8.2** and Apache AGE **1.6.0**, compiled from pinned upstream
  commits in a multi-stage build. AGE's upstream PG17 release tag is named
  `PG17/v1.6.0-rc0`; the installed extension version is `1.6.0`.
- One `db` service / `commander-lab-db` container, no published host port.
  Stack services will use `db:5432`, database/user `commander_lab`.
- No app DB wiring, ORM schema, data import, or Redis cutover in this slice.
  Redis and existing app/worker behavior stay unchanged.
- AGE is preloaded for all sessions via the image CMD. Init enables both
  extensions in `POSTGRES_DB` and sets its persistent search path to
  `ag_catalog, "$user", public` (pgvector remains in `public`).
- Init runs **only on empty PGDATA**. New databases created later must run
  `00-extensions.sql` explicitly as a superuser. `CREATE EXTENSION` alone
  does not set the AGE session hooks/search path on arbitrary other servers.
- The stock `POSTGRES_USER` is a bootstrap **superuser**. Do not expose it
  publicly or treat it as a future least-privilege application role; create
  restricted roles/grants alongside the application migrations.

## Development

Generate a nonempty secret into the ignored root `.env` (keep it out of git):

```sh
# Append once; do not overwrite other existing app settings in .env.
(umask 077; printf 'COMMANDER_DB_PASSWORD=%s\n' "$(openssl rand -hex 32)" >> .env)
chmod 600 .env
docker compose build db
docker compose up -d --wait db
docker compose exec -T db sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /usr/local/share/commander-db/smoke.sql'
```

The dev compose uses the project-scoped `pgdata` named volume. The password
has no insecure default; a missing/empty value fails at initdb. Changing the
environment after initialization does **not** rotate an existing DB password
(use `ALTER ROLE` and update the secret in coordination with clients).

## Tower / Portainer

The production `db` service references a **prebuilt local image**, with
`pull_policy: never` and no `build:` for this service. The existing GitOps
flow for app/worker is unchanged. Before the merged compose is deployed:

1. Copy the committed `docker/db/` build context to Tower (or use a checkout
   of the merged repo), then build on Tower's daemon:

   ```sh
   docker build -t commander-lab-db:pg17-vector0.8.2-age1.6.0 docker/db
   ```

2. On Tower, prepare a **new empty** direct-cache directory and root-only
   password file; do not attach another service's or old PG major's data:

   ```sh
   install -d -m 0700 -o 999 -g 999 /mnt/config/appdata/commander-lab/pgdata
   install -d -m 0700 /mnt/config/appdata/commander-lab/db-secrets
   # Only for first provisioning; never overwrite an existing password.
   (umask 077; set -C; openssl rand -hex 32 > /mnt/config/appdata/commander-lab/db-secrets/postgres_password)
   ```

   `POSTGRES_PASSWORD_FILE` reads this file; the password is neither in
   Compose nor Portainer environment values. The official root entrypoint
   reads it before dropping privileges. Do not share the whole secrets
   directory with unrelated services.

3. Deploy the merged stack through its existing Portainer GitOps flow;
   do **not** run a competing Compose project with production container
   names. Data uses `/mnt/config/appdata/commander-lab/pgdata`, intentionally
   not the architecture plan's `/mnt/user/...` FUSE path. Both dev and prod
   have 256 MB shared memory, graceful shutdown, SQL-aware TCP health,
   and Watchtower disabled. No Caddy route / host port is needed for a DB.
4. Verify with the smoke command above (production compose filename where
   needed), inspect logs/health/mounts, then register `commander-lab-db` in
   `/var/lib/docker/unraid-autostart` without removing any existing entries.

Major version upgrades require dump/restore or a planned `pg_upgrade`,
never just swapping an image tag against existing PGDATA. Rebuild the local
image deliberately for security/extension updates; update pins and rerun
verification before deploying. Back up canonical data with `pg_dump` to
storage outside the repository; graph/vector indexes remain rebuildable.

## Repeatable isolated acceptance check

Run on a Docker host (Tower can build without Framework's sudo/FIDO prompt).
This starts **db only**, not the app/Redis, and uses a separate named volume:

```sh
export COMMANDER_DB_PASSWORD="$(openssl rand -hex 32)"
# Choose an unused project/container; the overlay has a fixed test-only name.
docker compose -p cl-db-verify -f docker-compose.yml -f docker/db/compose.verify.yml build db
sh docker/db/verify.sh -p cl-db-verify -f docker-compose.yml -f docker/db/compose.verify.yml
# Only this disposable project's resources; NEVER use -v on production.
docker compose -p cl-db-verify -f docker-compose.yml -f docker/db/compose.verify.yml down -v
unset COMMANDER_DB_PASSWORD
```

`verify.sh` checks boot/health, extension versions, vector distance + HNSW,
Cypher vertex/edge creation + traversal, reapplying init safely, and committed
data surviving restart. The SQL smoke check rolls back all its artifacts.
The restart probe is test-only: do not run this lifecycle script on production.
A failed probe may leave a test table; discard only the disposable test volume
and retry. The helper intentionally does not delete data automatically.

For a pre-existing database on this image, explicitly install extensions
without erasing data (backup first):

```sh
docker compose exec -T db sh -c 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/00-extensions.sql'
```

Reconnect after setting the database search path. The health check requires
TCP password auth **and both extensions**, so a missing init on existing data
is unhealthy rather than a misleading successful `pg_isready` result.
