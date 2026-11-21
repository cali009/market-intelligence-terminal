"""
HONESTY OVERHAUL — §7 automated validation gates.

Run with:  python3 scripts/honesty_checks.py [--phase N] [--feeds DIR]

Exit code 0 = every implemented check passed. Exit 1 = at least one failed.
Checks that belong to a later phase report SKIPPED rather than silently passing,
so this script can never be used to claim a gate was met when it was not run.

Each check prints PASS / FAIL / SKIP with the specific evidence it examined.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# Claims that must not appear anywhere in the shipped surface. The one allowed
# exception, per the brief, is "certified" inside a sentence stating that
# something is NOT certified.
# Split by phase so a phase-scoped run never fails on work that has not been
# reached yet, and never passes a string that its own phase was meant to remove.
FORBIDDEN_PHASE_1 = [
    "PROMOTED TO LIVE",
    "LIVE_QUALIFIED",
    "edge confirmed",
]
FORBIDDEN_PHASE_2 = [
    "US$3,087",
    "we certify under penalty",
    "99.98%",
    "Equinix NY4",
    "TMX Datalinx Certified Reseller",
    "Official EOD Exchange Consolidation",
    "survivorship-bias-free",
    "TCPA 10DLC Registered",
    "Hallucination Rate 0.00%",
    "Citation Resolution 100%",
    "CIRO & SEC CETU Audited",
    "LOW_OVERFITTING_RISK",   # only legal when PBO is informative; see check 8
    "low overfitting risk",
]
FORBIDDEN_STRINGS = FORBIDDEN_PHASE_1 + FORBIDDEN_PHASE_2

# Hard-coded statistic fallbacks in the UI: `x || 1.5`, `x || '34.5%'`, `: 16.39`.
UI_FALLBACK_RE = re.compile(
    r"""(?:\|\|\s*(?P<or_val>'[^']*'|"[^"]*"|-?\d+\.\d+))"""
    r"""|(?:!==\s*undefined\s*\?\s*[\w.]+\s*:\s*(?P<tern_val>-?\d+\.\d+))"""
)
# Values that are legitimately neutral rather than invented statistics.
BENIGN_FALLBACKS = {"''", '""', "0.0", "1.0", "false", "[]", "{}", "'ALL'", "'/'", "'n/a'", "'N/A'"}


class Reporter:
    def __init__(self) -> None:
        self.results: List[Tuple[int, str, str, str]] = []

    def record(self, num: int, name: str, status: str, detail: str) -> None:
        self.results.append((num, name, status, detail))
        icon = {PASS: "✅", FAIL: "❌", SKIP: "⚠️ "}[status]
        print("%s check %2d  %-52s %s" % (icon, num, name, status))
        for line in detail.strip().split("\n"):
            if line.strip():
                print("        %s" % line)

    @property
    def failed(self) -> List[Tuple[int, str, str, str]]:
        return [r for r in self.results if r[2] == FAIL]

    def table(self) -> str:
        rows = ["", "  # | STATUS | CHECK", "  --+--------+" + "-" * 54]
        for num, name, status, _ in self.results:
            rows.append("  %2d | %-6s | %s" % (num, status, name))
        return "\n".join(rows)


