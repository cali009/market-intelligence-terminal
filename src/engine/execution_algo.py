"""
Algorithmic Execution Simulator, Kyle's Lambda Slippage Engine & Transaction Cost Analysis (TCA) (Phase 25.3)
US + Canada Dual-Market Intelligence Platform

Implements Institutional Execution Optimization Standards:
1. Almgren-Chriss (2000) & Kyle (1985) Temporary and Permanent Market Impact Models.
2. Order Book Depth-Weighted Execution Sweeps (L1-L5 Depth of Market).
3. Algorithmic Order Slicing Engines:
   - DIRECT_MARKET: Aggressive liquidity-taking sweep across DOM.
   - LIMIT_PASSIVE: Best-bid/best-ask resting quote with OBI fill probability.
   - TWAP: Time-Weighted Average Price uniform interval slicing.
   - VWAP: Volume-Weighted Average Price intraday volume curve slicing.
   - POV: Percentage of Volume (Iceberg) dynamic market participation rate.
4. Dual-Market Exchange Fee Schedules:
   - US Reg NMS (NYSE/Nasdaq): Maker-Taker pricing ($0.0030 take fee / -$0.0020 rebate).
   - Canadian UMIR (TSX/TSXV): Continuous trading fees ($0.0016 take / -$0.0009 rebate).
5. Implementation Shortfall (Arrival Price vs Effective Execution Price in $ and bps).
6. Statutory Impersonal Research Compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion).
"""

from datetime import datetime, timezone
import math
from typing import Dict, List, Optional, Tuple, Any, Literal
import numpy as np

from src.compliance.linter import linter
from src.models.schemas import (
    OrderBookSnapshot,
    OrderBookLevel,
    MicrostructureMetrics,
    ChildOrderSlice,
    ExecutionPlanResult,
    ExecutionStrategy,
)

TCA_DISCLAIMERS = [
    "Algorithmic execution simulations, slippage estimates, and transaction cost analysis (TCA) are derived quantitative models provided for impersonal decision-support research.",
    "Actual fills in live markets will vary based on queue priority, exchange latency, hidden orders, and volatile order arrival rates.",
    "Does not constitute personalized execution counsel, order routing advice, or fiduciary recommendations under CSA Staff Notice 31-369 and SEC rules."
]


