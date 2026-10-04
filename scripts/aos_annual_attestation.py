#!/usr/bin/env python3
"""
Annual Automated Order System Attestation -- CIRO / UMIR Policy 7.1 Part 8

Policy 7.1 Part 8 requires an automated order system to be tested before use and at least
annually, with a written record. This script produces that record by actually running the
control-coverage suite and writing both a JSON artifact and a database row.

Exit codes are meaningful so CI can gate on them:
    0  attestation passed
    1  attestation failed (missing coverage, failing tests, or kill switch not verified)
    2  could not run

Usage:
    python3 scripts/aos_annual_attestation.py            # run and record
    python3 scripts/aos_annual_attestation.py --check    # report status only, run nothing
    python3 scripts/aos_annual_attestation.py --year 2026
    python3 scripts/aos_annual_attestation.py --no-tests # skip the suites (records a FAIL)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution.aos_attestation import (  # noqa: E402
    ATTESTATION_VALIDITY_DAYS,
    artifact_digest,
    latest_attestation,
    run_attestation,
)


def _print_report(att, *, ran: bool) -> None:
    status = "PASSED" if att.passed else "FAILED"
    print("=" * 70)
    print(f" AUTOMATED ORDER SYSTEM ATTESTATION -- {att.year}   [{status}]")
    print("=" * 70)
    print(f"  Attested at            : {att.attested_at}")
    print(f"  Code version           : {att.code_version[:12]}")
    print(f"  Controls registered    : {att.controls_registered}")
    print(f"  Controls with tests    : {att.controls_with_tests}")
    if att.controls_without_tests:
        print(f"  !! WITHOUT COVERAGE    : {', '.join(att.controls_without_tests)}")
    print(f"  Routing gates checked  : {att.routing_gates_checked}")
    print(f"  State machine edges    : {att.state_machine_edges}")
    print(f"  Kill switch verified   : {att.kill_switch_verified}")
    if ran:
        print(f"  Test suites run        : {att.suites_run}")
        print(f"  Tests passed / failed  : {att.tests_passed} / {att.tests_failed}")
    else:
        print("  Test suites run        : SKIPPED (--no-tests; recorded as a failure)")
    print(f"  Days until re-test due : {att.days_until_expiry}")
    print(f"  Artifact               : {att.artifact_path}")
    digest = artifact_digest(att.artifact_path)
    if digest:
        print(f"  Artifact SHA-256       : {digest}")
    print("-" * 70)
    print(f"  Scope: {att.scope_note}")
    print("=" * 70)


def _print_status(att) -> None:
    print("=" * 70)
    if att is None:
        print(" NO ATTESTATION ON RECORD")
        print(" The automated order system has never been attested.")
        print(" Run: python3 scripts/aos_annual_attestation.py")
        print("=" * 70)
        return
    state = "EXPIRED" if att.expired else "CURRENT"
    print(f" AOS ATTESTATION STATUS -- {att.year}   [{state}]")
    print("=" * 70)
    print(f"  Last attested          : {att.attested_at}")
    print(f"  Result                 : {'PASSED' if att.passed else 'FAILED'}")
    print(f"  Code version           : {att.code_version[:12]}")
    print(f"  Controls covered       : {att.controls_with_tests}/{att.controls_registered}")
    print(f"  Kill switch verified   : {att.kill_switch_verified}")
    print(f"  Tests passed / failed  : {att.tests_passed} / {att.tests_failed}")
    print(f"  Days until re-test due : {att.days_until_expiry} "
          f"(validity {ATTESTATION_VALIDITY_DAYS} days)")
    if att.expired:
        print("  !! Policy 7.1 Part 8 requires re-testing. Run this script now.")
    print("=" * 70)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=None,
                    help="Attestation year (default: current UTC year)")
    ap.add_argument("--check", action="store_true",
                    help="Report the current attestation status without running anything")
    ap.add_argument("--no-tests", action="store_true",
                    help="Skip the test suites. The attestation is then recorded as FAILED.")
    args = ap.parse_args()

    if args.check:
        att = latest_attestation()
        _print_status(att)
        if att is None or att.expired or not att.passed:
            return 1
        return 0

    try:
        att = run_attestation(year=args.year, run_tests=not args.no_tests)
    except Exception as exc:
        print(f"ERROR: attestation could not be run: {exc}", file=sys.stderr)
        return 2

    _print_report(att, ran=not args.no_tests)
    return 0 if att.passed else 1


if __name__ == "__main__":
    sys.exit(main())
