#!/bin/sh
# Run against a DISPOSABLE Compose project: no application containers are started.
# Compose overrides must assign a unique db container_name and fresh data mount.
# Usage: docker/db/verify.sh -p cl-db-test -f docker-compose.yml -f override.yml
set -eu

docker compose "$@" config --quiet
docker compose "$@" up -d --no-deps --wait --wait-timeout 120 db
docker compose "$@" exec -T db commander-db-healthcheck
docker compose "$@" exec -T db sh -c \
    'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /usr/local/share/commander-db/smoke.sql'
# Explicit reapply is safe; Docker only runs init scripts automatically on empty PGDATA.
docker compose "$@" exec -T db sh -c \
    'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/00-extensions.sql'
# Prove committed data survives restart, not just extension catalogs.
docker compose "$@" exec -T db sh -c \
    'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE TABLE public.infra_restart_probe (value integer); INSERT INTO public.infra_restart_probe VALUES (42);"'
docker compose "$@" restart db
docker compose "$@" up -d --no-deps --wait --wait-timeout 120 db
docker compose "$@" exec -T db commander-db-healthcheck
result=$(docker compose "$@" exec -T db sh -c \
    'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT value FROM public.infra_restart_probe"')
[ "$result" = 42 ]
docker compose "$@" exec -T db sh -c \
    'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP TABLE public.infra_restart_probe"'
docker compose "$@" exec -T db sh -c \
    'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /usr/local/share/commander-db/smoke.sql'
printf '%s\n' 'PASS: boot, health, extensions, vector/HNSW, Cypher, idempotent init, restart/persistence'
