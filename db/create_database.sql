-- create_database.sql — one-time bootstrap: create the tata_steel DATABASE.
-- ============================================================================
-- This is the PRE-STEP that runs BEFORE db/00_schema.sql. The numbered files
-- (00_schema, 01_seed, 02_verify) all run *inside* the tata_steel database and
-- assume it already exists. This file is what creates that database.
--
-- IDEMPOTENT + NEVER DROPS: PostgreSQL has no "CREATE DATABASE IF NOT EXISTS",
-- so we guard it with a pg_database existence check and only create when it is
-- missing. Re-running this does nothing if tata_steel already exists — it never
-- drops, never recreates, never touches an existing database or its data.
--
-- WHY A SEPARATE FILE (not part of 00_schema.sql):
--   1. CREATE DATABASE cannot run inside a transaction block, and it cannot be
--      combined with other DDL in the same connection the way schema/tables are.
--   2. It must run against a DIFFERENT database — the "postgres" maintenance DB —
--      because you cannot create tata_steel while connected to tata_steel.
--
-- HOW TO RUN (local, matches rebuild.sh connection rules — Unix socket, no host,
-- GSSAPI disabled). Connect to the "postgres" maintenance DB, not tata_steel:
--
--     PGGSSENCMODE=disable psql -U postgres -d postgres -f db/create_database.sql
--
-- After this succeeds once, run the normal build (./rebuild.sh), which applies
-- db/00_schema.sql + db/01_seed.sql into the now-existing tata_steel database.
--
-- NOTE: On Neon/Render the database already exists (it is "neondb", created by
-- the provider), so this file is only for a local/self-managed PostgreSQL where
-- you own the server and must create the database yourself.
-- ============================================================================

SELECT 'CREATE DATABASE tata_steel'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'tata_steel'
)\gexec
