-- 001_initial_sqlite.sql
-- Canonical schema for SQLite & Cloudflare D1
-- Supports zero-cost local development and edge deployment

PRAGMA foreign_keys = ON;

-- 1. Securities Master
CREATE TABLE IF NOT EXISTS security (
    security_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,                  -- NYSE | NASDAQ | AMEX | TSX | TSXV
    country TEXT NOT NULL,                   -- US | CA
    currency TEXT NOT NULL,                  -- USD | CAD
    name TEXT NOT NULL,
    sector TEXT,                             -- GICS Sector
    industry TEXT,                           -- GICS Industry
    cik_padded TEXT,                         -- 10-digit zero-padded CIK for US SEC filers
    sedar_issuer_id TEXT,                    -- Canadian SEDAR+ issuer identifier
    paired_security_id INTEGER REFERENCES security(security_id), -- Cross-listed pair (e.g. SHOP <-> SHOP.TO)
    calendar_id TEXT NOT NULL,               -- XNYS | XTSE
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, exchange)
);

CREATE INDEX IF NOT EXISTS idx_security_lookup ON security(symbol, exchange);
CREATE INDEX IF NOT EXISTS idx_security_cik ON security(cik_padded);

-- 2. Ticker History (renames, corporate actions)
CREATE TABLE IF NOT EXISTS ticker_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id INTEGER NOT NULL REFERENCES security(security_id),
    old_symbol TEXT NOT NULL,
    new_symbol TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    notes TEXT
);

-- 3. Daily Bars (OHLCV) with Bitemporal Lineage
CREATE TABLE IF NOT EXISTS bar_1d (
    security_id INTEGER NOT NULL REFERENCES security(security_id),
    trading_date TEXT NOT NULL,             -- YYYY-MM-DD
    knowledge_at TEXT NOT NULL,             -- Bitemporal timestamp when bar was ingested
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    adjusted_close REAL NOT NULL,
    vwap REAL,
    rvol REAL,
    source TEXT NOT NULL,                   -- EDGAR | STOOQ | YFINANCE | SEC_FORM4
    confidence TEXT NOT NULL DEFAULT 'HIGH', -- HIGH | MEDIUM | LOW
    PRIMARY KEY (security_id, trading_date),
    CHECK (low <= high),
    CHECK (open >= low AND open <= high),
    CHECK (close >= low AND close <= high)
);

CREATE INDEX IF NOT EXISTS idx_bar_1d_date ON bar_1d(trading_date);

-- 4. Macroeconomic & Central Bank Observations
CREATE TABLE IF NOT EXISTS macro_observation (
    series_id TEXT NOT NULL,                 -- FXUSDCAD | V39055 | DGS10 | T10Y2Y | FEDFUNDS | VIXCLS
    observation_date TEXT NOT NULL,          -- YYYY-MM-DD
    knowledge_at TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,                   -- BOC_VALET | FRED | STATCAN
    provenance_metadata TEXT,               -- JSON string
    PRIMARY KEY (series_id, observation_date)
);

-- 5. Canonical Fundamental Facts (Bitemporal Point-In-Time)
CREATE TABLE IF NOT EXISTS fundamental_fact (
    fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id INTEGER NOT NULL REFERENCES security(security_id),
    concept TEXT NOT NULL,                  -- Revenues | NetIncomeLoss | OperatingCashFlow | SharesOutstanding | TotalDebt
    period_end TEXT NOT NULL,               -- Fiscal period end date YYYY-MM-DD
    filing_date TEXT NOT NULL,              -- Point-in-time knowledge date YYYY-MM-DD
    knowledge_at TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_period TEXT NOT NULL,            -- Q1 | Q2 | Q3 | FY
    form TEXT NOT NULL,                     -- 10-K | 10-Q | 40-F | 6-K
    value REAL NOT NULL,
    currency TEXT NOT NULL,                 -- USD | CAD
    source TEXT NOT NULL DEFAULT 'SEC_EDGAR_XBRL',
    confidence TEXT NOT NULL DEFAULT 'HIGH',
    UNIQUE (security_id, concept, period_end, filing_date, form)
);

CREATE INDEX IF NOT EXISTS idx_fundamental_lookup ON fundamental_fact(security_id, concept, filing_date);

