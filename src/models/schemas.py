"""
Canonical Pydantic v2 Models & Data Lineage Contracts
US + Canada Market Intelligence Platform
"""

from datetime import date, datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, model_validator


class SecurityMaster(BaseModel):
    symbol: str
    exchange: Literal["NYSE", "NASDAQ", "AMEX", "TSX", "TSXV"]
    country: Literal["US", "CA"]
    currency: Literal["USD", "CAD"]
    name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    cik_padded: Optional[str] = None
    sedar_issuer_id: Optional[str] = None
    paired_security_id: Optional[int] = None
    calendar_id: Literal["XNYS", "XTSE"]
    is_active: bool = True


class DailyBar(BaseModel):
    security_id: int
    trading_date: date
    knowledge_at: datetime
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    volume: float = Field(..., ge=0)
    adjusted_close: float = Field(..., gt=0)
    vwap: Optional[float] = None
    rvol: Optional[float] = None
    source: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "HIGH"

    @model_validator(mode="after")
    def validate_ohlc_consistency(self):
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"Open {self.open} must be between Low {self.low} and High {self.high}")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"Close {self.close} must be between Low {self.low} and High {self.high}")
        if self.low > self.high:
            raise ValueError(f"Low {self.low} cannot exceed High {self.high}")
        return self


class MacroObservation(BaseModel):
    series_id: str
    observation_date: date
    knowledge_at: datetime
    value: float
    source: str
    provenance_metadata: Optional[Dict[str, Any]] = None


class FundamentalFact(BaseModel):
    security_id: int
    concept: str
    period_end: date
    filing_date: date
    knowledge_at: datetime
    fiscal_year: int
    fiscal_period: Literal["Q1", "Q2", "Q3", "FY"]
    form: str
    value: float
    currency: Literal["USD", "CAD"]
    source: str = "SEC_EDGAR_XBRL"
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "HIGH"

    @model_validator(mode="after")
    def validate_filing_date_after_period_end(self):
        # Point-in-time sanity check: filing date must be >= period_end
        if self.filing_date < self.period_end:
            raise ValueError(
                f"Filing date {self.filing_date} cannot be before fiscal period end {self.period_end}"
            )
        return self


class FundamentalQualityMetrics(BaseModel):
    roic: Optional[float] = None
    roe: Optional[float] = None
    accruals_ratio: Optional[float] = None
    operating_margin_pct: Optional[float] = None
    margin_trend_yoy_bps: Optional[float] = None
    debt_to_equity: Optional[float] = None
    quality_score: int = Field(50, ge=0, le=100)


class ValuationMultiples(BaseModel):
    pe_ratio: Optional[float] = None
    ps_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    fcf_yield_pct: Optional[float] = None
    dividend_yield_pct: Optional[float] = None
    valuation_score: int = Field(50, ge=0, le=100)


class DividendSafetyMetrics(BaseModel):
    dividend_yield_pct: Optional[float] = None
    payout_ratio_pct: Optional[float] = None
    fcf_coverage: Optional[float] = None
    safety_score: int = Field(50, ge=0, le=100)
    safety_tier: Literal["HIGH_SAFETY", "MODERATE_SAFETY", "AT_RISK", "N/A_NO_DIVIDEND"] = "MODERATE_SAFETY"


class FundamentalDossier(BaseModel):
    symbol: str
    as_of_date: str
    filing_date: str
    currency: str
    fiscal_period: str
    fiscal_year: int
    quality: FundamentalQualityMetrics
    valuation: ValuationMultiples
    dividend_safety: DividendSafetyMetrics
    composite_fundamental_score: int = Field(50, ge=0, le=100)
    coverage_status: Literal["FULL", "PARTIAL", "INDEX_ETF_BYPASS"]
    missing_fields: List[str] = Field(default_factory=list)
    raw_concepts: Dict[str, float] = Field(default_factory=dict)
    provenance_source: str = "SEC_EDGAR_XBRL"


