# Compute Portainer Git stack recovery

`docker-compose.compute.yml` is a separate recovery definition for the compute
Docker endpoint, not the older Tower stack in `docs/GITOPS.md`. The original
compute stack 141 was lost. This file alone does not deploy or restore it.

## Safety before deployment

- Confirm the selected Portainer **compute** environment is the Docker host
  with the production data. Back up PostgreSQL and Redis first, and verify the
  backups. Do not start a second PostgreSQL or Redis against the same data, or
  leave conflicting containers/ports in use. Plan the stop/replacement with
  the operator; do not remove volumes or run destructive initialization.
- Inspect the existing Docker volume `commander-lab_pgdata` on compute and
  verify it is the intended production PostgreSQL 17 cluster. The volume is
  external, so Compose will not create it. The DB entrypoint refuses an empty
  or non-17 PGDATA rather than initializing it; this guard cannot prove that a
  different populated cluster is the correct one.
- Verify `/mnt/tower/appdata-compute/commander-lab/redis` is the original Redis
  data directory, and verify the PostgreSQL password file and `data`, `secrets`,
  and `auth` directories under `/mnt/tower/appdata-compute/commander-lab` on
  **compute**. The original stack snapshot confirms these mount paths. Bind
  mounts do not create missing host paths. The DB
  password file, app secrets and auth mounts are read-only.
- Optional Portainer stack env var `COMMANDER_LAB_DATA_ROOT` overrides the host
  data root (default `/mnt/tower/appdata-compute/commander-lab`), e.g.
  `/mnt/config/appdata/commander-lab` on tower. Leave unset on compute.
- Redis uses the image's default RDB configuration and refuses to start without
  a nonempty `/data/dump.rdb`. Confirm this snapshot is current and contains
  the intended production data; this guard cannot validate its contents. If
  production instead relies on AOF or custom Redis settings, stop and recover
  the original configuration rather than starting this default Redis service.
- The local images `commander-lab-db:pg17-vector0.8.2` and
  `commander-lab-web:e8e69cd` must already exist on compute. They are not pulled
  or built. App and worker use the public prebuilt
  `ghcr.io/andrewgari/commander-lab:latest` with `pull_policy: always`, so a
  stack redeploy picks up the newest `main` build (CI publishes `latest` and
  `sha-<short>` on every push to `main`). Review migrations and compatibility
  with the local web image before redeploying.
  No service has a build definition; the Portainer Agent build path is broken.

## Portainer settings and secrets

Use a Git repository stack pointing directly at
`https://github.com/andrewgari/commander-lab`, the desired ref (normally
`refs/heads/main` after review/merge), and compose path
`docker-compose.compute.yml`, on the compute environment. Configure the stack
environment in Portainer, not in Git. `DATABASE_URL` is required and must point
to `db:5432` with the existing database/user and the password matching the
compute password file. URL-encode special characters in the password. Missing
or empty `DATABASE_URL` fails Compose interpolation, before startup. The db
password file does not change the password of an existing PostgreSQL cluster.

Portainer stack environment values and `docker compose config` output may
expose secrets to administrators and in logs. Restrict Portainer and Docker
access; never commit credentials, paste rendered config into tickets, or log
the URL. Existing API keys can instead remain in the read-only app secrets
directory as described in `docs/GITOPS.md`. Optional production environment
variables are forwarded in the Compose file.

`ORACLE_URL` is deliberately empty unless separately configured: the old
Oracle address is down. Configure `ORACLE_KEY` securely if using Oracle.
`FORGE_SIM_URL` also has no Compose default: set it to the verified Forge
endpoint reachable **from the compute app container**. Compose service DNS
names only resolve on a shared Docker network; a service name from a different
stack/host is not automatically reachable. When empty, the app's current Forge
client does not select its internal default but may report an invalid-URL error
when simulation is used; configure the endpoint to use that feature.

The recovered database snapshot was at Alembic revision
`a012_app_tables_to_public`, while the tested app requires
`a013_deck_intents`. On 2026-11-21 UTC (November 20 local), the upgrade was rehearsed on an isolated
copy, then approved, applied and verified on the original compute database:
the revision and table were present, with 58 decks and 5,608 card instances.
Do not repeat a migration merely because of this note; check the target database's
actual revision. For any future schema change, back up and verify the database,
obtain approval, and run the reviewed migration against the intended target.
The application image does not include Alembic and does not automatically run
migrations. Never stamp the revision without applying the migration.

The app defaults to `DATA_ACCESS_MODE=readonly`, which guards its supported
write paths but is not a storage-level read-only guarantee. A startup gate uses
the app's own database URL to require the expected database, revision and table;
it does not prove the cluster's identity or full integrity. Do not start the app
until the production data and migration are separately verified.

The interactions worker is disabled by default (`INTERACTIONS_DISABLED=1`),
and the bracket worker is behind the `workers` Compose profile and refuses to
start unless `DATA_ACCESS_MODE=readwrite`. Enable writes and workers only after
production data and the deployment have been verified. The older Svelte web
image is also behind the `web` profile until its write flows are checked against
the tested app; this does not affect the classic site at port 8020.

Alembic's environment executes and commits an object-relocation step before
the migration transaction. Review that behavior and preserve a verified backup.
Its config parser also interprets `%` in database URLs; rehearse the actual
credential path without exposing it before using URL-encoded passwords.

The app publishes compute port 8020; the optional `web` profile publishes 8011.
Before enabling `web`, verify its write flows and ensure a trusted proxy controls
its forwarded headers and direct access cannot spoof them. PostgreSQL and Redis
publish no host ports. Existing Portainer Caddy origin
`commander.lab` targets compute port 8020, not the web container. Do not change
Caddy based on this file. Health checks gate initial app startup on Redis and
Postgres, and web startup on the app; they are not a data-integrity check.
After an authorized deployment, verify the actual volume/binds, container image
versions, service health, database contents, existing site and web separately.
Compose `config` only validates configuration; it does not verify compute host
paths, images, volume contents, DNS, credentials, or connectivity.
