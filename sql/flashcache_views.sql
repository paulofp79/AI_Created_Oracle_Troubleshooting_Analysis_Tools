--------------------------------------------------------------------------------
-- flashcache_views.sql
--
-- Builds on the external-table approach from
--   https://blogs.oracle.com/exadata/viewing-flash-cache-contents
-- but adds the views a DBA actually wants once the raw data is loaded:
--   1. debug$fc_raw            - external table over extract_flashcache.sh output
--   2. fc_by_object            - per-object rollup (same idea as the blog, plus
--                                object_id fallback and columnar figures)
--   3. fc_by_database          - per-PDB/CDB rollup
--   4. fc_by_object_type       - which object TYPES (indexes vs tables vs LOBs)
--                                are eating the cache - not in the original post
--   5. fc_cell_skew            - flags storage cells that are caching
--                                disproportionately more/less than the RAC
--                                average, which the blog's single-cell view
--                                can't show at all
--   6. fc_cold_cache_candidates- objects consuming >N MB with a low hit ratio,
--                                i.e. good candidates for NOCACHE / CELL_FLASH_CACHE
--                                tuning - the blog stops at "here's what's cached"
--                                and never gets to "here's what to do about it"
--------------------------------------------------------------------------------

-- Adjust directory/file names to match extract_flashcache.sh's -o output_dir
-- (script default is /tmp/fc_data; change here if you pass a different -o)
CREATE OR REPLACE DIRECTORY fc_data AS '/tmp/fc_data';

CREATE TABLE debug$fc_raw (
  run_ts               VARCHAR2(19),
  cell_name            VARCHAR2(50),
  db_id                NUMBER,
  db_name              VARCHAR2(30),
  object_id            NUMBER,
  tablespace_id        NUMBER,
  cached_bytes         NUMBER,
  cached_keep_bytes    NUMBER,
  cached_write_bytes   NUMBER,
  columnar_cache_bytes NUMBER,
  columnar_keep_bytes  NUMBER,
  hit_count            NUMBER,
  miss_count           NUMBER
)
ORGANIZATION EXTERNAL (
  TYPE ORACLE_LOADER
  DEFAULT DIRECTORY fc_data
  ACCESS PARAMETERS (
    RECORDS DELIMITED BY newline
    SKIP 1
    BADFILE  fc_data:'fc_raw.bad'
    LOGFILE  fc_data:'fc_raw.log'
    FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
    MISSING FIELD VALUES ARE NULL
  )
  LOCATION (fc_data:'flashcache_latest.csv')
)
REJECT LIMIT UNLIMITED;

--------------------------------------------------------------------------------
-- 2. Per-object rollup - equivalent to the blog's debug$fc_et_ob_vw, extended
--    with columnar cache size and a NULL-safe object name fallback.
--------------------------------------------------------------------------------
CREATE OR REPLACE VIEW fc_by_object AS
SELECT
  d.name                                        AS database_name,
  t.name                                        AS tablespace_name,
  o.owner,
  NVL(o.object_name, '(obj#'||r.object_id||')') AS object_name,
  o.object_type,
  ROUND(SUM(r.cached_bytes)/1048576, 2)         AS cached_mb,
  ROUND(SUM(r.cached_keep_bytes)/1048576, 2)    AS keep_mb,
  ROUND(SUM(r.columnar_cache_bytes)/1048576, 2) AS columnar_mb,
  COUNT(DISTINCT r.cell_name)                   AS cells_present_on,
  SUM(r.hit_count)                              AS hits,
  SUM(r.miss_count)                             AS misses,
  ROUND(100 * SUM(r.hit_count) /
        NULLIF(SUM(r.hit_count) + SUM(r.miss_count), 0), 1) AS hit_ratio_pct
FROM debug$fc_raw r
LEFT JOIN v$database d      ON r.db_id = d.dbid
LEFT JOIN v$tablespace t    ON r.tablespace_id = t.ts#
LEFT JOIN dba_objects o     ON r.object_id = o.data_object_id
GROUP BY d.name, t.name, o.owner, o.object_name, o.object_type, r.object_id;

