# syntax=docker/dockerfile:1
# PostgreSQL 17 (Debian bookworm), pinned for identical build/runtime ABI.
FROM postgres:17-bookworm@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27 AS extensions

# Upstream release commits: pgvector v0.8.2.
ARG PGVECTOR_COMMIT=cab9da72c04353f143bb06b42ab70a403daac64a
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates git postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src
RUN git init pgvector \
    && cd pgvector \
    && git remote add origin https://github.com/pgvector/pgvector.git \
    && git fetch --depth 1 origin "${PGVECTOR_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${PGVECTOR_COMMIT}" \
    && make -j2 OPTFLAGS="" \
    && make install DESTDIR=/extensions

FROM postgres:17-bookworm@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27
COPY --from=extensions /extensions/usr/ /usr/
COPY --from=extensions /usr/src/pgvector/LICENSE /usr/share/licenses/pgvector/LICENSE
COPY init/ /docker-entrypoint-initdb.d/
COPY healthcheck.sh /usr/local/bin/commander-db-healthcheck
COPY smoke.sql /usr/local/share/commander-db/smoke.sql
RUN chmod 0755 /usr/local/bin/commander-db-healthcheck