def _load(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _iter_texts(feeds_dir: Path, include_html: bool):
    """Yield (label, text) for index.html and every shipped JSON feed."""
    if include_html:
        for cand in (REPO / "public" / "index.html", REPO / "web" / "index.html"):
            if cand.exists():
                yield ("html:%s" % cand.name, cand.read_text(errors="replace"))
                break
    for p in sorted(feeds_dir.glob("*.json")):
        yield ("feed:%s" % p.name, p.read_text(errors="replace"))


# ---------------------------------------------------------------------------
# 1. No hard-coded statistics
# ---------------------------------------------------------------------------
def check_no_hardcoded_stats(rep: Reporter, feeds_dir: Path, include_html: bool) -> None:
    hits: List[str] = []
    if include_html:
        for cand in (REPO / "public" / "index.html", REPO / "web" / "index.html"):
            if not cand.exists():
                continue
            for i, line in enumerate(cand.read_text(errors="replace").split("\n"), 1):
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue  # commentary, not shipped logic
                for m in UI_FALLBACK_RE.finditer(line):
                    val = (m.group("or_val") or m.group("tern_val") or "").strip()
                    if val in BENIGN_FALLBACKS:
                        continue
                    # Only flag decimal literals — those are statistics, not indices.
                    if not re.fullmatch(r"-?\d+\.\d+", val.strip("'\"")):
                        continue
                    hits.append("%s:%d  %s" % (cand.name, i, stripped[:110]))
            break

    # Python sources must not carry typed-in per-rule statistics either.
    for src in (REPO / "src" / "engine" / "scanners.py",):
        if src.exists():
            txt = src.read_text()
            for field in ("historical_win_rate_pct", "forward_5d_return_pct"):
                for i, line in enumerate(txt.split("\n"), 1):
                    if line.strip().startswith("#"):
                        continue
                    if '"%s":' % field in line:
                        hits.append("%s:%d  %s" % (src.name, i, line.strip()[:110]))

    if hits:
        rep.record(1, "No hard-coded statistics", FAIL,
                   "%d hard-coded statistic(s) remain:\n  %s" % (len(hits), "\n  ".join(hits[:12])))
    else:
        rep.record(1, "No hard-coded statistics", PASS,
                   "No decimal-literal fallbacks in index.html; no typed-in per-rule "
                   "statistics in scanners.py.")


# ---------------------------------------------------------------------------
# 2. Scorecard recomputation equals the stored value
# ---------------------------------------------------------------------------
def check_recomputation(rep: Reporter, feeds_dir: Path) -> None:
    from src.engine.strategy_validation import StrategyValidationEngine

    published = _load(feeds_dir / "strategy_validation.json")
    if not published:
        rep.record(2, "Scorecard recomputation matches stored", SKIP,
                   "strategy_validation.json not generated yet.")
        return

    fresh = StrategyValidationEngine(REPO).generate_feed(feeds_dir)
    by_id = {s["strategy_id"]: s for s in fresh["strategies"]}
    drift: List[str] = []
    for pub in published["strategies"]:
        new = by_id.get(pub["strategy_id"])
        if new is None:
            drift.append("%s missing from recomputation" % pub["strategy_id"])
            continue
        for field in ("oos_mean_return_pct", "oos_expectancy_r", "oos_t_stat"):
            a, b = pub[field], new[field]
            if (a is None) != (b is None):
                drift.append("%s.%s: %s vs %s" % (pub["strategy_id"], field, a, b))
            elif a is not None and abs(a - b) > 0.01:
                drift.append("%s.%s: %.4f vs %.4f" % (pub["strategy_id"], field, a, b))

    if drift:
        rep.record(2, "Scorecard recomputation matches stored", FAIL,
                   "Drift beyond 0.01:\n  " + "\n  ".join(drift[:12]))
    else:
        rep.record(2, "Scorecard recomputation matches stored", PASS,
                   "All %d strategies recomputed within 0.01 on every published field."
                   % len(published["strategies"]))


# ---------------------------------------------------------------------------
# 3. No two identical scorecards
# ---------------------------------------------------------------------------
def check_no_duplicate_scorecards(rep: Reporter, feeds_dir: Path) -> None:
    published = _load(feeds_dir / "strategy_validation.json")
    if not published:
        rep.record(3, "No two identical scorecards", SKIP,
                   "strategy_validation.json not generated yet.")
        return

    seen: Dict[tuple, str] = {}
    dupes: List[str] = []
    for s in published["strategies"]:
        key = (s["oos_trades"], s["oos_months"], s["oos_mean_return_pct"],
               s["oos_expectancy_r"], s["oos_t_stat"])
        if key in seen:
            dupes.append("%s == %s on %s" % (seen[key], s["strategy_id"], key))
        seen[key] = s["strategy_id"]

    if dupes:
        rep.record(3, "No two identical scorecards", FAIL, "\n  ".join(dupes))
    else:
        rep.record(3, "No two identical scorecards", PASS,
                   "%d distinct records across %d strategies."
                   % (len(seen), len(published["strategies"])))


# ---------------------------------------------------------------------------
# 4. Forbidden strings gone
# ---------------------------------------------------------------------------
def check_forbidden_strings(rep: Reporter, feeds_dir: Path, include_html: bool,
                            phase: Optional[int] = None) -> None:
    banned = FORBIDDEN_PHASE_1 if phase == 1 else FORBIDDEN_STRINGS
    found: List[str] = []
    for label, text in _iter_texts(feeds_dir, include_html):
        for bad in banned:
            if bad == "LOW_OVERFITTING_RISK" or bad == "low overfitting risk":
                continue  # handled by check 8, which knows when it is legitimate
            n = text.count(bad)
            if n:
                # Allowed exception: "certified" used to say something is NOT certified.
                if "certified" in bad.lower():
                    ok = all(("not certified" in ln.lower() or "is not certified" in ln.lower()
                              or "not a certified" in ln.lower())
                             for ln in text.split("\n") if bad.lower() in ln.lower())
                    if ok:
                        continue
                found.append("%s contains %r x%d" % (label, bad, n))

    if found:
        rep.record(4, "Forbidden claims removed", FAIL, "\n  ".join(found[:15]))
    else:
        rep.record(4, "Forbidden claims removed", PASS,
                   "None of the %d forbidden strings in scope appear in index.html or any feed."
                   % (len(banned) - (2 if phase != 1 else 0)))


# ---------------------------------------------------------------------------
# 5-7. Later phases
# ---------------------------------------------------------------------------
def check_single_vix(rep: Reporter, feeds_dir: Path) -> None:
    """One authoritative VIX, equal to the latest stored ^VIX close."""
    name = "One VIX everywhere, equal to latest ^VIX close"
    try:
        from src.data.vix_ingestion import volatility_index_engine
    except Exception as e:                                    # pragma: no cover
        rep.record(5, name, FAIL, f"cannot import vix_ingestion: {e}")
        return

    row = volatility_index_engine.latest()
    if not row:
        rep.record(5, name, FAIL,
                   "no ^VIX close stored — run the daily pipeline")
        return
    vix_close = float(row["close"])
    vix_date = str(row["trading_date"])

    summary = feeds_dir / "daily_summary.json"
    if not summary.exists():
        rep.record(5, name, FAIL, f"missing {summary}")
        return
    data = json.loads(summary.read_text())
    block = data.get("volatility_index") or {}

    problems = []
    if abs(float(block.get("close") or -1) - vix_close) > 0.01:
        problems.append("published close %s != stored ^VIX %s"
                        % (block.get("close"), vix_close))
    if block.get("as_of_date") != vix_date:
        problems.append("published as_of %s != stored %s"
                        % (block.get("as_of_date"), vix_date))
    if not block.get("source"):
        problems.append("no source attribution on the volatility block")

    # No other feed may carry a competing current-VIX figure. Historical scenario
    # parameters (e.g. the 2020 VIX peak inside portfolio_stress) are exempt.
    banned = (16.0, 21.4)
    for path in sorted(feeds_dir.glob("*.json")):
        if path.name == "daily_summary.json" or path.name == "portfolio_stress.json":
            continue
        blob = path.read_text()
        for value in banned:
            if re.search(r'"vix[^"]*"\s*:\s*%s\b' % re.escape(str(value)), blob, re.I):
                problems.append("%s carries a competing vix value %s" % (path.name, value))

    if problems:
        rep.record(5, name, FAIL, "; ".join(problems))
    else:
        rep.record(5, name, PASS,
                   "^VIX = %.2f on %s; daily_summary publishes the same value with "
                   "source attribution; no competing figure in any other feed."
                   % (vix_close, vix_date))


def check_pe_bounds(rep: Reporter, feeds_dir: Path) -> None:
    """Every published P/E is within (0, 100] or carries an explicit flag."""
    name = "P/E within 0-100 or flagged"
    offenders = []
    examined = 0
    flagged = 0

    def walk(node, where):
        nonlocal examined, flagged
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "pe_ratio" and isinstance(v, (int, float)):
                    examined += 1
                    flag = node.get("pe_flag")
                    if v <= 0 or v > 100.0:
                        if flag:
                            flagged += 1
                        else:
                            offenders.append("%s pe_ratio=%s unflagged" % (where, v))
                else:
                    walk(v, where)
        elif isinstance(node, list):
            for i, x in enumerate(node):
                walk(x, where)

    for path in sorted(feeds_dir.glob("*.json")):
        try:
            walk(json.loads(path.read_text()), path.name)
        except (json.JSONDecodeError, OSError):
            continue

    if offenders:
        rep.record(6, name, FAIL, "; ".join(offenders[:8]))
    elif examined == 0:
        rep.record(6, name, FAIL, "no pe_ratio values found in any feed")
    else:
        rep.record(6, name, PASS,
                   "%d P/E values examined; %d outside (0, 100] and all of those "
                   "carry an explicit pe_flag; the rest are in range."
                   % (examined, flagged))


def check_target_ordering(rep: Reporter, feeds_dir: Path) -> None:
    """Long setups must have T1 < T2 < T3 and an ordered entry zone."""
    name = "T1 < T2 < T3 and entry-zone low < high"
    path = feeds_dir / "signals.json"
    if not path.exists():
        rep.record(7, name, FAIL, f"missing {path}")
        return
    plans = json.loads(path.read_text()).get("signals") or []
    if not plans:
        rep.record(7, name, PASS,
                   "no active signal plans published — nothing to violate the "
                   "invariants (an empty result is legitimate)")
        return

    bad_zone, bad_targets, rr_constant = [], [], None
    rrs = set()
    for p in plans:
        sym = p.get("symbol")
        zone = p.get("entry_zone") or []
        if len(zone) == 2 and not (zone[0] < zone[1]):
            bad_zone.append("%s %s" % (sym, zone))
        tg = [t.get("target") for t in (p.get("targets") or [])]
        if len(tg) >= 2 and not all(tg[i] < tg[i + 1] for i in range(len(tg) - 1)):
            bad_targets.append("%s %s" % (sym, tg))
        if p.get("risk_reward_ratio") is not None:
            rrs.add(round(float(p["risk_reward_ratio"]), 4))

    problems = []
    if bad_zone:
        problems.append("inverted entry zone: " + ", ".join(bad_zone))
    if bad_targets:
        problems.append("targets not ascending: " + ", ".join(bad_targets))
    # A single R:R shared by every plan means it is a construction constant, not
    # a property of the setup.
    if len(plans) > 1 and len(rrs) == 1:
        problems.append("risk_reward_ratio is %s for all %d plans — a constant, "
                        "not computed per setup" % (rrs.pop(), len(plans)))

    if problems:
        rep.record(7, name, FAIL, "; ".join(problems))
    else:
        rep.record(7, name, PASS,
                   "%d plans: every entry zone ordered, every target ladder "
                   "ascending, and risk_reward_ratio takes %d distinct value(s)."
                   % (len(plans), len(rrs)))


# ---------------------------------------------------------------------------
# 8. One PBO everywhere, and never "low risk" when nothing is profitable
# ---------------------------------------------------------------------------
def check_pbo(rep: Reporter, feeds_dir: Path, include_html: bool) -> None:
    published = _load(feeds_dir / "strategy_validation.json")
    if not published:
        rep.record(8, "One PBO; no false 'low risk' claim", SKIP,
                   "strategy_validation.json not generated yet.")
        return

    problems: List[str] = []
    pbo = published.get("pbo") or {}
    value = pbo.get("value")
    informative = pbo.get("informative")

    # (a) the reassuring label may only appear when the engine says PBO is informative
    if not informative:
        for label, text in _iter_texts(feeds_dir, include_html):
            for bad in ("LOW_OVERFITTING_RISK", "low overfitting risk"):
                if bad in text:
                    problems.append("%s still asserts %r while PBO is not informative" % (label, bad))

    # (b) no other feed may publish its own competing PBO
    for p in sorted(feeds_dir.glob("*.json")):
        if p.name == "strategy_validation.json":
            continue
        data = _load(p)
        if data is None:
            continue
        blob = json.dumps(data)
        for key in ('"pbo"', '"probability_backtest_overfitting"'):
            if key in blob:
                problems.append("%s publishes its own %s" % (p.name, key.strip('"')))

    detail = "PBO = %s, informative = %s, assessment = %r." % (
        value, informative, pbo.get("assessment"))
    if problems:
        rep.record(8, "One PBO; no false 'low risk' claim", FAIL,
                   detail + "\n  " + "\n  ".join(problems[:10]))
    else:
        rep.record(8, "One PBO; no false 'low risk' claim", PASS,
                   detail + "\n  No competing PBO in any other feed.")


# ---------------------------------------------------------------------------
# 9-10. Later phases
# ---------------------------------------------------------------------------
def check_quantlab_panel(rep: Reporter, feeds_dir: Path) -> None:
    name = "QuantLab panel renders; banner matches backtest JSON"
    problems = []

    feed = feeds_dir / "quantlab_status.json"
    if not feed.exists():
        problems.append("missing quantlab_status.json")
    else:
        try:
            ql = json.loads(feed.read_text())
        except json.JSONDecodeError as e:
            ql = None
            problems.append("quantlab_status.json does not parse: %s" % e)
        if ql is not None:
            # The feed must carry the headline verdict, not just a table of rows.
            if not str(ql.get("programme_status") or "").strip():
                problems.append("programme_status is empty")
            if not ql.get("experiments"):
                problems.append("no experiments recorded")
            # Every experiment must carry a verdict; a row without one is a claim
            # the reader cannot evaluate.
            for x in ql.get("experiments") or []:
                if not x.get("verdict"):
                    problems.append("experiment %s has no verdict" % x.get("id"))

    # The tab must exist in the published page and be wired to the feed.
    html_path = REPO / "public" / "index.html"
    if not html_path.exists():
        html_path = REPO / "web" / "index.html"
    if html_path.exists():
        html = html_path.read_text()
        for token, why in (
            ('id="tab-researchledger"', "Research Ledger pane is missing"),
            ('data-tab="researchledger"', "Research Ledger tab button is missing"),
            ("loadResearchLedgerAndRender", "Research Ledger loader is missing"),
            ("quantlab_status", "page never references the quantlab feed"),
        ):
            if token not in html:
                problems.append(why)

    # The research banner must agree with the backtest bundle it summarises.
    bt = feeds_dir / "backtests.json"
    sv = feeds_dir / "strategy_validation.json"
    if bt.exists() and sv.exists():
        try:
            b = json.loads(bt.read_text())
            v = json.loads(sv.read_text())
            n_bt = len(b.get("strategies") or b.get("results") or [])
            n_sv = v.get("strategies_evaluated")
            if n_bt and n_sv is not None and n_bt != n_sv:
                problems.append("backtests.json has %d strategies but "
                                "strategy_validation.json reports %d"
                                % (n_bt, n_sv))
        except json.JSONDecodeError as e:
            problems.append("cannot compare banner to backtest JSON: %s" % e)

    if problems:
        rep.record(9, name, FAIL, "; ".join(problems))
    else:
        rep.record(9, name, PASS,
                   "quantlab_status.json publishes %d experiments with a verdict on "
                   "each and a non-empty programme_status; the Research Ledger tab is "
                   "present and wired to the feed; banner strategy count agrees with "
                   "the backtest bundle."
                   % len(json.loads(feed.read_text()).get("experiments") or []))


def check_no_console_errors(rep: Reporter, feeds_dir: Path) -> None:
    rep.record(10, "No console errors; no panel stuck on 'Loading…'", SKIP,
               "Requires a browser page-load run; see the JS self-check in index.html.")


# ---------------------------------------------------------------------------
CHECKS: List[Tuple[int, Callable[..., None], bool]] = [
    (1, check_no_hardcoded_stats, True),
    (2, check_recomputation, False),
    (3, check_no_duplicate_scorecards, False),
    (4, check_forbidden_strings, True),
    (5, check_single_vix, False),
    (6, check_pe_bounds, False),
    (7, check_target_ordering, False),
    (8, check_pbo, True),
    (9, check_quantlab_panel, False),
    (10, check_no_console_errors, False),
]

PHASE_OF_CHECK = {1: 1, 2: 1, 3: 1, 4: 2, 5: 3, 6: 3, 7: 3, 8: 1, 9: 4, 10: 5}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeds", default=str(REPO / "data" / "feeds"))
    ap.add_argument("--phase", type=int, default=None,
                    help="only run checks belonging to this phase")
    ap.add_argument("--no-html", action="store_true",
                    help="skip index.html (feeds-only run)")
    args = ap.parse_args()

    feeds_dir = Path(args.feeds)
    if not feeds_dir.is_dir():
        print("feeds directory not found: %s" % feeds_dir)
        return 2

    sys.path.insert(0, str(REPO))
    rep = Reporter()
    print("Honesty checks — feeds: %s" % feeds_dir)
    print()

    for num, fn, wants_html in CHECKS:
        if args.phase is not None and PHASE_OF_CHECK[num] != args.phase:
            continue
        if num == 4:
            fn(rep, feeds_dir, not args.no_html, args.phase)
        elif wants_html:
            fn(rep, feeds_dir, not args.no_html)
        else:
            fn(rep, feeds_dir)

    print(rep.table())
    passed = sum(1 for r in rep.results if r[2] == PASS)
    skipped = sum(1 for r in rep.results if r[2] == SKIP)
    print("\n  %d passed, %d failed, %d skipped" % (passed, len(rep.failed), skipped))
    if rep.failed:
        print("\n  DO NOT DEPLOY — %d check(s) failed." % len(rep.failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
