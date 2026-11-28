"""
Forward paper trading ledger.

This module exists to satisfy gate 8 of the validation suite: ">= 3 months of genuine
timestamped forward paper record, degrading <= 40% vs backtest". No such record had ever
existed, so gate 8 failed for every strategy automatically. A backtest, however careful, is
still a model of the past. A forward record is the only thing in this lab that cannot be
retro-fitted, which is what makes it worth more than any backtest here.


WHAT THIS IS NOT -- READ BEFORE TRUSTING A RESULT FROM IT
---------------------------------------------------------
Three independent model reviews of this design converged on the same conclusion, and they
are right: **the integrity mechanism here is tamper-EVIDENT, not tamper-PROOF.**

The realistic threat model is that the person running this is also the person who benefits
from a flattering result, and that person controls the file, the git history and the clock.
A hash chain cannot defend against someone willing to rewrite the whole file and recompute
every hash. It defends against the far more likely failure: quietly fixing one line three
weeks later and forgetting that it is now a different record.

So this ledger is a discipline device, not a proof. It makes self-deception effortful and
detectable rather than impossible. Anyone reading a gate-8 PASS produced from this file
should understand that the PASS means "the process was followed", not "the edge is real".

The only thing that would upgrade this to evidence is an anchor outside the operator's
control: a per-run hash pushed to a remote whose commit timestamps are recorded by a third
party, or an RFC 3161 timestamp. Until that exists, treat every number here as self-attested.


DESIGN DECISIONS, AND THE REVIEW THAT FORCED THEM
-------------------------------------------------
1. Every DECISION gets exactly one FILL, even when nothing was bought.
   A review pointed out the obvious cheat: write ten DECISIONs, peek at the opens, then
   write FILLs only for the five that gapped favourably and let the rest "not trigger".
   Absence of a FILL is therefore itself an integrity violation, and verify() reports it.

2. A CENSUS record is written for every strategy on every run, including no-signal days.
   Without it you could backfill only the strategies that happened to work. A CENSUS row
   proves the strategy ran, saw N securities and chose not to trade.

3. The DECISION records the stop/target RULE and the ATR, never a stop price.
   The stop is anchored to the entry price, and the entry is the NEXT session's open, which
   is unknown when the decision is made. Recording a price would mean inventing one.

4. strategy_version_hash is captured on every record.
   Quietly retuning a strategy mid-stream and continuing to log under the same id would
   blend two different strategies into one flattering record. verify() reports version
   drift so the log can be segmented instead of blended.

5. The signal predicate is imported from the backtester, not re-implemented.
   A forward log that fires on slightly different conditions than the backtest it is
   compared against is not a comparison. Both paths call the same function.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.data.db import db                                              # noqa: E402
from src.engine.technicals import TechnicalAnalysisEngine                 # noqa: E402
from src.engine.backtester import BacktestConfig, backtest_engine          # noqa: E402

LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "forward_paper_log.jsonl"

# Strategies tracked forward. The sleeve is tracked because gate 8 evaluates per
# strategy_id and the sleeve is the record whose gate-8 failure motivated this module.
TRACKED_STRATEGIES = ["ADAPTIVE_DUAL_REGIME", "PO3_META_GATED", "PO3_LIQUIDITY_SWEEP"]

# Exits replicated here. The backtester additionally applies a conformal trailing stop
# after a partial trim, a breakeven stop, and a half-position trim ladder at target_1.
# Those are NOT replicated, so forward r-multiples are not perfectly comparable to
# backtest r-multiples. Every CLOSE record names this so the comparison cannot be
# mistaken for like-for-like.
EXIT_RULES_VERSION = "FORWARD_EXIT_RULES_V1"
MAX_HOLDING_DAYS = 20
TARGET_1_MIN_HOLDING_DAYS = 6

INTEGRITY_STATEMENT = (
    "This forward record is tamper-EVIDENT, not tamper-PROOF. The hash chain detects "
    "edits to individual records but cannot prevent the operator from rewriting the whole "
    "file, and the timestamps come from a clock the operator controls. A gate-8 pass "
    "computed from this file means the process was followed, not that the edge is real. "
    "No third-party anchor (RFC 3161 timestamp or externally-recorded commit) exists yet."
)

# PO3 strategies share entry gates that live in the backtester's loop rather than in the
# predicate. They are replicated here so the forward path fires on the same conditions.
PO3_STRATEGIES = {"PO3_LIQUIDITY_SWEEP", "PO3_META_GATED"}
_PO3_EXCLUDED_SYMBOLS = {"SPY", "QQQ", "XIU"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(rec: Dict[str, Any]) -> str:
    """Stable serialisation for hashing: sorted keys, no hash fields."""
    return json.dumps(
        {k: v for k, v in rec.items() if k != "record_hash"},
        sort_keys=True, separators=(",", ":"), default=str,
    )


def _hash(rec: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(rec).encode("utf-8")).hexdigest()


def strategy_version_hash(strategy_id: str) -> str:
    """Fingerprint the strategy definition, so silent retuning shows up in the log.

    Covers the signal predicate source and the cost/sizing/risk configuration the
    backtester would use. It will not catch a change inside compute_all_features, which is
    a real limitation, so it is a drift DETECTOR rather than a guarantee.
    """
    try:
        src = inspect.getsource(type(backtest_engine)._evaluate_signal_predicate)
    except (OSError, TypeError):
        src = ""
    cfg = BacktestConfig()
    parts = [
        strategy_id, src,
        f"commission_bps={cfg.commission_bps}", f"slippage_bps={cfg.slippage_bps}",
        f"risk_per_trade_pct={cfg.risk_per_trade_pct}",
        f"exit_rules={EXIT_RULES_VERSION}", f"max_holding_days={MAX_HOLDING_DAYS}",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


class ForwardPaperLedger:
    """Append-only, hash-chained forward paper log."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else LOG_PATH

    # -- integrity ---------------------------------------------------------
    def read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        out.append({"_unparseable": True, "_raw": line})
        return out

    def _last_hash(self) -> Optional[str]:
        recs = self.read()
        return recs[-1].get("record_hash") if recs else None

    def append(self, records: List[Dict[str, Any]]) -> int:
        """Append records, extending the hash chain. Never rewrites existing lines."""
        if not records:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prev = self._last_hash()
        with open(self.path, "a") as f:
            for rec in records:
                rec = dict(rec)
                rec.setdefault("recorded_at", _now())
                rec["prev_hash"] = prev
                rec["record_hash"] = _hash(rec)
                f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
                prev = rec["record_hash"]
        return len(records)

    def verify(self) -> Dict[str, Any]:
        """Check the chain. Returns the first integrity violation found, if any."""
        recs = self.read()
        if not recs:
            return {"ok": True, "records": 0, "violations": [],
                    "note": "Log is empty.",
                    "integrity_statement": INTEGRITY_STATEMENT}

        violations: List[str] = []
        prev: Optional[str] = None
        for i, rec in enumerate(recs, start=1):
            if rec.get("_unparseable"):
                violations.append(f"line {i}: not valid JSON")
                prev = None
                continue
            if rec.get("prev_hash") != prev:
                violations.append(
                    f"line {i}: prev_hash mismatch (chain broken here; either this line "
                    f"or an earlier one was edited or removed)")
            computed = _hash(rec)
            stored = rec.get("record_hash")
            if stored and stored != computed:
                violations.append(f"line {i}: record_hash does not match its own contents")
            if not rec.get("recorded_at"):
                violations.append(f"line {i}: missing recorded_at (not timestamped)")
            # Chain on the COMPUTED hash, not the stored one. Otherwise someone could
            # edit a line's contents, leave its stale record_hash in place, and every
            # downstream prev_hash would still line up -- the edit would be invisible to
            # the chain and caught only by the self-hash check. Deriving the link from
            # the contents makes any edit propagate a break all the way down.
            prev = computed

        # Every DECISION must have been settled by a FILL. An unsettled DECISION is the
        # signature of peeking at the open and then dropping the trades you dislike.
        # A DECISION dated on the newest as_of_date in the log cannot have been filled
        # yet -- the fill needs the NEXT session's open, which has not printed. Flagging
        # those would be a false positive, so only decisions for which a later session
        # demonstrably exists in this log are treated as unsettled.
        decisions = {r.get("decision_hash"): r for r in recs
                     if r.get("record_type") == "DECISION"}
        filled = {r.get("decision_hash") for r in recs if r.get("record_type") == "FILL"}
        newest = max((r.get("as_of_date") or "" for r in recs), default="")
        dangling = [d for d, r in decisions.items()
                    if d not in filled and (r.get("as_of_date") or "") < newest]
        if dangling:
            violations.append(
                f"{len(dangling)} DECISION record(s) older than the newest logged session "
                f"have no FILL: {', '.join(str(d)[:12] for d in dangling[:5])}. A later "
                f"session exists, so these should have been settled.")

        # Parameter drift mid-stream, per strategy.
        versions: Dict[str, set] = {}
        for r in recs:
            sid, vh = r.get("strategy_id"), r.get("strategy_version_hash")
            if sid and vh:
                versions.setdefault(sid, set()).add(vh)
        drift = {k: sorted(v) for k, v in versions.items() if len(v) > 1}
        if drift:
            violations.append(
                f"strategy_version_hash changed mid-stream for: "
                + ", ".join(f"{k} ({len(v)} versions)" for k, v in drift.items())
                + ". The record blends different strategy definitions and must be segmented.")

        return {
            "ok": not violations,
            "records": len(recs),
            "violations": violations,
            "integrity_statement": INTEGRITY_STATEMENT,
        }

    # -- capture -----------------------------------------------------------
    def capture(self, as_of_date: str, capital_usd: float = 500.0) -> Dict[str, Any]:
        """Record today's decisions, before the next open is known.

        Run after the close of `as_of_date`. The signal is evaluated on that day's bar;
        the entry will be the NEXT session's open, which this function cannot see and
        therefore does not record.
        """
        securities = db.execute_query(
            "SELECT security_id, symbol, exchange, country, currency FROM security "
            "WHERE is_active = 1;")
        if not securities:
            raise RuntimeError("no active securities")

        frames: Dict[str, pd.DataFrame] = {}
        for s in securities:
            rows = db.execute_query(
                "SELECT security_id, trading_date, open, high, low, close, volume "
                "FROM bar_1d WHERE security_id = ? ORDER BY trading_date ASC;",
                (s["security_id"],))
            if not rows:
                continue
            df = pd.DataFrame(rows)
            df = TechnicalAnalysisEngine.compute_all_features(df)
            frames[s["symbol"]] = df

        existing = self.read()
        already = {(r.get("strategy_id"), r.get("as_of_date"))
                   for r in existing if r.get("record_type") == "CENSUS"}

        fee_rate = (BacktestConfig().commission_bps + BacktestConfig().slippage_bps) / 10000.0
        records: List[Dict[str, Any]] = []
        summary: Dict[str, Any] = {"as_of_date": as_of_date, "strategies": {}}

        for sid in TRACKED_STRATEGIES:
            if (sid, as_of_date) in already:
                summary["strategies"][sid] = {"skipped": "already captured for this date"}
                continue

            vh = strategy_version_hash(sid)
            evaluated = fired = 0
            is_po3 = sid in PO3_STRATEGIES

            for sym, df in frames.items():
                if is_po3 and sym in _PO3_EXCLUDED_SYMBOLS:
                    continue
                bar = df[df["trading_date"] == as_of_date]
                if bar.empty:
                    continue
                evaluated += 1
                b = bar.iloc[-1]

                # Called on the real engine instance: the ADAPTIVE predicate recurses
                # through self into PO3_LIQUIDITY_SWEEP, so a bare unbound call would
                # pass the strategy id as self and silently evaluate nothing.
                if not backtest_engine._evaluate_signal_predicate(sid, b):
                    continue
                # Entry gates the backtester applies in its loop, not in the predicate.
                if is_po3:
                    if (b.get("atr_pct") or 0.0) > 3.5:
                        continue
                    c_val = float(b["close"])
                    for ma, lim in (("sma_200", 20.0), ("sma_50", 8.0)):
                        v = b.get(ma)
                        if v and not math.isnan(v) and v > 0 and ((c_val / v) - 1.0) * 100.0 > lim:
                            break
                    else:
                        cmf = b.get("cmf_20")
                        if cmf is not None and not math.isnan(cmf) and cmf < -0.05:
                            continue
                        fired += 1
                        atr = b.get("atr_14")
                        atr = float(atr) if atr and not math.isnan(atr) and atr > 0 else float(c_val) * 0.02
                        records.append(self._decision(sid, vh, as_of_date, sym, atr,
                                                      float(b["low"]), capital_usd, fee_rate))
                        continue
                    continue
                fired += 1
                atr = b.get("atr_14")
                atr = float(atr) if atr and not math.isnan(atr) and atr > 0 else float(b["close"]) * 0.02
                records.append(self._decision(sid, vh, as_of_date, sym, atr,
                                              float(b["low"]), capital_usd, fee_rate))

            records.append({
                "record_type": "CENSUS", "strategy_id": sid, "strategy_version_hash": vh,
                "as_of_date": as_of_date, "securities_evaluated": evaluated,
                "signals_fired": fired,
                "note": "Written whether or not anything fired, so a strategy that never "
                        "trades cannot be quietly omitted from the record.",
            })
            summary["strategies"][sid] = {"securities_evaluated": evaluated,
                                          "signals_fired": fired}

        written = self.append(records)
        summary["records_written"] = written
        return summary

    @staticmethod
    def _decision(sid, vh, as_of_date, sym, atr, sweep_low, capital_usd, fee_rate):
        """A DECISION records the RULE that will produce stop and target, not a price.

        The stop is anchored to the entry price, and the entry is next session's open. It
        is unknowable now, so recording a stop price here would mean inventing one.
        """
        if sid == "PO3_LIQUIDITY_SWEEP":
            stop_rule, targets = "raw_entry - max(0.60*atr, (raw_entry-sweep_low)+0.40*atr)", (1.8, 3.0, 4.5)
        elif sid == "ADAPTIVE_DUAL_REGIME":
            stop_rule, targets = "raw_entry - 1.4*atr", (1.6, 2.6, 4.0)
        else:
            stop_rule, targets = "raw_entry - 1.5*atr", (1.6, 2.6, 4.0)
        body = {
            "record_type": "DECISION", "strategy_id": sid, "strategy_version_hash": vh,
            "as_of_date": as_of_date, "symbol": sym, "direction": "LONG",
            "planned_entry": "NEXT_SESSION_OPEN",
            "stop_rule": stop_rule, "target_multiples_of_risk": list(targets),
            "atr_at_signal": round(atr, 6), "sweep_low_at_signal": round(sweep_low, 6),
            "planned_risk_pct_of_capital": BacktestConfig().risk_per_trade_pct,
            "capital_usd": capital_usd, "fee_rate_per_side": fee_rate,
            "r_multiple": None,
            "note": "Recorded before the fill price was known. The entry price is absent "
                    "by design; it is supplied by the FILL record.",
        }
        body["decision_hash"] = _hash(body)[:16]
        return body

    # -- settle ------------------------------------------------------------
    def settle(self, as_of_date: str) -> Dict[str, Any]:
        """Fill pending decisions at the actual open, and close positions that exited.

        Called on a LATER date than the DECISION. Every pending DECISION gets a FILL,
        entered or not, so nothing can be silently dropped.
        """
        recs = self.read()
        decisions = [r for r in recs if r.get("record_type") == "DECISION"]
        fills = {r.get("decision_hash"): r for r in recs if r.get("record_type") == "FILL"}
        closes = {r.get("decision_hash"): r for r in recs if r.get("record_type") == "CLOSE"}
        pending = [d for d in decisions if d["decision_hash"] not in fills]

        out: List[Dict[str, Any]] = []
        stats = {"fills": 0, "entered": 0, "skipped": 0, "closes": 0}

        for d in pending:
            sym, strat, vh = d["symbol"], d["strategy_id"], d["strategy_version_hash"]
            df = self._frame(sym)
            if df is None:
                continue
            nxt = df[df["trading_date"] > d["as_of_date"]]
            if nxt.empty:
                continue  # no later session yet; legitimately still pending
            nxt = nxt.iloc[0]
            raw_entry = float(nxt["open"])
            entry_date = str(nxt["trading_date"])

            stop, risk, targets = self._price_stop_targets(strat, d, raw_entry)
            entered = risk > 0 and stop < raw_entry
            if strat == "PO3_LIQUIDITY_SWEEP" and entered and risk <= 0.3 * d["atr_at_signal"]:
                entered = False  # backtester rejects risk too tight relative to ATR

            base = {
                "record_type": "FILL", "strategy_id": strat, "strategy_version_hash": vh,
                "as_of_date": entry_date, "decision_hash": d["decision_hash"],
                "symbol": sym, "entered": bool(entered), "entry_date": entry_date,
                "raw_entry_price": round(raw_entry, 6),
                "entry_price_net": round(raw_entry * (1 + d["fee_rate_per_side"]), 6),
                "stop_loss": round(stop, 6) if entered else None,
                "target_1": round(targets[0], 6) if entered else None,
                "initial_risk_per_share": round(risk, 6) if entered else None,
                "shares": self._shares(d, raw_entry, stop) if entered else 0,
                "r_multiple": None,
                "skip_reason": None if entered else "stop/target geometry invalid at the actual open",
            }
            out.append(base)
            stats["fills"] += 1
            stats["entered" if entered else "skipped"] += 1

        # Close anything that has an entry and has since hit stop, target or time limit.
        for f in [r for r in recs if r.get("record_type") == "FILL" and r.get("entered")]:
            if f["decision_hash"] in closes or f["decision_hash"] in {o.get("decision_hash") for o in out}:
                continue
            df = self._frame(f["symbol"])
            if df is None:
                continue
            after = df[df["trading_date"] > f["entry_date"]]
            if after.empty:
                continue
            res = self._find_exit(f, after)
            if not res:
                continue
            exit_date, raw_exit, reason = res
            fee = f.get("fee_rate_per_side", 0.001)
            entry_net = f["entry_price_net"]
            exit_net = raw_exit * (1 - fee)
            gross = (raw_exit - f["raw_entry_price"]) * f["shares"]
            net = (exit_net - entry_net) * f["shares"]
            risk_amt = f["initial_risk_per_share"] * f["shares"]
            out.append({
                "record_type": "CLOSE", "strategy_id": f["strategy_id"],
                "strategy_version_hash": f["strategy_version_hash"],
                "as_of_date": exit_date, "decision_hash": f["decision_hash"],
                "symbol": f["symbol"], "exit_date": exit_date, "exit_reason": reason,
                "raw_exit_price": round(raw_exit, 6), "exit_price_net": round(exit_net, 6),
                "gross_pnl": round(gross, 4), "net_pnl": round(net, 4),
                "fee_drag": round(gross - net, 4),
                "return_pct": round((net / (entry_net * f["shares"])) * 100.0, 6),
                "r_multiple": round(net / risk_amt, 6) if risk_amt > 0 else None,
                "holding_days": self._sessions_between(df, f["entry_date"], exit_date),
                "exit_rules_version": EXIT_RULES_VERSION,
                "comparability_note": "The backtester additionally applies a conformal "
                                      "trailing stop, a breakeven stop and a half-position "
                                      "trim ladder that are not replicated here, so this "
                                      "r-multiple is not perfectly like-for-like with the "
                                      "backtest figure.",
            })
            stats["closes"] += 1

        written = self.append(out)
        stats["records_written"] = written
        return stats

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _frame(symbol: str) -> Optional[pd.DataFrame]:
        rows = db.execute_query(
            "SELECT b.trading_date, b.open, b.high, b.low, b.close, b.volume FROM bar_1d b "
            "JOIN security s ON s.security_id = b.security_id WHERE s.symbol = ? "
            "ORDER BY b.trading_date ASC;", (symbol,))
        return pd.DataFrame(rows) if rows else None

    @staticmethod
    def _price_stop_targets(strat, d, raw_entry):
        atr = d["atr_at_signal"]
        mults = d["target_multiples_of_risk"]
        if strat == "PO3_LIQUIDITY_SWEEP":
            buf = max(0.60 * atr, (raw_entry - d["sweep_low_at_signal"]) + 0.40 * atr)
            stop = raw_entry - buf
        elif strat == "ADAPTIVE_DUAL_REGIME":
            stop = raw_entry - 1.4 * atr
        else:
            stop = raw_entry - 1.5 * atr
        risk = raw_entry - stop
        targets = [raw_entry + m * risk for m in mults]
        return stop, risk, targets

    @staticmethod
    def _shares(d, raw_entry, stop) -> int:
        risk_per_share = raw_entry - stop
        if risk_per_share <= 0:
            return 0
        max_risk = d["capital_usd"] * (d["planned_risk_pct_of_capital"] / 100.0)
        # r_multiple is a ratio of P&L to risk, so the FX rate cancels; sizing in local
        # currency keeps the R outcome identical without an FX dependency.
        return max(1, int(max_risk / risk_per_share))

    @staticmethod
    def _find_exit(f, after: pd.DataFrame):
        """First triggered exit, in the backtester's priority order."""
        stop, t1 = f["stop_loss"], f["target_1"]
        for i, (_, b) in enumerate(after.iterrows(), start=1):
            lo, hi, op = float(b["low"]), float(b["high"]), float(b["open"])
            if lo <= stop:
                return str(b["trading_date"]), min(op, stop), "STOP_LOSS"
            if hi >= t1 and i >= TARGET_1_MIN_HOLDING_DAYS:
                return str(b["trading_date"]), t1, "TARGET_1"
            if i >= MAX_HOLDING_DAYS:
                return str(b["trading_date"]), op, "TIME_EXIT"
        return None

    @staticmethod
    def _sessions_between(df, a: str, b: str) -> int:
        d = df["trading_date"]
        return int(((d > a) & (d <= b)).sum())

    # -- reporting ---------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        recs = [r for r in self.read() if r.get("recorded_at")]
        by_sid: Dict[str, List[Dict[str, Any]]] = {}
        for r in recs:
            if r.get("strategy_id"):
                by_sid.setdefault(r["strategy_id"], []).append(r)

        per: Dict[str, Any] = {}
        for sid, rows in by_sid.items():
            closed = [r for r in rows if r.get("record_type") == "CLOSE"
                      and r.get("r_multiple") is not None]
            r_mults = [r["r_multiple"] for r in closed]
            months = sorted({(r.get("recorded_at") or "")[:7] for r in rows} - {""})
            per[sid] = {
                "records": len(rows),
                "decisions": len([r for r in rows if r.get("record_type") == "DECISION"]),
                "fills": len([r for r in rows if r.get("record_type") == "FILL"]),
                "entered": len([r for r in rows if r.get("record_type") == "FILL" and r.get("entered")]),
                "closed_trades": len(closed),
                "months_covered": len(months),
                "months": months,
                "first_recorded_at": min((r.get("recorded_at") for r in rows), default=None),
                "forward_mean_r": round(sum(r_mults) / len(r_mults), 4) if r_mults else None,
            }
        return {
            "exists": self.path.exists(),
            "records": len(recs),
            "per_strategy": per,
            "integrity": self.verify(),
            "integrity_statement": INTEGRITY_STATEMENT,
        }


forward_paper_ledger = ForwardPaperLedger()
