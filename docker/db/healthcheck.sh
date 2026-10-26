#!/bin/sh
set -eu
# TCP deliberately excludes the temporary socket-only initdb server.
if [ -n "${POSTGRES_PASSWORD_FILE:-}" ]; then
    PGPASSWORD=$(cat "$POSTGRES_PASSWORD_FILE")
else
    PGPASSWORD=${POSTGRES_PASSWORD:?POSTGRES_PASSWORD or POSTGRES_PASSWORD_FILE is required}
fi
export PGPASSWORD
count=$(psql -X -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v ON_ERROR_STOP=1 -Atc \
    "SELECT count(*) FROM pg_extension WHERE extname IN ('vector', 'age')")
[ "$count" = 2 ]
