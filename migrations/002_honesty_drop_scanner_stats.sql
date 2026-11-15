-- ============================================================================
-- HONESTY OVERHAUL — Phase 1
--
-- Drops the four per-scanner performance columns from `scanner_definition`.
--
-- These columns were populated from typed-in constants in
-- src/engine/scanners.py (59.2 / 1.85 / 0.52 / 142 for MOMENTUM_LEADERS, and
-- equivalent literals for the other 14 rules). Nothing in the codebase ever
-- computed them: the scanner rules have no evaluable predicate in the
-- walk-forward backtest engine, so there was never a sample behind any of those
-- figures. They also fed a 30-point "Empirical Expectancy" component of the
-- conviction score, which means an invented number was influencing ranking.
--
-- The columns are dropped rather than nulled so the schema cannot silently
-- re-acquire a NOT NULL constraint that tempts a future caller to invent a
-- value again. See SCANNER_STATS_STATUS in src/engine/scanners.py for the text
-- the UI now shows in place of these statistics.
--
-- Idempotent: safe to re-run. Requires SQLite >= 3.35 for DROP COLUMN.
-- ============================================================================

-- SQLite has no DROP COLUMN IF EXISTS, so guard each drop on the column's
-- presence in pragma_table_info.
DROP TABLE IF EXISTS _mig002_guard;
CREATE TEMP TABLE _mig002_guard AS
    SELECT name FROM pragma_table_info('scanner_definition');

-- Historical note: the four columns are removed here. Because DROP COLUMN cannot
-- be made conditional in plain SQL, the application performs the guarded drop
-- (see MarketScannerEngine.init_scanner_definitions_in_db) and this file
-- documents the intended end state for a fresh database.

-- End state for a fresh install is defined in migrations/001_initial_sqlite.sql,
-- which no longer declares these columns.
