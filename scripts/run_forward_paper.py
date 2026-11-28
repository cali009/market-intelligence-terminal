#!/usr/bin/env python3
"""
Run one forward-paper session: settle what is pending, capture today's decisions, then
verify the chain and publish a status feed.

Order matters. Settlement happens BEFORE capture so that a decision recorded today is
never filled from a price the run already knows. Capture is last because it must reflect
the close that has just printed, with tomorrow's open still unknown.

Intended to run from the daily cron after the US close, alongside the rest of the pipeline.

Usage:
    python3 scripts/run_forward_paper.py                 # settle + capture latest session
    python3 scripts/run_forward_paper.py --date 2026-11-27
    python3 scripts/run_forward_paper.py --verify-only
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db import db                                        # noqa: E402
from src.engine.forward_paper import forward_paper_ledger as LEDGER  # noqa: E402

FEED = Path(__file__).resolve().parent.parent / "data" / "feeds" / "forward_paper.json"


def latest_bar_date() -> str:
    row = db.execute_query("SELECT MAX(trading_date) AS d FROM bar_1d;")
    return str(row[0]["d"])


def publish_feed() -> dict:
    """Publish the ledger status, plus what gate 8 currently makes of it.

    The feed states plainly that the record is self-attested. A gate-8 pass computed from
    this file means the process was followed, not that an edge has been demonstrated.
    """
    from datetime import datetime, timezone
    status = LEDGER.status()
    integrity = status["integrity"]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_of_truth": "data/forward_paper_log.jsonl, appended by "
                           "scripts/run_forward_paper.py after each close. Read back and "
                           "verified by src/engine/forward_paper.py; nothing here is "
                           "hard-coded.",
        "log_exists": status["exists"],
        "records": status["records"],
        "per_strategy": status["per_strategy"],
        "chain_verifies": integrity["ok"],
        "chain_violations": integrity["violations"],
        "integrity_statement": status["integrity_statement"],
        "what_this_is": (
            "A forward paper record: the strategy's decisions are written down after each "
            "close, before the next open is known, and are never revised. It is the only "
            "evidence in this lab that cannot be retro-fitted to a favourable outcome."
        ),
        "what_this_is_not": (
            "It is not proof of an edge, and it is not tamper-proof. The operator controls "
            "the file, the git history and the clock. Until each run's hash is anchored to "
            "something outside the operator's control, every number here is self-attested."
        ),
    }
    FEED.parent.mkdir(parents=True, exist_ok=True)
    json.dump(payload, open(FEED, "w"), indent=2)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="as-of trading date (default: newest bar in bar_1d)")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    if args.verify_only:
        v = LEDGER.verify()
        print(f"chain verifies: {v['ok']}  ({v['records']} records)")
        for x in v["violations"]:
            print(f"  VIOLATION: {x}")
        print(f"\n{v['integrity_statement']}")
        return 0 if v["ok"] else 1

    as_of = args.date or latest_bar_date()
    print(f"forward paper session for {as_of}")

    print("  settling pending decisions ...")
    st = LEDGER.settle(as_of)
    print(f"    fills {st['fills']} (entered {st['entered']}, skipped {st['skipped']}), "
          f"closes {st['closes']}")

    print("  capturing today's decisions ...")
    cap = LEDGER.capture(as_of)
    for sid, s in cap["strategies"].items():
        if "skipped" in s:
            print(f"    {sid:24} {s['skipped']}")
        else:
            print(f"    {sid:24} evaluated {s['securities_evaluated']:4}  fired {s['signals_fired']}")
    print(f"    records written: {cap['records_written']}")

    v = LEDGER.verify()
    print(f"  chain verifies: {v['ok']}  ({v['records']} records)")
    for x in v["violations"]:
        print(f"    VIOLATION: {x}")

    if not args.no_publish:
        publish_feed()
        print(f"  published {FEED.name}")

    s = LEDGER.status()["per_strategy"]
    print("\n  gate-8 readiness:")
    for sid, r in s.items():
        need = max(0, 3 - r["months_covered"])
        print(f"    {sid:24} {r['months_covered']}/3 months, {r['closed_trades']} closed, "
              f"mean R {r['forward_mean_r']} "
              f"{'-- READY TO EVALUATE' if need == 0 else f'-- {need} more month(s)'}")

    if not v["ok"]:
        print("\nCHAIN INTEGRITY FAILED -- do not treat this record as evidence.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
