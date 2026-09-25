-- 001_initial_schema.sql
-- Production PostgreSQL 16 + TimescaleDB Migration
-- Optimised for Oracle Cloud Always Free ARM (Ampere A1)

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Securities Master
CREATE TABLE IF NOT EXISTS security (
    security_id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    country VARCHAR(2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    name TEXT NOT NULL,
    sector TEXT,
    industry TEXT,
    cik_padded VARCHAR(10),
    sedar_issuer_id TEXT,
    paired_security_id BIGINT REFERENCES security(security_id),
    calendar_id VARCHAR(10) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(symbol, exchange)
);

CREATE INDEX IF NOT EXISTS idx_security_lookup ON security(symbol, exchange);
CREATE INDEX IF NOT EXISTS idx_security_cik ON security(cik_padded);

-- 2. Daily Bars (TimescaleDB Hypertable)
CREATE TABLE IF NOT EXISTS bar_1d (
    security_id BIGINT NOT NULL REFERENCES security(security_id),
    trading_date DATE NOT NULL,
    knowledge_at TIMESTAMPTZ NOT NULL,
    open NUMERIC(12,4) NOT NULL,
    high NUMERIC(12,4) NOT NULL,
    low NUMERIC(12,4) NOT NULL,
    close NUMERIC(12,4) NOT NULL,
    volume NUMERIC(16,2) NOT NULL,
    adjusted_close NUMERIC(12,4) NOT NULL,
    vwap NUMERIC(12,4),
    rvol NUMERIC(8,2),
    source TEXT NOT NULL,
    confidence VARCHAR(10) NOT NULL DEFAULT 'HIGH',
    PRIMARY KEY (security_id, trading_date),
    CHECK (low <= high),
    CHECK (open >= low AND open <= high),
    CHECK (close >= low AND close <= high)
);

-- Convert to Hypertable partitioned by trading_date
SELECT create_hypertable('bar_1d', 'trading_date', if_not_exists => TRUE);

-- 3. Macro Observations (TimescaleDB Hypertable)
CREATE TABLE IF NOT EXISTS macro_observation (
    series_id VARCHAR(32) NOT NULL,
    observation_date DATE NOT NULL,
    knowledge_at TIMESTAMPTZ NOT NULL,
    value NUMERIC(14,6) NOT NULL,
    source VARCHAR(32) NOT NULL,
    provenance_metadata JSONB,
    PRIMARY KEY (series_id, observation_date)
);

SELECT create_hypertable('macro_observation', 'observation_date', if_not_exists => TRUE);

-- 4. Fundamental Facts (Point-In-Time)
CREATE TABLE IF NOT EXISTS fundamental_fact (
    fact_id BIGSERIAL PRIMARY KEY,
    security_id BIGINT NOT NULL REFERENCES security(security_id),
    concept TEXT NOT NULL,
    period_end DATE NOT NULL,
    filing_date DATE NOT NULL,
    knowledge_at TIMESTAMPTZ NOT NULL,
    fiscal_year INT NOT NULL,
    fiscal_period VARCHAR(4) NOT NULL,
    form VARCHAR(10) NOT NULL,
    value NUMERIC(18,2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'SEC_EDGAR_XBRL',
    confidence VARCHAR(10) NOT NULL DEFAULT 'HIGH',
    UNIQUE (security_id, concept, period_end, filing_date, form)
);

CREATE INDEX IF NOT EXISTS idx_fundamental_lookup ON fundamental_fact(security_id, concept, filing_date);

-- 5. Opportunity Scores & Attribution
CREATE TABLE IF NOT EXISTS score (
    security_id BIGINT NOT NULL REFERENCES security(security_id),
    as_of_date DATE NOT NULL,
    knowledge_at TIMESTAMPTZ NOT NULL,
    technical_score SMALLINT NOT NULL,
    fundamental_score SMALLINT,
    regime_state VARCHAR(32) NOT NULL,
    regime_multiplier NUMERIC(4,2) NOT NULL,
    news_contribution NUMERIC(5,2) NOT NULL DEFAULT 0.0,
    sector_contribution NUMERIC(5,2) NOT NULL DEFAULT 0.0,
    risk_penalty NUMERIC(5,2) NOT NULL DEFAULT 0.0,
    composite_score SMALLINT NOT NULL,
    confidence_tier CHAR(1) NOT NULL,
    data_quality_floor VARCHAR(10) NOT NULL,
    factor_attribution_json JSONB NOT NULL,
    model_version TEXT NOT NULL,
    PRIMARY KEY (security_id, as_of_date)
);

-- 6. Immutable Signals & Outcomes
CREATE TABLE IF NOT EXISTS signal (
    signal_id BIGSERIAL PRIMARY KEY,
    security_id BIGINT NOT NULL REFERENCES security(security_id),
    strategy_id VARCHAR(64) NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    direction VARCHAR(8) NOT NULL,
    preferred_entry NUMERIC(12,4),
    alt_entry NUMERIC(12,4),
    entry_zone_low NUMERIC(12,4),
    entry_zone_high NUMERIC(12,4),
    stop_loss NUMERIC(12,4),
    target_1 NUMERIC(12,4),
    target_2 NUMERIC(12,4),
    target_3 NUMERIC(12,4),
    risk_reward_ratio NUMERIC(6,2),
    confidence_tier CHAR(1) NOT NULL,
    invalidation_rules JSONB NOT NULL,
    risk_notes JSONB,
    disclaimer_version VARCHAR(16) NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_outcome (
    signal_id BIGINT PRIMARY KEY REFERENCES signal(signal_id),
    resolved_at TIMESTAMPTZ,
    outcome VARCHAR(16),
    max_favorable_excursion NUMERIC(12,4),
    max_adverse_excursion NUMERIC(12,4),
    realized_r NUMERIC(8,2),
    days_held INT,
    notes TEXT
);
