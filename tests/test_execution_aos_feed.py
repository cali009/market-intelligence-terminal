"""
Phase 34.5 Test Suite -- AOS Attestation and the Execution Gateway Feed

The properties this suite exists to protect:

  1. THE ATTESTATION IS EVIDENCE, NOT ASSERTION. It must fail when coverage is missing or
     the kill switch does not actually halt routing. An attestation that always passes is
     worse than none, because CIRO Policy 7.1 Part 8 expects a real written record.

  2. THE FEED PUBLISHES THE SAFETY FLAGS. `external_adapters_registered` must be 0 and
     `canadian_routing.external_routing_permitted` must be False, so a reader can verify
     the platform's routing posture from the artifact rather than taking it on trust.

  3. THE SCOPE IS BOUNDED IN THE ARTIFACT ITSELF. The attestation and feed must state what
     they do NOT attest -- profitability, fill realism, signal suitability.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.data.db import Database
from src.execution.aos_attestation import (
    ATTESTATION_VALIDITY_DAYS,
    EXECUTION_TEST_SUITES,
    REENTRANCY_GUARD_ENV,
    AosAttestation,
    artifact_digest,
    latest_attestation,
    run_attestation,
)
from src.execution.execution_telemetry import (
    build_default_registry,
    export_execution_gateway_feed,
)
from src.execution.risk_governor import CONTROL_REGISTRY
from src.execution.state_machine import LEGAL_TRANSITIONS


@pytest.fixture()
def db():
    return Database(db_url=f"sqlite:///{tempfile.mktemp(suffix='.db')}")


# ======================================================================================
# 1. Attestation evidence
# ======================================================================================

class TestAttestationEvidence:

    @pytest.fixture()
    def att(self, db):
        """`run_tests=False` keeps this fast; a separate test exercises the real suites."""
        return run_attestation(db=db, run_tests=False)

    def test_every_registered_control_has_coverage(self, att):
        """The core assertion of the artifact."""
        assert att.controls_registered == len(CONTROL_REGISTRY)
        assert att.controls_with_tests == len(CONTROL_REGISTRY)
        assert att.controls_without_tests == []

    def test_the_control_registry_is_the_one_from_34_2(self, att):
        assert att.controls_registered == 12

    def test_the_state_machine_edge_count_is_recorded(self, att):
        assert att.state_machine_edges == sum(len(v) for v in LEGAL_TRANSITIONS.values())
        assert att.state_machine_edges == 23

    def test_the_kill_switch_was_verified_live(self, att):
        """
        Not asserted -- exercised. run_attestation engages the kill switch, confirms
        routing halts, disengages, and confirms routing resumes.
        """
        assert att.kill_switch_verified is True

    def test_a_json_artifact_is_written(self, att):
        assert Path(att.artifact_path).exists()
        data = json.loads(Path(att.artifact_path).read_text(encoding="utf-8"))
        assert data["year"] == att.year
        assert data["kill_switch_verified"] is True

    def test_the_artifact_has_a_stable_digest(self, att):
        d = artifact_digest(att.artifact_path)
        assert len(d) == 64
        assert d == artifact_digest(att.artifact_path)

    def test_the_code_version_is_recorded(self, att):
        """Either a real commit or an explicit UNKNOWN -- never silently blank."""
        assert att.code_version
        assert att.code_version != ""

    def test_expiry_is_one_year_out(self, att):
        assert 0 < att.days_until_expiry <= ATTESTATION_VALIDITY_DAYS
        assert att.expired is False

    def test_without_tests_the_attestation_does_not_pass(self, db):
        """
        `suites_run == 0` means no evidence was gathered, so the attestation must record a
        failure rather than pass vacuously.
        """
        att = run_attestation(db=db, run_tests=False)
        assert att.suites_run == 0
        assert att.passed is False

    def test_the_scope_note_bounds_the_claim(self, att):
        """The artifact must say what it does NOT attest."""
        assert "Does not attest profitability" in att.scope_note
        assert "Unregistered impersonal research" in att.scope_note

    def test_to_dict_includes_derived_fields(self, att):
        d = att.to_dict()
        assert "days_until_expiry" in d
        assert "expired" in d


class TestAttestationWithRealSuites:

    @pytest.mark.skipif(
        os.environ.get(REENTRANCY_GUARD_ENV) == "1",
        reason="already running inside an attestation; recursing would never terminate",
    )
    def test_the_execution_suites_actually_pass(self, db):
        """
        The slow test, and the one that makes the artifact mean something: it runs the real
        Phase 34 suites and requires zero failures.

        Skipped when this suite is itself being run by an attestation, because this suite is
        part of EXECUTION_TEST_SUITES and would otherwise start a nested attestation.
        """
        att = run_attestation(db=db, run_tests=True)
        assert att.suites_run == len(EXECUTION_TEST_SUITES)
        assert att.tests_failed == 0
        assert att.tests_passed > 300
        assert att.passed is True


class TestAttestationPersistence:

    def test_no_record_returns_none(self, db):
        assert latest_attestation(db=db) is None

    def test_the_record_round_trips(self, db):
        run_attestation(db=db, run_tests=False)
        r = latest_attestation(db=db)
        assert r is not None
        assert r.controls_registered == 12
        assert r.kill_switch_verified is True
        assert r.controls_without_tests == []

    def test_re_attesting_the_same_year_updates_rather_than_duplicates(self, db):
        run_attestation(db=db, run_tests=False)
        run_attestation(db=db, run_tests=False)
        rows = db.execute_query("SELECT COUNT(*) n FROM aos_attestation;")
        assert rows[0]["n"] == 1

    def test_a_missing_artifact_digests_to_empty(self):
        assert artifact_digest(None) == ""
        assert artifact_digest("/nonexistent/aos_1999.json") == ""


class TestAttestationExpiry:

    def test_a_year_old_attestation_is_expired(self):
        att = AosAttestation(year=2025, attested_at="2025-01-01T00:00:00+00:00",
                             code_version="x")
        assert att.expired is True
        assert att.days_until_expiry < 0

    def test_a_malformed_timestamp_fails_closed_as_expired(self):
        """
        An unreadable attestation date cannot be shown to be current, so it must read as
        expired. Returning 0 here would have reported a compliance record as valid.
        """
        att = AosAttestation(year=2026, attested_at="not-a-timestamp", code_version="x")
        assert att.days_until_expiry == -1
        assert att.expired is True


# ======================================================================================
# 2. The default registry
# ======================================================================================

class TestDefaultRegistry:

    def test_exactly_one_adapter_ships(self):
        reg = build_default_registry()
        assert [a.adapter_id for a in reg.all()] == ["INTERNAL_SIMULATOR"]

    def test_no_external_adapter_is_registered(self):
        """The Alpaca determination and CIRO DMR 3200 together mean none may ship."""
        reg = build_default_registry()
        assert not any(a.is_external for a in reg.all())

    def test_the_registered_adapter_is_license_cleared(self):
        reg = build_default_registry()
        assert all(a.license_cleared for a in reg.all())

    def test_the_registered_adapter_serves_both_markets(self):
        from src.execution.order_model import Market
        reg = build_default_registry()
        a = reg.all()[0]
        assert a.supports_market(Market.US) and a.supports_market(Market.CA)


# ======================================================================================
# 3. The feed
# ======================================================================================

class TestExecutionGatewayFeed:

    @pytest.fixture()
    def feed(self, db, tmp_path):
        run_attestation(db=db, run_tests=False)
        path = export_execution_gateway_feed(
            db=db, output_path=str(tmp_path / "execution_gateway.json"))
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def test_required_top_level_keys(self, feed):
        for key in ("as_of_date", "generated_at", "session", "predicate_28", "adapters",
                    "external_adapters_registered", "canadian_routing", "kill_switch",
                    "aos_attestation", "disclaimers"):
            assert key in feed, f"missing {key}"

    def test_both_timestamps_are_present(self, feed):
        """
        Every feed carries two wall-clock stamps and both are needed: as_of_date is the UTC
        session, generated_at is the export instant. See docs/16 §10.
        """
        assert feed["as_of_date"]
        assert feed["generated_at"]

    def test_no_external_adapter_is_published(self, feed):
        assert feed["external_adapters_registered"] == 0
        assert all(not a["is_external"] for a in feed["adapters"])

    def test_the_published_adapter_declares_its_licence(self, feed):
        assert all(a["license_cleared"] for a in feed["adapters"])

    def test_canadian_external_routing_is_published_as_forbidden(self, feed):
        assert feed["canadian_routing"]["external_routing_permitted"] is False
        assert "CIRO" in feed["canadian_routing"]["basis"]

    def test_predicate_28_block_is_present_with_thresholds(self, feed):
        p = feed["predicate_28"]
        assert p["predicate_type"] == "PRE_TRADE_RISK_BREACH"
        assert p["thresholds"]["hard_rejection_trigger"] == 3
        assert p["thresholds"]["critical_rejections"] == 10
        assert p["thresholds"]["rejected_notional_share"] == 0.25

    def test_the_feed_states_the_boundary_is_exclusive(self, feed):
        """Published so a consumer cannot misread >= as >."""
        assert feed["predicate_28"]["thresholds"]["boundary_exclusive"] is True

    def test_a_quiet_session_does_not_fire(self, db, tmp_path):
        feed = json.loads(Path(export_execution_gateway_feed(
            db=db, output_path=str(tmp_path / "quiet.json"), session="1999-01-01",
        )).read_text(encoding="utf-8"))
        assert feed["predicate_28"]["fired"] is False
        assert feed["predicate_28"]["severity"] is None
        assert feed["session"]["evaluations"] == 0

    def test_kill_switch_state_is_published(self, feed):
        assert feed["kill_switch"]["halted"] is False
        assert isinstance(feed["kill_switch"]["active_scopes"], list)

    def test_the_attestation_is_embedded(self, feed):
        a = feed["aos_attestation"]
        assert a is not None
        assert a["controls_registered"] == 12
        assert "artifact_sha256" in a

    def test_disclaimers_bound_the_claim(self, feed):
        joined = " ".join(feed["disclaimers"]).lower()
        assert "not investment advice" in joined
        assert "no profitability claim" in joined
        assert "paper simulation only" in joined

    def test_the_feed_is_json_serialisable(self, feed):
        assert json.loads(json.dumps(feed)) == feed

    def test_the_default_output_path_is_the_feeds_directory(self, db, tmp_path, monkeypatch):
        """
        DATA_DIR is redirected to a temp directory. An earlier version of this test wrote to
        the real feeds directory using a temp database, which clobbered the production feed
        with a stub that had no attestation. A test must not mutate shipped artifacts.
        """
        import config.settings as settings
        monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
        path = export_execution_gateway_feed(db=db)
        assert path == str(tmp_path / "feeds" / "execution_gateway.json")
        assert Path(path).exists()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data["external_adapters_registered"] == 0

    def test_the_production_feed_is_not_touched(self, db, tmp_path, monkeypatch):
        """Guards the regression above: nothing may be written under the real DATA_DIR."""
        import config.settings as settings
        real_dir = settings.DATA_DIR
        monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
        before = (real_dir / "feeds" / "execution_gateway.json")
        mtime = before.stat().st_mtime if before.exists() else None
        export_execution_gateway_feed(db=db)
        after = before.stat().st_mtime if before.exists() else None
        assert mtime == after, "the shipped feed must not be modified by this suite"


# ======================================================================================
# 4. Evidence-set completeness and the reentrancy guard
# ======================================================================================

class TestEvidenceSetCompleteness:

    def test_the_attestation_covers_its_own_suite(self):
        """
        An attestation whose evidence set omitted its own tests would understate coverage.
        This suite must be in the list.
        """
        assert "tests/test_execution_aos_feed.py" in EXECUTION_TEST_SUITES

    def test_every_listed_suite_exists(self):
        """A missing path is skipped silently by the runner, so a typo would quietly shrink
        the evidence set. Assert the list matches reality."""
        from config.settings import BASE_DIR
        missing = [s for s in EXECUTION_TEST_SUITES if not (BASE_DIR / s).exists()]
        assert missing == [], f"EXECUTION_TEST_SUITES references files that do not exist: {missing}"

    def test_the_reentrancy_guard_is_defined(self):
        """
        Including this suite in the evidence set is only safe because of the guard. If the
        constant disappeared, the attestation would recurse until the process died.
        """
        assert REENTRANCY_GUARD_ENV == "AOS_ATTESTATION_IN_PROGRESS"

    def test_the_guard_is_passed_to_every_suite_subprocess(self, monkeypatch):
        """
        Verifies the mechanism that makes self-inclusion safe: `_run_test_suites` must set
        the guard in the child environment, or a suite that calls run_attestation would
        start a nested attestation without bound.

        Asserted by intercepting subprocess.run rather than by launching a nested pytest
        run. The nested version took two minutes, contended for the SQLite lock against its
        own parent, and hung. A subprocess-spawning test is the wrong instrument for
        checking that an environment variable is set.
        """
        import src.execution.aos_attestation as aos
        seen = []

        class _Result:
            returncode = 0
            stdout = "5 passed in 0.01s"

        def fake_run(cmd, **kwargs):
            seen.append(kwargs.get("env"))
            return _Result()

        monkeypatch.setattr(aos.subprocess, "run", fake_run)
        aos._run_test_suites()

        assert seen, "no suites were launched"
        assert len(seen) == len(EXECUTION_TEST_SUITES)
        for env in seen:
            assert env is not None, "env was not passed to the subprocess"
            assert env.get(REENTRANCY_GUARD_ENV) == "1"