-- 6. News & Material Events (Deduplicated Clusters)
CREATE TABLE IF NOT EXISTS news_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_hash TEXT UNIQUE NOT NULL,        -- MD5(normalized_headline + date)
    primary_security_id INTEGER REFERENCES security(security_id),
    headline TEXT NOT NULL,
    summary TEXT,
    source TEXT NOT NULL,                   -- NEWSFILE | CNW_CISION | GLOBENEWSWIRE | SEC_8K
    published_at TEXT NOT NULL,
    knowledge_at TEXT NOT NULL,
    is_primary_source INTEGER NOT NULL DEFAULT 1,
    event_category TEXT,                    -- EARNINGS | GUIDANCE | MA | LITIGATION | REGULATORY
    sentiment_score REAL,                   -- -1.0 to +1.0
    materiality_score INTEGER,              -- 1 to 5
    raw_payload TEXT
);

-- 7. Data Quality & Audit Event Log
CREATE TABLE IF NOT EXISTS data_quality_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    security_id INTEGER,
    dimension TEXT NOT NULL,                 -- COMPLETENESS | FRESHNESS | ACCURACY | LINEAGE
    severity TEXT NOT NULL,                  -- INFO | WARNING | CRITICAL
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',     -- OPEN | QUARANTINED | RESOLVED
    resolution_notes TEXT
);

-- 8. Opportunity Scores & Factor Attribution
CREATE TABLE IF NOT EXISTS score (
    security_id INTEGER NOT NULL REFERENCES security(security_id),
    as_of_date TEXT NOT NULL,
    knowledge_at TEXT NOT NULL,
    technical_score INTEGER NOT NULL,       -- 0 - 100
    fundamental_score INTEGER,              -- 0 - 100 (or NULL if Canadian pure-play domestic)
    regime_state TEXT NOT NULL,             -- STRONG_BULL | WEAK_BULL | CONSOLIDATION | HIGH_VOL | BEAR | CRISIS
    regime_multiplier REAL NOT NULL,
    news_contribution REAL NOT NULL DEFAULT 0.0,
    sector_contribution REAL NOT NULL DEFAULT 0.0,
    risk_penalty REAL NOT NULL DEFAULT 0.0,
    composite_score INTEGER NOT NULL,       -- 0 - 100
    confidence_tier TEXT NOT NULL,          -- A | B | C | D
    data_quality_floor TEXT NOT NULL,       -- HIGH | MEDIUM | LOW
    factor_attribution_json TEXT NOT NULL,
    model_version TEXT NOT NULL,
    PRIMARY KEY (security_id, as_of_date)
);

-- 9. Signals & Immutable Outcome Ledger (The Paper Scoreboard)
CREATE TABLE IF NOT EXISTS signal (
    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id INTEGER NOT NULL REFERENCES security(security_id),
    strategy_id TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    direction TEXT NOT NULL,                 -- LONG | EXIT | NONE
    preferred_entry REAL,
    alt_entry REAL,
    entry_zone_low REAL,
    entry_zone_high REAL,
    stop_loss REAL,
    target_1 REAL,
    target_2 REAL,
    target_3 REAL,
    risk_reward_ratio REAL,
    confidence_tier TEXT NOT NULL,
    invalidation_rules TEXT NOT NULL,        -- Machine-checkable JSON predicates
    risk_notes TEXT,
    disclaimer_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_outcome (
    signal_id INTEGER PRIMARY KEY REFERENCES signal(signal_id),
    resolved_at TEXT,
    outcome TEXT,                           -- T1 | T2 | T3 | STOP | EXPIRED | INVALIDATED
    max_favorable_excursion REAL,
    max_adverse_excursion REAL,
    realized_r REAL,
    days_held INTEGER,
    notes TEXT
);

-- 10. Compliance Consent & Entitlements
CREATE TABLE IF NOT EXISTS consent_record (
    consent_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,                  -- EMAIL | IN_APP
    consent_type TEXT NOT NULL,             -- EXPRESS | STATUTORY
    ip_address TEXT,
    granted_at TEXT NOT NULL,
    revoked_at TEXT,
    evidence TEXT
);

CREATE TABLE IF NOT EXISTS data_entitlement (
    entitlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    dataset TEXT NOT NULL,                  -- TSX_EOD | US_EOD | EDGAR_XBRL
    subscriber_class TEXT NOT NULL,          -- NON_PROFESSIONAL | PROFESSIONAL
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    attestation_confirmed INTEGER NOT NULL DEFAULT 1
);

-- 11. Corporate Actions & Event Calendar
CREATE TABLE IF NOT EXISTS event_calendar (
    calendar_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    security_id INTEGER REFERENCES security(security_id),
    event_type TEXT NOT NULL,               -- EARNINGS | DIVIDEND | POLICY_RATE | FILING_DEADLINE
    event_date TEXT NOT NULL,               -- YYYY-MM-DD
    title TEXT NOT NULL,
    details TEXT,
    is_confirmed INTEGER NOT NULL DEFAULT 1,
    proximity_flag TEXT                     -- IMMEDIATE | UPCOMING | PAST
);