class NewsCluster(BaseModel):
    dedup_hash: str
    primary_security_id: Optional[int] = None
    headline: str
    summary: Optional[str] = None
    source: str
    published_at: datetime
    knowledge_at: datetime
    is_primary_source: bool = True
    event_category: Optional[str] = None
    sentiment_score: Optional[float] = Field(None, ge=-1.0, le=1.0)
    materiality_score: Optional[int] = Field(None, ge=1, le=5)


class CatalystEvent(BaseModel):
    id: str
    symbol: str
    company_name: str
    market: Literal["US", "CA", "MACRO"]
    source: str
    source_url: str
    headline: str
    summary: str
    filing_date: str
    published_at: str
    event_category: str
    materiality_level: Literal["HIGH", "MEDIUM", "LOW"]
    materiality_score: int = Field(..., ge=1, le=5)
    materiality_rationale: str
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    sentiment_label: Literal["BULLISH", "LEAN_BULLISH", "NEUTRAL", "LEAN_BEARISH", "BEARISH"]
    sentiment_drivers: List[str] = Field(default_factory=list)
    horizon: Literal["IMMEDIATE", "SHORT_TERM", "MEDIUM_TERM", "STRUCTURAL"]
    priced_in_status: Literal["FRESH", "PARTIAL", "PRICED_IN"]
    reaction_1d_pct: Optional[float] = None
    reaction_3d_pct: Optional[float] = None
    rvol_at_event: Optional[float] = None
    factual_claims: List[str] = Field(default_factory=list)
    invalidation_risks: List[str] = Field(default_factory=list)
    hard_gate_triggered: bool = False
    score_impact_pts: float = 0.0


class ScoreRecord(BaseModel):
    security_id: int
    as_of_date: date
    knowledge_at: datetime
    technical_score: int = Field(..., ge=0, le=100)
    fundamental_score: Optional[int] = Field(None, ge=0, le=100)
    regime_state: str
    regime_multiplier: float
    news_contribution: float = 0.0
    sector_contribution: float = 0.0
    risk_penalty: float = 0.0
    composite_score: int = Field(..., ge=0, le=100)
    confidence_tier: Literal["A", "B", "C", "D"]
    data_quality_floor: Literal["HIGH", "MEDIUM", "LOW"]
    factor_attribution_json: Dict[str, Any]
    model_version: str


class SignalPlanContract(BaseModel):
    stance: Literal["POTENTIAL LONG SETUP", "NO_SETUP", "EXIT_ADVISORY"] = "POTENTIAL LONG SETUP"
    setup_type: Literal[
        "BREAKOUT",
        "PULLBACK",
        "BASE_BREAKOUT",
        "TREND_CONTINUATION",
        "OVERSOLD_REVERSAL",
        "EARNINGS_DRIFT",
    ] = "PULLBACK"
    entry_zone_low: float
    entry_zone_high: float
    preferred_entry: float
    alt_entry: float
    stop_loss: float
    stop_method: str = "ATR_AND_STRUCTURE"
    stop_distance_pct: float
    risk_per_share: float
    target_1: float
    target_1_basis: str
    target_2: float
    target_2_basis: str
    target_3: float
    target_3_basis: str
    planned_rr_t1: float
    planned_rr_t2: float
    realized_rr_t1: float
    holding_period_desc: str
    strength: int = Field(..., ge=0, le=100)
    confidence_tier: Literal["A", "B", "C", "D"]
    rationale: Dict[str, List[str]]
    invalidation_predicates: List[Dict[str, Any]]
    risks: List[str]
    data_lineage: Dict[str, Any]
    disclaimer_version: str


