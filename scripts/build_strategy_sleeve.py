"""
Build a three-strategy sleeve from the surviving profitable strategies and validate it
through the same gate suite as every other strategy.

Why this exists
---------------
ADAPTIVE_DUAL_REGIME, PO3_META_GATED and PO3_LIQUIDITY_SWEEP are the only three of the
eight registered strategies with positive gross AND net return. Their daily-return
correlations are 0.455-0.703, so an equal-weight combination reduces volatility without
any parameter being tuned. This script constructs that combination as a first-class
strategy record so strategy_validation.py can run all eight gates against it.

Two things this script refuses to hide
--------------------------------------
1. SELECTION BIAS. The three constituents were chosen AFTER seeing full-period results
   for all eight. That is a selection effect, and it inflates the combination's apparent
   Sharpe. The record carries an explicit note and reports the trial count honestly
   rather than pretending the sleeve was specified in advance.

2. NO REBALANCING COST. The sleeve is the average of three equity curves, i.e. invest
   one third in each sub-strategy and never rebalance. That is deliberate: daily
   rebalancing back to equal weight would add turnover this script does not price, and
   silently assuming it is free would understate cost. The sub-strategy curves already
   include their own 20 bps round-trip costs.

Output is written into backtests.json alongside the existing strategies so the gate
suite evaluates it on identical terms. Run scripts/run_backtests.py first.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FEEDS = Path(__file__).resolve().parent.parent / "data" / "feeds"
DIST = Path(__file__).resolve().parent.parent / "data" / "dist"

SLEEVE_ID = "SLEEVE_3WAY_EQUAL"
CONSTITUENTS = ["ADAPTIVE_DUAL_REGIME", "PO3_META_GATED", "PO3_LIQUIDITY_SWEEP"]
WEIGHT = 1.0 / len(CONSTITUENTS)


def build_sleeve(bundle: dict) -> dict:
    strategies = bundle.get("strategies") or {}
    missing = [c for c in CONSTITUENTS if c not in strategies]
    if missing:
        raise SystemExit(f"constituents not present in backtests.json: {missing}")

    curves = {}
    for c in CONSTITUENTS:
        ec = strategies[c].get("equity_curve") or []
        if not ec:
            raise SystemExit(f"{c} has no equity_curve")
        curves[c] = {p["date"]: p for p in ec}

    # Only dates every constituent has. Dropping a date silently would misalign the
    # average, so intersect and report how much was dropped.
    common = sorted(set.intersection(*[set(c) for c in curves.values()]))
    dropped = {c: len(curves[c]) - len(common) for c in CONSTITUENTS}

    initial = float(strategies[CONSTITUENTS[0]]["config"]["initial_capital_usd"])

    # Equal-weight, never rebalanced: one third of capital in each, left to drift.
    curve = []
    for d in common:
        equity = sum(
            curves[c][d]["portfolio_equity"] * WEIGHT for c in CONSTITUENTS
        )
        ref = curves[CONSTITUENTS[0]][d]
        curve.append({
            "date": d,
            "portfolio_equity": round(equity, 2),
            "cash": None,
            "open_positions": None,
            "benchmark_price": ref.get("benchmark_price"),
            "benchmark_price_spy": ref.get("benchmark_price_spy"),
            "benchmark_price_xiu": ref.get("benchmark_price_xiu"),
            "partition": ref.get("partition"),
        })

    # Pooled trade ledger. Each trade is scaled by its sleeve weight: a sub-strategy
    # running on one third of the capital contributes one third of the return and risks
    # one third as much, so both return_pct and r_multiple scale by the weight.
    trades = []
    for c in CONSTITUENTS:
        for t in (strategies[c].get("all_trades") or strategies[c].get("trades") or []):
            t2 = dict(t)
            t2["strategy_id"] = SLEEVE_ID
            t2["constituent"] = c
            t2["sleeve_weight"] = WEIGHT
            if t2.get("return_pct") is not None:
                t2["return_pct"] = round(t2["return_pct"] * WEIGHT, 6)
            if t2.get("r_multiple") is not None:
                t2["r_multiple"] = round(t2["r_multiple"] * WEIGHT, 6)
            for k in ("net_pnl_usd", "gross_pnl_usd", "fee_drag_usd", "net_pnl", "gross_pnl"):
                if isinstance(t2.get(k), (int, float)):
                    t2[k] = t2[k] * WEIGHT
            trades.append(t2)
    trades.sort(key=lambda t: (t.get("entry_date") or "", t.get("symbol") or ""))

    val_cutoff = strategies[CONSTITUENTS[0]]["config"].get("val_cutoff")

    return {
        "strategy_id": SLEEVE_ID,
        "strategy_name": "Three-Strategy Equal-Weight Sleeve (ADAPTIVE + PO3_META + PO3_LIQ)",
        "status": "EXPERIMENTAL",
        "hypothesis": (
            "Three strategies with different entry mechanisms and daily-return "
            "correlations of 0.455-0.703 should combine to a higher Sharpe than any one "
            "of them, because diversification reduces variance without requiring any "
            "parameter to be fitted."
        ),
        "universe": strategies[CONSTITUENTS[0]].get("universe"),
        "config": {
            "initial_capital_usd": initial,
            "commission_bps": strategies[CONSTITUENTS[0]]["config"].get("commission_bps"),
            "slippage_bps": strategies[CONSTITUENTS[0]]["config"].get("slippage_bps"),
            "risk_per_trade_pct": strategies[CONSTITUENTS[0]]["config"].get("risk_per_trade_pct"),
            "max_open_positions": None,
            "train_cutoff": strategies[CONSTITUENTS[0]]["config"].get("train_cutoff"),
            "val_cutoff": val_cutoff,
            "constituents": CONSTITUENTS,
            "weights": {c: WEIGHT for c in CONSTITUENTS},
            "rebalancing": "NONE - one third in each, left to drift",
            "survivorship_bias_note": strategies[CONSTITUENTS[0]]["config"].get(
                "survivorship_bias_note"),
        },
        "selection_bias_note": (
            "The three constituents were selected AFTER observing full-period results for "
            "all eight registered strategies. This is a post-hoc selection and it inflates "
            "the measured Sharpe of the combination. The sleeve must not be read as a "
            "pre-specified strategy, and its deflated Sharpe should count the selection."
        ),
        "dates_dropped_for_alignment": dropped,
        "equity_curve": curve,
        "all_trades": trades,
        "trades": trades,
        "statutory_disclaimer": strategies[CONSTITUENTS[0]].get("statutory_disclaimer"),
        "disclaimer_version": strategies[CONSTITUENTS[0]].get("disclaimer_version"),
    }


def main() -> None:
    path = FEEDS / "backtests.json"
    bundle = json.load(open(path))
    sleeve = build_sleeve(bundle)
    bundle["strategies"][SLEEVE_ID] = sleeve
    json.dump(bundle, open(path, "w"), indent=2)

    n_test = len([t for t in sleeve["all_trades"]
                  if (t.get("entry_date") or "") > (sleeve["config"]["val_cutoff"] or "")])
    print(f"sleeve written to {path}")
    print(f"  constituents      : {', '.join(CONSTITUENTS)}")
    print(f"  curve points      : {len(sleeve['equity_curve'])}")
    print(f"  pooled trades     : {len(sleeve['all_trades'])}")
    print(f"  TEST-window trades: {n_test}  (cutoff {sleeve['config']['val_cutoff']})")
    print(f"  dropped for align : {sleeve['dates_dropped_for_alignment']}")

    # Mirror to data/dist so strategy_validation can read it from either root.
    DIST.mkdir(parents=True, exist_ok=True)
    json.dump(bundle, open(DIST / "backtests.json", "w"), indent=2)
    print(f"  mirrored to {DIST / 'backtests.json'}")


if __name__ == "__main__":
    main()
