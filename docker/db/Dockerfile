# syntax=docker/dockerfile:1
# PostgreSQL 17 (Debian bookworm), pinned for identical build/runtime ABI.
FROM postgres:17-bookworm@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27 AS extensions

# Upstream release commits: pgvector v0.8.2; Apache AGE PG17/v1.6.0-rc0
# (the PG17 1.6.0 release tag; age.control reports version 1.6.0).
ARG PGVECTOR_COMMIT=cab9da72c04353f143bb06b42ab70a403daac64a
ARG AGE_COMMIT=54905a09bf8462f22a87c3adfd2ab5752e5c1e71
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bison build-essential ca-certificates flex git postgresql-server-dev-17 \
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
RUN git init age \
    && cd age \
    && git remote add origin https://github.com/apache/age.git \
    && git fetch --depth 1 origin "${AGE_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${AGE_COMMIT}" \
    && make -j2 \
    && make install DESTDIR=/extensions

FROM postgres:17-bookworm@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27
COPY --from=extensions /extensions/usr/ /usr/
COPY --from=extensions /usr/src/age/LICENSE /usr/share/licenses/age/LICENSE
COPY --from=extensions /usr/src/age/NOTICE /usr/share/licenses/age/NOTICE
COPY --from=extensions /usr/src/pgvector/LICENSE /usr/share/licenses/pgvector/LICENSE
COPY init/ /docker-entrypoint-initdb.d/
COPY healthcheck.sh /usr/local/bin/commander-db-healthcheck
COPY smoke.sql /usr/local/share/commander-db/smoke.sql
RUN chmod 0755 /usr/local/bin/commander-db-healthcheck
# AGE must load before queries in every backend, including pooled connections.
CMD ["postgres", "-c", "shared_preload_libraries=age"]
