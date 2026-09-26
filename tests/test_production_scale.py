"""
Unit & Integration Tests: Production Scaling, Exchange Entitlements & Disaster Recovery (Phase 12)
US + Canada Market Intelligence Platform

Verifies:
- User data classification segregation (Non-Professional Retail vs Professional Institutional)
- Formal exchange monthly subscriber declarations for TMX Datalinx & Nasdaq Basic
- Multi-region disaster recovery drill execution (DoD SLA: RTO < 15 min, RPO < 5 min)
- Capacity stress testing & unit economics across 100 / 1K / 10K / 100K users
- SOC 2 Type I readiness control matrix evaluation
- 60-day rolling uptime verification (DoD SLA: >= 99.90%)
- Impersonal decision-support compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

import json
import pytest

from src.engine.production_scale import production_scale_engine
from src.compliance.linter import linter
from src.models.schemas import (
    UserEntitlementRecord,
    ExchangeAuditDeclaration,
    DisasterRecoveryDrillRecord,
    TierUnitEconomics,
    SOC2ControlAudit,
)


def test_user_entitlements_segregation():
    """Verify strict segregation between Non-Professional retail and Professional institutional seats."""
    summary = production_scale_engine.get_entitlements_summary()

    assert summary["total_licensed_subscribers"] >= 4
    assert summary["retail_non_professional_count"] >= 2
    assert summary["professional_institutional_count"] >= 2
    assert summary["monthly_exchange_accrual_usd"] > 0
    assert summary["attestation_compliance_rate_pct"] == 100.0

    # Ensure pro subscribers pay higher exchange fees than retail non-pro
    pro_users = [e for e in summary["entitlements"] if e["data_class"] == "PROFESSIONAL_INSTITUTIONAL"]
    ret_users = [e for e in summary["entitlements"] if e["data_class"] == "RETAIL_NON_PROFESSIONAL"]
    assert all(p["monthly_exchange_fee_usd"] > 25.0 for p in pro_users)
    assert all(r["monthly_exchange_fee_usd"] < 5.0 for r in ret_users)


def test_exchange_audit_declarations_tmx_nasdaq():
    """Verify formal monthly subscriber reporting for TMX Datalinx and Nasdaq Basic."""
    declarations = production_scale_engine.generate_exchange_audit_declarations("2026-08")
    assert len(declarations) >= 3

    auths = {d.exchange_authority for d in declarations}
    assert "TMX_DATALINX" in auths
    assert "NASDAQ_BASIC" in auths
    assert "NYSE_CTA" in auths

    for d in declarations:
        assert d.reporting_period == "2026-08"
        assert d.non_pro_subscribers > 1000
        assert d.pro_subscribers > 20
        assert d.total_payable_usd > 1000.0
        assert d.audit_status == "VERIFIED"
        assert len(d.compliance_certification) > 20


def test_disaster_recovery_drill_dod_sla():
    """DoD: Verify that automated DR drill achieves RTO < 15 min and RPO < 5 min."""
    drill = production_scale_engine.run_disaster_recovery_drill(
        scenario="Automated Standby Promotion Test",
        target_region="ca-central-1 (Secondary Hot Replica)",
    )

    assert drill.outcome == "PASSED"
    assert drill.rto_actual_minutes < drill.rto_target_minutes
    assert drill.rto_actual_minutes < 15.0, f"RTO {drill.rto_actual_minutes}m exceeded 15m DoD SLA"
    assert drill.rpo_actual_minutes < drill.rpo_target_minutes
    assert drill.rpo_actual_minutes < 5.0, f"RPO {drill.rpo_actual_minutes}m exceeded 5m DoD SLA"
    assert "sha256_" in drill.validation_hash


def test_capacity_stress_scaling_100k_users():
    """DoD: Validate capacity scaling models across 100 to 100K users."""
    model = production_scale_engine.get_capacity_scaling_model()
    levels = model["tested_capacity_levels"]

    assert "100_users" in levels
    assert "1000_users" in levels
    assert "10000_users" in levels
    assert "100000_users" in levels

    k100 = levels["100000_users"]
    assert k100["active_users"] == 100000
    assert k100["concurrent_peak"] >= 20000
    assert k100["p95_latency_ms"] < 100.0  # Fast edge CDN response
    assert k100["gross_margin_pct"] >= 95.0
    assert k100["monthly_mrr_usd"] > 1000000.0
    assert model["static_cache_hit_rate_pct"] > 90.0


def test_tier_unit_economics_positive_margins():
    """Verify unit economics across Free, Pro, and Institutional tiers."""
    tiers = production_scale_engine.unit_economics
    assert len(tiers) == 3

    pro = next(t for t in tiers if t.tier_name == "PRO_RESEARCHER")
    assert pro.mrr_per_user_usd == 29.00
    assert pro.gross_margin_pct >= 85.0

    inst = next(t for t in tiers if t.tier_name == "INSTITUTIONAL_DESK")
    assert inst.mrr_per_user_usd == 199.00
    assert inst.gross_margin_pct >= 85.0


def test_soc2_controls_compliance():
    """Verify SOC 2 Type I readiness controls mapping across Trust Services Criteria."""
    controls = production_scale_engine.soc2_controls
    assert len(controls) >= 6

    ctrl_ids = {c.control_id for c in controls}
    assert "CC6.1" in ctrl_ids
    assert "CC6.6" in ctrl_ids
    assert "CC7.2" in ctrl_ids
    assert "A1.2" in ctrl_ids
    assert "C1.1" in ctrl_ids
    assert "PI1.2" in ctrl_ids

    for c in controls:
        assert c.status == "COMPLIANT"
        assert len(c.evidence_summary) > 20
        assert len(c.last_tested) == 10


def test_60_day_uptime_dod_sla():
    """DoD: Verify that rolling 60-day availability meets or exceeds 99.90% SLA."""
    status = production_scale_engine.get_production_status()

    assert status["rolling_60_day_uptime_pct"] >= 99.90
    assert status["sla_target_met"] is True
    assert status["active_outages_count"] == 0
    assert len(status["components"]) >= 6

    # Verify all components report operational
    for comp in status["components"]:
        assert comp["status"] == "OPERATIONAL"
        assert comp["uptime_pct"] >= 99.90


def test_impersonal_advice_linter_compliance_phase12():
    """Verify that all Phase 12 text outputs pass the ImpersonalAdviceLinter."""
    feed = production_scale_engine.generate_production_health_feed()

    for d in feed["exchange_declarations"]:
        linter.assert_clean(d["compliance_certification"])

    for c in feed["soc2_matrix"]:
        linter.assert_clean(c["title"])
        linter.assert_clean(c["evidence_summary"])

    for disc in feed["disclaimers"]:
        linter.assert_clean(disc)


def test_production_health_feed_export_and_staging():
    """Verify exported production_health.json feed exists on disk and matches schema."""
    with open("data/feeds/production_health.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "production_status" in data
    assert data["production_status"]["rolling_60_day_uptime_pct"] >= 99.90
    assert "capacity_scaling" in data
    assert "exchange_declarations" in data
    assert len(data["exchange_declarations"]) >= 3
    assert len(data["soc2_matrix"]) >= 6
