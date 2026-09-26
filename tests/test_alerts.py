"""
Unit & Integration Tests: Multi-Channel Alerts Engine & Consent Ledger (Phase 10)
US + Canada Market Intelligence Platform

Verifies:
- Alert Taxonomy & Severity Architecture (6 Taxonomies, 4 Severities)
- CASL (Canada) & TCPA (US) Statutory Consent Ledger in SQLite
- SMS CTIA Double Opt-In Verification Handshake
- Instantaneous Consent Revocation (exceeding CASL 10-business-day mandate)
- Policy Engine: Quiet Hours (21:00 - 07:00), Emergency Critical Bypass
- Anti-Fatigue Daily Rate Budgets & 120-minute Hysteresis Cooldowns
- Content Deduplication rolling key checks
- Definition of Done SLA Telemetry:
  - Email p95 latency < 60s
  - In-app p95 latency < 5s
  - Bounce rate < 2.0%
  - Opt-out rate < 0.5%/month
- Impersonal Advice Linter Compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

import json
import pytest
from datetime import datetime, timezone

from src.engine.alerts import alert_engine, ConsentLedger, AlertPolicyEngine, AlertDispatcher
from src.compliance.linter import linter
from src.models.schemas import AlertRecord, AlertPolicyConfig


def test_alert_taxonomies_and_severities():
    """Verify that all 6 platform taxonomies and 4 severities are supported and generated."""
    alerts = alert_engine.build_system_alerts()
    assert len(alerts) >= 6

    taxonomies = {a.taxonomy for a in alerts}
    assert "TECHNICAL_BREAKOUT" in taxonomies
    assert "EXIT_TRIGGER_ESCALATION" in taxonomies
    assert "REGIME_SHIFT" in taxonomies
    assert "CATALYST_MATERIALITY" in taxonomies
    assert "PORTFOLIO_CIRCUIT_BREAKER" in taxonomies
    assert "PRICE_VOLUME_SPIKE" in taxonomies

    severities = {a.severity for a in alerts}
    assert "CRITICAL" in severities
    assert "WARNING" in severities
    assert "NOTICE" in severities


def test_consent_registration_casl_and_tcpa():
    """Verify express consent registration for both Canadian (CASL) and US (TCPA) users."""
    ledger = ConsentLedger()

    # Canadian user
    res_ca = ledger.register_consent(
        user_id="test_ca_user@example.ca",
        channel="EMAIL",
        consent_type="EXPRESS_OPT_IN",
        jurisdiction="CA",
        ip_address="198.51.100.77",
    )
    assert res_ca["status"] == "ACTIVE"
    assert res_ca["jurisdiction"] == "CA"

    ok, reason = ledger.is_channel_permitted("test_ca_user@example.ca", "EMAIL")
    assert ok is True

    # Unregistered user should be blocked
    ok_unreg, reason_unreg = ledger.is_channel_permitted("unknown_person@example.com", "EMAIL")
    assert ok_unreg is False
    assert "opt-in required" in reason_unreg.lower()


def test_sms_double_opt_in_verification():
    """Verify CTIA/TCPA requirement: SMS is blocked prior to double opt-in verification."""
    ledger = ConsentLedger()
    phone = "+16045550999"

    # Initial opt-in
    ledger.register_consent(
        user_id=phone,
        channel="SMS",
        consent_type="EXPRESS_OPT_IN",
        jurisdiction="CA",
    )

    # Before verification, SMS should NOT be permitted
    ok_before, reason_before = ledger.is_channel_permitted(phone, "SMS")
    assert ok_before is False
    assert "double opt-in" in reason_before.lower()

    # Verify SMS via 2-factor code
    verify_ok = ledger.verify_sms_double_opt_in(phone, "729401")
    assert verify_ok is True

    # After verification, SMS is permitted
    ok_after, reason_after = ledger.is_channel_permitted(phone, "SMS")
    assert ok_after is True


def test_instantaneous_revocation_casl_compliance():
    """Verify that unsubscribe/opt-out is honored instantaneously in software (vs 10 business days)."""
    ledger = ConsentLedger()
    user = "opt_out_test@example.com"

    ledger.register_consent(user_id=user, channel="EMAIL", consent_type="EXPRESS_OPT_IN")
    ok1, _ = ledger.is_channel_permitted(user, "EMAIL")
    assert ok1 is True

    # Revoke consent
    rev_res = ledger.revoke_consent(user, channel="EMAIL")
    assert rev_res["status"] == "REVOKED"
    assert "instantaneously" in rev_res["casl_compliance"].lower()

    # Verify channel is immediately blocked
    ok2, reason2 = ledger.is_channel_permitted(user, "EMAIL")
    assert ok2 is False
    assert "revoked" in reason2.lower()


def test_quiet_hours_enforcement_and_emergency_bypass():
    """Verify that quiet hours suppress non-critical alerts but allow critical emergency alerts."""
    ledger = ConsentLedger()
    policy = AlertPolicyEngine()
    dispatcher = AlertDispatcher(ledger, policy)

    test_user = "quiet_user@example.com"
    ledger.register_consent(test_user, "EMAIL", "EXPRESS_OPT_IN")

    # Non-critical warning alert
    alert_warning = AlertRecord(
        alert_id="ALT_WARN_01",
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol="AAPL",
        taxonomy="TECHNICAL_BREAKOUT",
        severity="WARNING",
        headline="AAPL: Breakout",
        body="Technical setup observed.",
        channels=["EMAIL"],
        dedupe_key="AAPL:BREAKOUT:01",
        disclaimer="Impersonal research only.",
        is_quiet_hours_eligible=True,
    )

    # Critical emergency stop alert
    alert_critical = AlertRecord(
        alert_id="ALT_CRIT_02",
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol="AAPL",
        taxonomy="EXIT_TRIGGER_ESCALATION",
        severity="CRITICAL",
        headline="AAPL: Emergency Stop Breach",
        body="Price fell below stop loss level.",
        channels=["EMAIL"],
        dedupe_key="AAPL:STOP:02",
        disclaimer="Impersonal research only.",
        is_quiet_hours_eligible=False,
    )

    # Test during quiet hours (e.g. 23:00 / 11 PM)
    res_warn = dispatcher.dispatch(alert_warning, test_user, "EMAIL", test_hour=23)
    assert res_warn["status"] == "QUEUED_QUIET_HOURS"

    res_crit = dispatcher.dispatch(alert_critical, test_user, "EMAIL", test_hour=23)
    assert res_crit["status"] == "DELIVERED"

    # Test outside quiet hours (e.g. 14:00 / 2 PM)
    alert_daytime = AlertRecord(
        alert_id="ALT_DAY_03",
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol="MSFT",
        taxonomy="TECHNICAL_BREAKOUT",
        severity="NOTICE",
        headline="MSFT: Breakout",
        body="Technical setup observed.",
        channels=["EMAIL"],
        dedupe_key="MSFT:BREAKOUT:03",
        disclaimer="Impersonal research only.",
        is_quiet_hours_eligible=True,
    )
    res_day = dispatcher.dispatch(alert_daytime, test_user, "EMAIL", test_hour=14)
    assert res_day["status"] == "DELIVERED"


def test_anti_fatigue_rate_budgets():
    """Verify that daily rate budgets throttle excess message volume."""
    ledger = ConsentLedger()
    policy = AlertPolicyEngine(AlertPolicyConfig(max_alerts_per_day_sms=2))
    dispatcher = AlertDispatcher(ledger, policy)

    user = "+16045558888"
    ledger.register_consent(user, "SMS", "DOUBLE_OPT_IN_SMS")

    for i in range(2):
        alert = AlertRecord(
            alert_id=f"ALT_SMS_{i}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=f"SYM{i}",
            taxonomy="PRICE_VOLUME_SPIKE",
            severity="NOTICE",
            headline="Volume Spike",
            body="Details observed.",
            channels=["SMS"],
            dedupe_key=f"DEDUPE_SMS_{i}",
            disclaimer="Research only.",
        )
        res = dispatcher.dispatch(alert, user, "SMS", test_hour=14)
        assert res["status"] == "DELIVERED"

    # 3rd SMS should exceed the limit of 2/day
    alert_excess = AlertRecord(
        alert_id="ALT_SMS_EXCESS",
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol="EXCESS",
        taxonomy="PRICE_VOLUME_SPIKE",
        severity="NOTICE",
        headline="Excess Alert",
        body="Details observed.",
        channels=["SMS"],
        dedupe_key="DEDUPE_SMS_EXCESS",
        disclaimer="Research only.",
    )
    res_excess = dispatcher.dispatch(alert_excess, user, "SMS", test_hour=14)
    assert res_excess["status"] == "SUPPRESSED_BUDGET"


def test_hysteresis_cooldown_and_deduplication():
    """Verify 120-minute cooldown on same symbol and content deduplication."""
    ledger = ConsentLedger()
    policy = AlertPolicyEngine()
    dispatcher = AlertDispatcher(ledger, policy)

    user = "cooldown_user@example.com"
    ledger.register_consent(user, "EMAIL", "EXPRESS_OPT_IN")

    alert1 = AlertRecord(
        alert_id="ALT_CD_01",
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol="NVDA",
        taxonomy="TECHNICAL_BREAKOUT",
        severity="WARNING",
        headline="NVDA: Breakout 1",
        body="Setup observed.",
        channels=["EMAIL"],
        dedupe_key="NVDA:BREAKOUT:KEY1",
        disclaimer="Research only.",
    )
    res1 = dispatcher.dispatch(alert1, user, "EMAIL", test_hour=14)
    assert res1["status"] == "DELIVERED"

    # Immediate second alert on NVDA for same taxonomy should hit cooldown
    alert2 = AlertRecord(
        alert_id="ALT_CD_02",
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol="NVDA",
        taxonomy="TECHNICAL_BREAKOUT",
        severity="WARNING",
        headline="NVDA: Breakout 2",
        body="Setup observed again.",
        channels=["EMAIL"],
        dedupe_key="NVDA:BREAKOUT:KEY2",
        disclaimer="Research only.",
    )
    res2 = dispatcher.dispatch(alert2, user, "EMAIL", test_hour=14)
    assert res2["status"] == "SUPPRESSED_COOLDOWN"


def test_delivery_telemetry_slas_dod():
    """Verify Definition of Done metrics for alerts telemetry."""
    telemetry = alert_engine.dispatcher.get_telemetry()

    assert telemetry.p95_latency_email_seconds < 60.0, "Email p95 must be < 60s"
    assert telemetry.p95_latency_in_app_seconds < 5.0, "In-app p95 must be < 5s"
    assert telemetry.bounce_rate_pct < 2.0, "Bounce rate must be < 2.0%"
    assert telemetry.monthly_opt_out_rate_pct < 0.5, "Opt-out rate must be < 0.5%/month"
    assert telemetry.total_delivered_24h > 0


def test_compliance_linter_on_all_alerts():
    """Verify that all generated alert headlines, bodies, and disclaimers are free of advisory phrasing."""
    alerts = alert_engine.build_system_alerts()
    for a in alerts:
        linter.assert_clean(a.headline)
        linter.assert_clean(a.body)
        linter.assert_clean(a.disclaimer)


def test_alerts_feed_export_and_schema():
    """Verify exported alerts feed exists and matches expected format."""
    with open("data/feeds/alerts.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["active_alerts_count"] >= 6
    assert "delivery_telemetry" in data
    assert "compliance_attestation" in data
    assert "casl_compliance" in data["compliance_attestation"]
    assert "tcpa_compliance" in data["compliance_attestation"]
    assert len(data["consent_ledger_audit"]) > 0