class ExitVerdict(BaseModel):
    position_id: Optional[int] = None
    symbol: str
    state: Literal["HOLD", "WATCH", "REDUCE", "EXIT", "EMERGENCY_RISK"]
    active_triggers: List[str]
    trigger_severity: Literal["INFO", "WATCH", "REDUCE", "EXIT", "EMERGENCY"]
    trigger_details: List[Dict[str, Any]]
    pnl_pct: float
    r_multiple: float
    reversal_condition: str
    escalation_cycles: int = 0
    timestamp: str
    primary_trigger: Optional[str] = None
    action_summary: Optional[str] = None
    target_allocation_pct: float = 100.0
    suggested_trailing_stop: Optional[float] = None

    @property
    def recommended_state(self) -> str:
        return self.state

    def to_dict(self) -> Dict[str, Any]:
        d = self.model_dump()
        d["recommended_state"] = self.state
        return d


class JournalPosition(BaseModel):
    id: Optional[int] = None
    user_id: str = "default_user"
    symbol: str
    direction: Literal["LONG", "SHORT"] = "LONG"
    shares: float = Field(..., gt=0)
    entry_price: float = Field(..., gt=0)
    entry_date: str
    stop_loss: Optional[float] = None
    profit_target: Optional[float] = None
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    status: Literal["OPEN", "CLOSED", "WATCHLIST"] = "OPEN"
    currency: Literal["USD", "CAD"] = "USD"
    conviction: int = Field(default=3, ge=1, le=5)
    thesis_notes: Optional[str] = None
    strategy_tag: Optional[str] = None
    market: Literal["US", "CA"] = "US"
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    risk_reward_ratio: Optional[float] = None


class WatchlistItem(BaseModel):
    item_id: Optional[int] = None
    watchlist_id: int
    symbol: str
    company_name: Optional[str] = None
    market: Literal["US", "CA"] = "US"
    currency: Literal["USD", "CAD"] = "USD"
    last_price: Optional[float] = None
    day_change_pct: Optional[float] = None
    rvol: Optional[float] = None
    composite_score: Optional[int] = None
    notes: Optional[str] = None
    added_at: str


class Watchlist(BaseModel):
    watchlist_id: int
    name: str
    description: Optional[str] = None
    created_at: str
    items: List[WatchlistItem] = Field(default_factory=list)


class FeedHealth(BaseModel):
    feed_id: str
    name: str
    market: Literal["US", "CA", "MACRO"]
    primary_source: str
    status: Literal["HEALTHY", "DEGRADED", "STALE", "DOWN"]
    freshness_hours: float
    staleness_badge: Literal["FRESH", "WARNING", "STALE"]
    last_synced_at: str
    records_count: int
    missing_days_count: int = 0
    coverage_pct: float = 100.0
    notes: str


# ==============================================================================
# PHASE 8: PAPER TRADING & RESOLUTION TRACKING (MFE / MAE / SCOREBOARD)
# ==============================================================================

