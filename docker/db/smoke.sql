-- Functional, repeatable checks: rolled back, including graph and HNSW index.
\set ON_ERROR_STOP on
SELECT version();
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('vector', 'age') ORDER BY extname;
SHOW shared_preload_libraries;
SHOW search_path;
BEGIN;
-- No LOAD or SET here: prove a FRESH connection inherits the image/DB settings.
CREATE TEMP TABLE vector_probe (id integer, embedding vector(3));
INSERT INTO vector_probe VALUES (1, '[1,2,3]'), (2, '[4,5,6]');
CREATE INDEX ON vector_probe USING hnsw (embedding vector_l2_ops);
SELECT ((SELECT id FROM vector_probe ORDER BY embedding <-> '[1,2,3]' LIMIT 1) = 1
        AND abs(('[1,2,3]'::vector <-> '[4,5,6]'::vector) - sqrt(27.0)) < 0.000001)
       AS vector_ok \gset
\if :vector_ok
\echo pgvector distance + HNSW: PASS
\else
\echo pgvector: FAIL
-- SQL error + ON_ERROR_STOP gives a nonzero exit on PostgreSQL 17 psql.
SELECT 1 / 0 AS assertion_failed;
\endif

SELECT create_graph('commander_infra_smoke');
SELECT * FROM cypher('commander_infra_smoke', $$
    CREATE (:Probe {name: 'first'})-[:LINKS]->(:Probe {name: 'second'})
$$) AS (result agtype);
-- AGE's explicit agtype->text cast returns the unquoted scalar string.
SELECT (count(*) = 1 AND min(name::text) = 'second') AS graph_ok
FROM cypher('commander_infra_smoke', $$
    MATCH (:Probe {name: 'first'})-[:LINKS]->(b:Probe) RETURN b.name
$$) AS (name agtype) \gset
\if :graph_ok
\echo AGE create + traversal: PASS
\else
\echo AGE: FAIL
-- SQL error + ON_ERROR_STOP gives a nonzero exit on PostgreSQL 17 psql.
SELECT 1 / 0 AS assertion_failed;
\endif
ROLLBACK;