--------------------------------------------------------------------------------
-- 3. Per-database rollup - equivalent to the blog's debug$fc_et_db_vw
--------------------------------------------------------------------------------
CREATE OR REPLACE VIEW fc_by_database AS
SELECT
  db_name                                        AS database_name,
  ROUND(SUM(cached_bytes)/1048576, 2)            AS cached_mb,
  ROUND(SUM(cached_keep_bytes)/1048576, 2)       AS keep_mb,
  ROUND(SUM(cached_write_bytes)/1048576, 2)      AS write_mb,
  ROUND(SUM(columnar_cache_bytes)/1048576, 2)    AS columnar_mb,
  SUM(hit_count)                                 AS hits,
  SUM(miss_count)                                AS misses,
  ROUND(100 * SUM(hit_count) /
        NULLIF(SUM(hit_count) + SUM(miss_count), 0), 1) AS hit_ratio_pct
FROM debug$fc_raw
GROUP BY db_name
ORDER BY cached_mb DESC;

--------------------------------------------------------------------------------
-- 4. Object-type rollup - not present in the blog at all. Answers "is my
--    flash cache mostly indexes, tables, LOBs, or partitions?"
--------------------------------------------------------------------------------
CREATE OR REPLACE VIEW fc_by_object_type AS
SELECT
  NVL(o.object_type, 'UNKNOWN')                 AS object_type,
  COUNT(*)                                       AS object_count,
  ROUND(SUM(r.cached_bytes)/1048576, 2)          AS cached_mb,
  ROUND(100 * RATIO_TO_REPORT(SUM(r.cached_bytes)) OVER (), 1) AS pct_of_cache,
  ROUND(100 * SUM(r.hit_count) /
        NULLIF(SUM(r.hit_count) + SUM(r.miss_count), 0), 1) AS hit_ratio_pct
FROM debug$fc_raw r
LEFT JOIN dba_objects o ON r.object_id = o.data_object_id
GROUP BY o.object_type
ORDER BY cached_mb DESC;

--------------------------------------------------------------------------------
-- 5. Cell skew - flags cells whose cached MB deviates from the cluster
--    average by more than 25%. On a healthy RAC/Exadata cluster, flash
--    cache usage should be roughly even across cells; a skewed cell can
--    indicate an ASM rebalance in progress, a failed/offline grid disk,
--    or an application routing bias. The original post never aggregates
--    across cells this way.
--------------------------------------------------------------------------------
CREATE OR REPLACE VIEW fc_cell_skew AS
WITH per_cell AS (
  SELECT cell_name, ROUND(SUM(cached_bytes)/1048576, 2) AS cached_mb
  FROM debug$fc_raw
  GROUP BY cell_name
),
cluster_avg AS (
  SELECT AVG(cached_mb) AS avg_mb FROM per_cell
)
SELECT
  p.cell_name,
  p.cached_mb,
  c.avg_mb                                            AS cluster_avg_mb,
  ROUND(100 * (p.cached_mb - c.avg_mb) / NULLIF(c.avg_mb, 0), 1) AS pct_deviation,
  CASE WHEN ABS(p.cached_mb - c.avg_mb) > 0.25 * c.avg_mb
       THEN 'SKEWED' ELSE 'OK' END                    AS status
FROM per_cell p, cluster_avg c
ORDER BY pct_deviation DESC;

--------------------------------------------------------------------------------
-- 6. Cold-cache candidates - objects taking meaningful space with a poor
--    hit ratio. These are candidates for CELL_FLASH_CACHE=NONE/KEEP tuning,
--    which is the actionable question the blog's "here's what's cached"
--    view never answers.
--------------------------------------------------------------------------------
CREATE OR REPLACE VIEW fc_cold_cache_candidates AS
SELECT *
FROM fc_by_object
WHERE cached_mb > 50                 -- adjust threshold to your flash cache size
  AND hit_ratio_pct IS NOT NULL
  AND hit_ratio_pct < 50
ORDER BY cached_mb DESC;

-- Convenience: export everything the dashboard needs in one shot
-- SELECT * FROM fc_by_object ORDER BY cached_mb DESC;
--   -> spool to CSV and load into flashcache_dashboard.html