class PaperTradeRecord(BaseModel):
    trade_id: Optional[int] = None
    portfolio_id: str = "default_paper_book"
    strategy_id: str
    symbol: str
    direction: Literal["LONG", "SHORT"] = "LONG"
    status: Literal["OPEN", "CLOSED"] = "OPEN"
    signal_date: str
    entry_date: str
    entry_price: float
    entry_price_net: float
    shares: int
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    initial_risk_per_share: float
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_price_net: Optional[float] = None
    exit_reason: Optional[str] = None
    gross_pnl_usd: float = 0.0
    net_pnl_usd: float = 0.0
    fee_drag_usd: float = 0.0
    return_pct: float = 0.0
    r_multiple: float = 0.0
    mfe_pct: float = 0.0
    mfe_r: float = 0.0
    mae_pct: float = 0.0
    mae_r: float = 0.0
    holding_days: int = 0
    currency: Literal["USD", "CAD"] = "USD"
    is_random_control: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class StrategyPaperScorecard(BaseModel):
    strategy_id: str
    strategy_name: str
    status: Literal["LIVE_QUALIFIED", "INCUBATING", "DEGRADED", "RETIRED"]
    total_trades: int
    win_rate_pct: float
    expectancy_r: float
    sharpe_ratio: float
    profit_factor: float
    max_drawdown_pct: float
    backtest_expectancy_r: float
    backtest_win_rate_pct: float
    degradation_pct: float
    random_control_expectancy_r: float
    random_control_win_rate_pct: float
    excess_over_random_r: float
    avg_mfe_r: float
    avg_mae_r: float
    mfe_mae_ratio: float
    promotion_verdict: str

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class PortfolioHoldingRecord(BaseModel):
    symbol: str
    company_name: str
    exchange: str
    country: Literal["US", "CA"]
    currency: Literal["USD", "CAD"]
    shares: float
    cost_basis_per_share: float
    cost_basis_total: float
    current_price: float
    market_value_local: float
    market_value_base: float
    unrealized_pnl_base: float
    unrealized_pnl_pct: float
    weight_pct: float
    sector: str
    industry: str
    beta_local: float
    beta_cross: float
    risk_contribution_pct: float
    daily_volatility_pct: float
    days_to_liquidate: float
    exit_verdict: Literal["HOLD", "WATCH", "REDUCE", "EXIT"] = "HOLD"
    exit_trigger: Optional[str] = None
    exit_score: int = 0
    suggested_trailing_stop: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class PortfolioAnalyticsReport(BaseModel):
    portfolio_id: str
    portfolio_name: str
    as_of_date: str
    base_currency: Literal["USD", "CAD"]
    risk_profile: Literal["CONSERVATIVE", "MODERATE", "AGGRESSIVE"]
    total_cost_basis_base: float
    total_market_value_base: float
    total_unrealized_pnl_base: float
    total_unrealized_pnl_pct: float
    cash_base: float
    effective_bets: float
    portfolio_beta_local: float
    portfolio_beta_cross: float
    portfolio_daily_vol_pct: float
    portfolio_annualized_vol_pct: float
    var_95_daily_pct: float
    cvar_95_daily_pct: float
    fx_exposure: Dict[str, Any]
    sector_concentrations: Dict[str, float]
    correlation_clusters: List[Dict[str, Any]]
    stress_replays: List[Dict[str, Any]]
    circuit_breakers: List[Dict[str, Any]]
    holdings: List[PortfolioHoldingRecord]
    disclaimers: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class AlertRecord(BaseModel):
    alert_id: str
    timestamp: str
    symbol: Optional[str] = None
    taxonomy: Literal[
        "TECHNICAL_BREAKOUT",
        "EXIT_TRIGGER_ESCALATION",
        "REGIME_SHIFT",
        "CATALYST_MATERIALITY",
        "PORTFOLIO_CIRCUIT_BREAKER",
        "PRICE_VOLUME_SPIKE"
    ]
    severity: Literal["INFO", "NOTICE", "WARNING", "CRITICAL"]
    headline: str
    body: str
    channels: List[Literal["IN_APP", "EMAIL", "WEB_PUSH", "SMS"]]
    dedupe_key: str
    data_payload: Dict[str, Any] = {}
    is_quiet_hours_eligible: bool = True
    disclaimer: str

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ConsentRecord(BaseModel):
    consent_id: str
    user_id: str
    channel: Literal["EMAIL", "SMS", "WEB_PUSH", "IN_APP"]
    consent_type: Literal["EXPRESS_OPT_IN", "DOUBLE_OPT_IN_SMS", "IMPLIED_TRANSACTIONAL"]
    jurisdiction: Literal["CA", "US"]
    ip_address: str
    user_agent: str
    granted_at: str
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None
    unsubscribe_token: str
    evidence_text: str

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class AlertPolicyConfig(BaseModel):
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = "21:00"
    quiet_hours_end: str = "07:00"
    user_timezone: str = "America/Vancouver"
    max_alerts_per_day_email: int = 10
    max_alerts_per_day_sms: int = 5
    cooldown_minutes_per_symbol: int = 120

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class AlertDeliveryTelemetry(BaseModel):
    p95_latency_email_seconds: float
    p95_latency_in_app_seconds: float
    bounce_rate_pct: float
    monthly_opt_out_rate_pct: float
    total_delivered_24h: int
    total_suppressed_quiet_hours: int
    total_suppressed_budget: int
    total_suppressed_cooldown: int

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()