class ExecutionAlgoEngine:
    """
    Simulates institutional trade execution strategies, estimating slippage,
    market impact, exchange maker/taker fees, and optimal child order slicing.
    """

    def __init__(self):
        # US Reg NMS Standard Fee Model ($/share)
        self.us_taker_fee_per_share = 0.0030
        self.us_maker_rebate_per_share = 0.0020
        self.us_regulatory_fee_per_share = 0.000166

        # Canadian UMIR / TSX Continuous Fee Model (CAD/share)
        self.ca_taker_fee_per_share = 0.0016
        self.ca_maker_rebate_per_share = 0.0009
        self.ca_regulatory_fee_per_share = 0.000150

        # Intraday 5-bucket U-shaped volume curve weights for VWAP
        # [Open 9:30-10:30, Morning 10:30-12:00, Midday 12:00-14:00, Afternoon 14:00-15:00, Close 15:00-16:00]
        self.vwap_intraday_weights = [0.28, 0.16, 0.12, 0.16, 0.28]

    def get_exchange_fee(self, shares: int, country: str, is_maker: bool) -> float:
        """
        Calculates net exchange and regulatory clearing fee for a given share quantity.
        Returns positive value for fee paid, negative for rebate earned.
        """
        if country == "CA":
            if is_maker:
                net_rate = -self.ca_maker_rebate_per_share + self.ca_regulatory_fee_per_share
            else:
                net_rate = self.ca_taker_fee_per_share + self.ca_regulatory_fee_per_share
        else:
            if is_maker:
                net_rate = -self.us_maker_rebate_per_share + self.us_regulatory_fee_per_share
            else:
                net_rate = self.us_taker_fee_per_share + self.us_regulatory_fee_per_share

        return round(float(shares * net_rate), 4)

    def simulate_direct_market_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        shares: int,
        book: OrderBookSnapshot,
        country: str,
        kyle_lambda: float = 0.015,
    ) -> ExecutionPlanResult:
        """
        Simulates an immediate market order sweeping through top-of-book levels.
        """
        arrival_price = book.mid_price
        spread = book.spread_dollars
        total_ask_depth = sum(a.size for a in book.asks)
        total_bid_depth = sum(b.size for b in book.bids)
        levels = book.asks if side == "BUY" else book.bids

        remaining = shares
        filled_cost = 0.0
        slices: List[ChildOrderSlice] = []
        slice_idx = 1

        for i, lvl in enumerate(levels):
            if remaining <= 0:
                break
            fill_qty = min(remaining, lvl.size)
            fill_price = lvl.price
            impact_bps = abs(fill_price - arrival_price) / arrival_price * 10000.0

            fee = self.get_exchange_fee(fill_qty, country, is_maker=False)
            slices.append(
                ChildOrderSlice(
                    slice_idx=slice_idx,
                    offset_sec=0,
                    shares=fill_qty,
                    projected_price=round(fill_price, 2),
                    projected_impact_bps=round(impact_bps, 2),
                    fee_or_rebate=round(fee, 4),
                )
            )
            filled_cost += fill_qty * fill_price
            remaining -= fill_qty
            slice_idx += 1

        # Residual beyond 5 DOM levels suffers Kyle's lambda market impact expansion
        if remaining > 0:
            last_lvl_price = levels[-1].price
            # Impact shifts price further away from midpoint
            residual_impact = (remaining / 10000.0) * kyle_lambda
            if side == "BUY":
                penalty_price = last_lvl_price + residual_impact
            else:
                penalty_price = max(0.01, last_lvl_price - residual_impact)

            impact_bps = abs(penalty_price - arrival_price) / arrival_price * 10000.0
            fee = self.get_exchange_fee(remaining, country, is_maker=False)
            slices.append(
                ChildOrderSlice(
                    slice_idx=slice_idx,
                    offset_sec=1,
                    shares=remaining,
                    projected_price=round(penalty_price, 2),
                    projected_impact_bps=round(impact_bps, 2),
                    fee_or_rebate=round(fee, 4),
                )
            )
            filled_cost += remaining * penalty_price
            remaining = 0

        effective_price = filled_cost / max(1, shares)
        slippage_per_share = (effective_price - arrival_price) if side == "BUY" else (arrival_price - effective_price)
        slippage_bps = (slippage_per_share / arrival_price) * 10000.0

        half_spread_cost = (spread / 2.0) * shares
        market_impact_cost = max(0.0, (abs(slippage_per_share) * shares) - half_spread_cost)
        exchange_fees = sum(s.fee_or_rebate for s in slices)
        total_exec_cost = (abs(slippage_per_share) * shares) + exchange_fees
        is_bps = (total_exec_cost / (arrival_price * shares)) * 10000.0

        return ExecutionPlanResult(
            symbol=symbol,
            strategy="DIRECT_MARKET",
            side=side,
            total_shares=shares,
            arrival_price=round(arrival_price, 2),
            expected_effective_price=round(effective_price, 2),
            expected_slippage_per_share=round(slippage_per_share, 4),
            expected_slippage_bps=round(slippage_bps, 2),
            market_impact_cost=round(market_impact_cost, 2),
            spread_cost=round(half_spread_cost, 2),
            exchange_fees=round(exchange_fees, 2),
            total_execution_cost=round(total_exec_cost, 2),
            implementation_shortfall_bps=round(is_bps, 2),
            fill_probability_pct=99.9,
            recommended_strategy="DIRECT_MARKET",
            maker_taker_status="TAKER",
            child_slices=slices,
            disclaimers=TCA_DISCLAIMERS,
        )

    def simulate_limit_passive_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        shares: int,
        book: OrderBookSnapshot,
        country: str,
        obi: float = 0.0,
        kyle_lambda: float = 0.015,
    ) -> ExecutionPlanResult:
        """
        Simulates a passive resting limit order posted at the best bid (for buys) or best ask (for sells).
        Captures the maker rebate and avoids spread crossing, but carries non-fill risk and adverse selection.
        """
        arrival_price = book.mid_price
        posted_price = book.bids[0].price if side == "BUY" else book.asks[0].price
        top_depth = book.bids[0].size if side == "BUY" else book.asks[0].size

        # Maker fee rebate
        exchange_fees = self.get_exchange_fee(shares, country, is_maker=True)

        # Spread benefit: capturing half spread rather than paying it
        spread_benefit = (book.spread_dollars / 2.0) * shares

        # Fill probability modeled via logistic function based on OBI and queue size
        # If OBI > 0 for buys, higher queue ahead of us, but more eager market orders coming into book
        logit = 1.2 + (obi * 1.5) - (0.8 * (shares / max(100, top_depth)))
        fill_prob = float(1.0 / (1.0 + math.exp(-logit))) * 100.0
        fill_prob = round(float(np.clip(fill_prob, 25.0, 92.0)), 1)

        # Adverse selection risk: probability that prices tick through our limit
        adverse_selection_pct = 0.35 if (side == "BUY" and obi < -0.2) or (side == "SELL" and obi > 0.2) else 0.15
        adverse_impact_per_sh = (kyle_lambda * 0.25) * adverse_selection_pct
        effective_price = posted_price + (adverse_impact_per_sh if side == "BUY" else -adverse_impact_per_sh)

        slippage_per_share = (effective_price - arrival_price) if side == "BUY" else (arrival_price - effective_price)
        slippage_bps = (slippage_per_share / arrival_price) * 10000.0

        total_exec_cost = (slippage_per_share * shares) + exchange_fees
        is_bps = (total_exec_cost / (arrival_price * shares)) * 10000.0

        slices = [
            ChildOrderSlice(
                slice_idx=1,
                offset_sec=0,
                shares=shares,
                projected_price=round(effective_price, 2),
                projected_impact_bps=round(slippage_bps, 2),
                fee_or_rebate=round(exchange_fees, 4),
            )
        ]

        return ExecutionPlanResult(
            symbol=symbol,
            strategy="LIMIT_PASSIVE",
            side=side,
            total_shares=shares,
            arrival_price=round(arrival_price, 2),
            expected_effective_price=round(effective_price, 2),
            expected_slippage_per_share=round(slippage_per_share, 4),
            expected_slippage_bps=round(slippage_bps, 2),
            market_impact_cost=round(adverse_impact_per_sh * shares, 2),
            spread_cost=-round(spread_benefit, 2),
            exchange_fees=round(exchange_fees, 2),
            total_execution_cost=round(total_exec_cost, 2),
            implementation_shortfall_bps=round(is_bps, 2),
            fill_probability_pct=fill_prob,
            recommended_strategy="LIMIT_PASSIVE",
            maker_taker_status="MAKER",
            child_slices=slices,
            disclaimers=TCA_DISCLAIMERS,
        )

    def simulate_twap_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        shares: int,
        book: OrderBookSnapshot,
        country: str,
        kyle_lambda: float = 0.015,
        num_slices: int = 5,
        horizon_mins: int = 15,
    ) -> ExecutionPlanResult:
        """
        Simulates Time-Weighted Average Price uniform order slicing over N minutes.
        """
        arrival_price = book.mid_price
        slice_shares = [shares // num_slices] * num_slices
        # Distribute remainder
        for i in range(shares % num_slices):
            slice_shares[i] += 1

        interval_sec = int((horizon_mins * 60) / num_slices)
        slices: List[ChildOrderSlice] = []
        weighted_cost = 0.0

        # Almgren-Chriss square-root impact reduction
        alpha = 0.55
        impact_reduction = 1.0 / (num_slices ** alpha)

        for i, s_qty in enumerate(slice_shares):
            offset_sec = i * interval_sec
            # Base price has slight random walk drift plus reduced temporary impact
            slice_impact_dollars = (s_qty / 10000.0) * kyle_lambda * impact_reduction
            spread_cost_per_sh = (book.spread_dollars / 2.0) * 0.7  # Mixed maker/taker fills

            if side == "BUY":
                proj_price = arrival_price + spread_cost_per_sh + slice_impact_dollars
            else:
                proj_price = max(0.01, arrival_price - spread_cost_per_sh - slice_impact_dollars)

            impact_bps = abs(proj_price - arrival_price) / arrival_price * 10000.0
            fee = self.get_exchange_fee(s_qty, country, is_maker=(i % 2 == 1))  # Alternate maker/taker

            slices.append(
                ChildOrderSlice(
                    slice_idx=i + 1,
                    offset_sec=offset_sec,
                    shares=s_qty,
                    projected_price=round(proj_price, 2),
                    projected_impact_bps=round(impact_bps, 2),
                    fee_or_rebate=round(fee, 4),
                )
            )
            weighted_cost += s_qty * proj_price

        effective_price = weighted_cost / max(1, shares)
        slippage_per_share = (effective_price - arrival_price) if side == "BUY" else (arrival_price - effective_price)
        slippage_bps = (slippage_per_share / arrival_price) * 10000.0

        exchange_fees = sum(s.fee_or_rebate for s in slices)
        spread_cost = (book.spread_dollars / 2.0) * shares * 0.7
        market_impact_cost = max(0.0, (slippage_per_share * shares) - spread_cost)
        total_exec_cost = (slippage_per_share * shares) + exchange_fees
        is_bps = (total_exec_cost / (arrival_price * shares)) * 10000.0

        return ExecutionPlanResult(
            symbol=symbol,
            strategy="TWAP",
            side=side,
            total_shares=shares,
            arrival_price=round(arrival_price, 2),
            expected_effective_price=round(effective_price, 2),
            expected_slippage_per_share=round(slippage_per_share, 4),
            expected_slippage_bps=round(slippage_bps, 2),
            market_impact_cost=round(market_impact_cost, 2),
            spread_cost=round(spread_cost, 2),
            exchange_fees=round(exchange_fees, 2),
            total_execution_cost=round(total_exec_cost, 2),
            implementation_shortfall_bps=round(is_bps, 2),
            fill_probability_pct=95.0,
            recommended_strategy="TWAP",
            maker_taker_status="MIXED",
            child_slices=slices,
            disclaimers=TCA_DISCLAIMERS,
        )

    def simulate_vwap_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        shares: int,
        book: OrderBookSnapshot,
        country: str,
        kyle_lambda: float = 0.015,
        horizon_mins: int = 30,
    ) -> ExecutionPlanResult:
        """
        Simulates Volume-Weighted Average Price slicing weighted by intraday U-curve.
        Higher allocation in high-liquidity volume buckets reduces total Kyle's lambda impact.
        """
        arrival_price = book.mid_price
        weights = self.vwap_intraday_weights
        num_slices = len(weights)
        slice_shares = [int(round(shares * w)) for w in weights]

        # Fix rounding sum
        diff = shares - sum(slice_shares)
        slice_shares[0] += diff

        interval_sec = int((horizon_mins * 60) / num_slices)
        slices: List[ChildOrderSlice] = []
        weighted_cost = 0.0

        # VWAP achieves ~15% better impact reduction than TWAP due to volume alignment
        alpha = 0.62
        impact_reduction = (1.0 / (num_slices ** alpha)) * 0.85

        for i, (s_qty, w) in enumerate(zip(slice_shares, weights)):
            offset_sec = i * interval_sec
            slice_impact_dollars = (s_qty / 10000.0) * kyle_lambda * impact_reduction * (1.0 / math.sqrt(max(0.01, w * 3.5)))
            spread_cost_per_sh = (book.spread_dollars / 2.0) * 0.6

            if side == "BUY":
                proj_price = arrival_price + spread_cost_per_sh + slice_impact_dollars
            else:
                proj_price = max(0.01, arrival_price - spread_cost_per_sh - slice_impact_dollars)

            impact_bps = abs(proj_price - arrival_price) / arrival_price * 10000.0
            fee = self.get_exchange_fee(s_qty, country, is_maker=(i in (1, 2, 3)))

            slices.append(
                ChildOrderSlice(
                    slice_idx=i + 1,
                    offset_sec=offset_sec,
                    shares=s_qty,
                    projected_price=round(proj_price, 2),
                    projected_impact_bps=round(impact_bps, 2),
                    fee_or_rebate=round(fee, 4),
                )
            )
            weighted_cost += s_qty * proj_price

        effective_price = weighted_cost / max(1, shares)
        slippage_per_share = (effective_price - arrival_price) if side == "BUY" else (arrival_price - effective_price)
        slippage_bps = (slippage_per_share / arrival_price) * 10000.0

        exchange_fees = sum(s.fee_or_rebate for s in slices)
        spread_cost = (book.spread_dollars / 2.0) * shares * 0.6
        market_impact_cost = max(0.0, (slippage_per_share * shares) - spread_cost)
        total_exec_cost = (slippage_per_share * shares) + exchange_fees
        is_bps = (total_exec_cost / (arrival_price * shares)) * 10000.0

        return ExecutionPlanResult(
            symbol=symbol,
            strategy="VWAP",
            side=side,
            total_shares=shares,
            arrival_price=round(arrival_price, 2),
            expected_effective_price=round(effective_price, 2),
            expected_slippage_per_share=round(slippage_per_share, 4),
            expected_slippage_bps=round(slippage_bps, 2),
            market_impact_cost=round(market_impact_cost, 2),
            spread_cost=round(spread_cost, 2),
            exchange_fees=round(exchange_fees, 2),
            total_execution_cost=round(total_exec_cost, 2),
            implementation_shortfall_bps=round(is_bps, 2),
            fill_probability_pct=96.5,
            recommended_strategy="VWAP",
            maker_taker_status="MIXED",
            child_slices=slices,
            disclaimers=TCA_DISCLAIMERS,
        )

    def simulate_pov_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        shares: int,
        book: OrderBookSnapshot,
        country: str,
        kyle_lambda: float = 0.015,
        target_participation: float = 0.10,
    ) -> ExecutionPlanResult:
        """
        Simulates Percentage of Volume (POV / Iceberg) dynamic participation strategy.
        Caps market participation at 10% to prevent signaling and front-running.
        """
        arrival_price = book.mid_price
        total_ask_depth = sum(a.size for a in book.asks)
        # Top-of-book size gives baseline for typical minute bar volume
        est_minute_volume = max(500, total_ask_depth * 2)
        slice_size = max(25, int(est_minute_volume * target_participation))

        if slice_size >= shares:
            # Order fits in 2-3 smaller iceberg child slices
            num_slices = max(2, min(4, shares // 25)) if shares >= 50 else 1
            slice_shares = [shares // num_slices] * num_slices
            for i in range(shares % num_slices):
                slice_shares[i] += 1
        else:
            num_full = shares // slice_size
            remainder = shares % slice_size
            slice_shares = [slice_size] * num_full
            if remainder > 0:
                slice_shares.append(remainder)
        num_slices = len(slice_shares)

        interval_sec = 60  # 1 minute per participation interval
        slices: List[ChildOrderSlice] = []
        weighted_cost = 0.0

        for i, s_qty in enumerate(slice_shares):
            offset_sec = i * interval_sec
            slice_impact_dollars = (s_qty / 10000.0) * kyle_lambda * 0.45
            spread_cost_per_sh = (book.spread_dollars / 2.0) * 0.5

            if side == "BUY":
                proj_price = arrival_price + spread_cost_per_sh + slice_impact_dollars
            else:
                proj_price = max(0.01, arrival_price - spread_cost_per_sh - slice_impact_dollars)

            impact_bps = abs(proj_price - arrival_price) / arrival_price * 10000.0
            fee = self.get_exchange_fee(s_qty, country, is_maker=True)  # Iceberg orders rest as makers

            slices.append(
                ChildOrderSlice(
                    slice_idx=i + 1,
                    offset_sec=offset_sec,
                    shares=s_qty,
                    projected_price=round(proj_price, 2),
                    projected_impact_bps=round(impact_bps, 2),
                    fee_or_rebate=round(fee, 4),
                )
            )
            weighted_cost += s_qty * proj_price

        effective_price = weighted_cost / max(1, shares)
        slippage_per_share = (effective_price - arrival_price) if side == "BUY" else (arrival_price - effective_price)
        slippage_bps = (slippage_per_share / arrival_price) * 10000.0

        exchange_fees = sum(s.fee_or_rebate for s in slices)
        spread_cost = (book.spread_dollars / 2.0) * shares * 0.5
        market_impact_cost = max(0.0, (slippage_per_share * shares) - spread_cost)
        total_exec_cost = (slippage_per_share * shares) + exchange_fees
        is_bps = (total_exec_cost / (arrival_price * shares)) * 10000.0

        return ExecutionPlanResult(
            symbol=symbol,
            strategy="POV_10",
            side=side,
            total_shares=shares,
            arrival_price=round(arrival_price, 2),
            expected_effective_price=round(effective_price, 2),
            expected_slippage_per_share=round(slippage_per_share, 4),
            expected_slippage_bps=round(slippage_bps, 2),
            market_impact_cost=round(market_impact_cost, 2),
            spread_cost=round(spread_cost, 2),
            exchange_fees=round(exchange_fees, 2),
            total_execution_cost=round(total_exec_cost, 2),
            implementation_shortfall_bps=round(is_bps, 2),
            fill_probability_pct=91.0,
            recommended_strategy="POV_10",
            maker_taker_status="MAKER",
            child_slices=slices,
            disclaimers=TCA_DISCLAIMERS,
        )

        return ExecutionPlanResult(
            symbol=symbol,
            strategy="POV_10",
            side=side,
            total_shares=shares,
            arrival_price=round(arrival_price, 2),
            expected_effective_price=round(effective_price, 2),
            expected_slippage_per_share=round(slippage_per_share, 4),
            expected_slippage_bps=round(slippage_bps, 2),
            market_impact_cost=round(market_impact_cost, 2),
            spread_cost=round(spread_cost, 2),
            exchange_fees=round(exchange_fees, 2),
            total_execution_cost=round(total_exec_cost, 2),
            implementation_shortfall_bps=round(is_bps, 2),
            fill_probability_pct=91.0,
            recommended_strategy="POV_10",
            maker_taker_status="MAKER",
            child_slices=slices,
            disclaimers=TCA_DISCLAIMERS,
        )

    def evaluate_all_strategies(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        shares: int,
        book: OrderBookSnapshot,
        metrics: MicrostructureMetrics,
        country: str,
    ) -> Dict[str, Any]:
        """
        Computes execution simulations across all 5 strategies and determines
        the quantitatively optimal routing strategy for decision support.
        """
        k_lambda = metrics.kyle_lambda
        obi = metrics.order_book_imbalance

        market_res = self.simulate_direct_market_order(symbol, side, shares, book, country, k_lambda)
        limit_res = self.simulate_limit_passive_order(symbol, side, shares, book, country, obi, k_lambda)
        twap_res = self.simulate_twap_order(symbol, side, shares, book, country, k_lambda)
        vwap_res = self.simulate_vwap_order(symbol, side, shares, book, country, k_lambda)
        pov_res = self.simulate_pov_order(symbol, side, shares, book, country, k_lambda)

        # Decision rule for optimal recommendation
        # 1. If liquidity void or thin book -> LIMIT_PASSIVE or POV_10 (market orders strongly discouraged)
        total_ask_depth = sum(a.size for a in book.asks)
        if metrics.liquidity_state in ("LIQUIDITY_VOID", "SPREAD_BLOWOUT"):
            best_strat = "POV_10" if shares > 300 else "LIMIT_PASSIVE"
            rationale = "Liquidity void or spread expansion detected; passive execution mandatory to avoid sweeping empty books."
        elif shares <= (book.asks[0].size if side == "BUY" else book.bids[0].size) * 0.4:
            # Small order easily absorbed at top of book
            best_strat = "DIRECT_MARKET"
            rationale = "Order size is less than 40% of top-of-book depth; immediate direct execution incurs minimal impact."
        elif shares > 1000 or (shares > total_ask_depth * 0.5):
            # Large order relative to depth
            best_strat = "VWAP"
            rationale = "Large order relative to visible book depth; VWAP volume curve slicing optimizes Kyle's lambda impact."
        elif abs(obi) > 0.4 and ((side == "BUY" and obi > 0) or (side == "SELL" and obi < 0)):
            # Strong favorable book imbalance
            best_strat = "LIMIT_PASSIVE"
            rationale = "Favorable order book imbalance allows passive queue posting to earn maker rebates."
        else:
            best_strat = "TWAP"
            rationale = "Moderate order size; uniform TWAP time-slicing stabilizes execution price across time intervals."

        strategies_map = {
            "DIRECT_MARKET": market_res.to_dict(),
            "LIMIT_PASSIVE": limit_res.to_dict(),
            "TWAP": twap_res.to_dict(),
            "VWAP": vwap_res.to_dict(),
            "POV_10": pov_res.to_dict(),
        }

        # Compliance assert clean
        for disc in TCA_DISCLAIMERS:
            linter.assert_clean(disc)
        linter.assert_clean(rationale)

        return {
            "symbol": symbol,
            "side": side,
            "target_shares": shares,
            "recommended_strategy": best_strat,
            "recommendation_rationale": rationale,
            "strategies": strategies_map,
            "summary_comparison": [
                {
                    "strategy": strat,
                    "effective_price": data["expected_effective_price"],
                    "slippage_bps": data["expected_slippage_bps"],
                    "total_cost": data["total_execution_cost"],
                    "fill_probability_pct": data["fill_probability_pct"],
                    "maker_taker": data["maker_taker_status"],
                    "slices_count": len(data["child_slices"]),
                }
                for strat, data in strategies_map.items()
            ],
            "disclaimers": TCA_DISCLAIMERS,
        }


# Global singleton instance
execution_algo_engine = ExecutionAlgoEngine()
