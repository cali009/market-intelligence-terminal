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

-- 12. User Watchlists & Items (Phase 1 MVP)
CREATE TABLE IF NOT EXISTS watchlist (
    watchlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default_user',
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watchlist_item (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id INTEGER NOT NULL REFERENCES watchlist(watchlist_id) ON DELETE CASCADE,
    security_id INTEGER REFERENCES security(security_id),
    symbol TEXT NOT NULL,
    notes TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(watchlist_id, symbol)
);

-- 13. Position & Research Journal (Phase 1 MVP)
CREATE TABLE IF NOT EXISTS journal_position (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default_user',
    symbol TEXT NOT NULL,
    security_id INTEGER REFERENCES security(security_id),
    direction TEXT NOT NULL DEFAULT 'LONG',  -- LONG | SHORT
    shares REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_date TEXT NOT NULL,
    stop_loss REAL,
    profit_target REAL,
    exit_price REAL,
    exit_date TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',     -- OPEN | CLOSED | WATCHLIST
    currency TEXT NOT NULL DEFAULT 'USD',    -- USD | CAD
    conviction INTEGER NOT NULL DEFAULT 3,   -- 1 to 5
    thesis_notes TEXT,
    strategy_tag TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 14. Quantitative Scanner Definitions & Outcome Tracking (Phase 2)
CREATE TABLE IF NOT EXISTS scanner_definition (
    scanner_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,                  -- MOMENTUM | MEAN_REVERSION | BREAKOUT | VOLUME | VOLATILITY
    description TEXT NOT NULL,
    rule_summary TEXT NOT NULL,
    regime_compatibility TEXT NOT NULL,      -- Comma-separated compatible regimes
    historical_win_rate_pct REAL NOT NULL,
    forward_5d_return_pct REAL NOT NULL,
    expectancy_r REAL NOT NULL,
    sample_size INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scanner_run (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_date TEXT NOT NULL,
    executed_at TEXT NOT NULL DEFAULT (datetime('now')),
    total_universe_scanned INTEGER NOT NULL,
    total_matches_found INTEGER NOT NULL,
    regime_state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scanner_result (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES scanner_run(run_id),
    scanner_id TEXT NOT NULL REFERENCES scanner_definition(scanner_id),
    security_id INTEGER REFERENCES security(security_id),
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    price REAL NOT NULL,
    why_matched TEXT NOT NULL,
    key_metrics_json TEXT NOT NULL,
    regime_gated INTEGER NOT NULL DEFAULT 0,
    outcome_1d_pct REAL,
    outcome_5d_pct REAL,
    outcome_20d_pct REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(scanner_id, symbol, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_scanner_result_lookup ON scanner_result(scanner_id, as_of_date);
CREATE INDEX IF NOT EXISTS idx_scanner_result_symbol ON scanner_result(symbol);

-- 15. Technical Feature Store & Multi-Timeframe Confluence (Phase 4)
CREATE TABLE IF NOT EXISTS feature_store (
    feature_id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id INTEGER NOT NULL REFERENCES security(security_id),
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    timeframe TEXT NOT NULL,                 -- 1h | 1d | 1w | 1m
    trend_state TEXT NOT NULL,               -- BULLISH | NEUTRAL | BEARISH
    ema_8 REAL,
    ema_21 REAL,
    sma_50 REAL,
    sma_200 REAL,
    rsi_14 REAL,
    adx_14 REAL,
    atr_14 REAL,
    support_1 REAL,
    support_2 REAL,
    resistance_1 REAL,
    resistance_2 REAL,
    poc_price REAL,
    vah_price REAL,
    val_price REAL,
    value_area_state TEXT,                   -- ABOVE_VALUE | INSIDE_VALUE | BELOW_VALUE
    mtf_confluence_score INTEGER NOT NULL,   -- 0 - 100
    features_json TEXT NOT NULL,
    knowledge_at TEXT NOT NULL DEFAULT (datetime('now')),
    feature_version TEXT NOT NULL DEFAULT 'v4.0',
    UNIQUE(security_id, as_of_date, timeframe, feature_version)
);

CREATE INDEX IF NOT EXISTS idx_feature_store_lookup ON feature_store(symbol, as_of_date, timeframe);

-- 21. Canonical Fundamental Ratios & Valuation Metrics (Point-In-Time)
CREATE TABLE IF NOT EXISTS fundamental_metric (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id INTEGER NOT NULL REFERENCES security(security_id),
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    fiscal_period TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    currency TEXT NOT NULL,
    roic REAL,                              -- Return on Invested Capital (%)
    roe REAL,                               -- Return on Equity (%)
    accruals_ratio REAL,                    -- Sloan Accruals Ratio
    gross_margin_pct REAL,                  -- Gross Margin (%)
    gross_margin_trend_bps REAL,            -- Gross Margin YoY Trend in Basis Points
    operating_margin_pct REAL,              -- Operating Margin (%)
    operating_margin_trend_bps REAL,        -- Operating Margin YoY Trend in Basis Points
    debt_to_equity REAL,                    -- Total Debt / Equity
    pe_ratio REAL,                          -- Price / Earnings TTM
    ps_ratio REAL,                          -- Price / Sales TTM
    pb_ratio REAL,                          -- Price / Book Value
    fcf_yield_pct REAL,                     -- FCF Yield (%)
    dividend_yield_pct REAL,                -- Annual Dividend Yield (%)
    payout_ratio_pct REAL,                  -- Dividend Payout Ratio (%)
    dividend_safety_score INTEGER,          -- 0 - 100 Dividend Safety Score
    quality_score INTEGER NOT NULL,         -- 0 - 100 Fundamental Quality Score
    valuation_score INTEGER NOT NULL,       -- 0 - 100 Valuation Score
    composite_fundamental_score INTEGER NOT NULL, -- 0 - 100 Blended Score
    coverage_status TEXT NOT NULL,          -- FULL | PARTIAL | INDEX_ETF_BYPASS
    missing_fields_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (security_id, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_fundamental_metric_lookup ON fundamental_metric(symbol, as_of_date);

