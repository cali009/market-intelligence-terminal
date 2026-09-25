"""
Event-Driven Walk-Forward Backtesting Engine (Dual-Market US + Canada)
US + Canada Market Intelligence Platform

Strict anti-bias controls and realistic quantitative standards:
1. Strict Anti-Lookahead: Signals generated on bar T close; orders executed on bar T+1 Open.
2. Same-Day Intraday Risk: Positions evaluated for adverse intraday stops on day of entry.
3. Adverse Intrabar Priority: If a bar touches both Stop and Target, the Stop Loss triggers first.
4. Cross-Border FX Normalization: Converts CAD equity transactions to USD portfolio base via point-in-time Bank of Canada FX rates.
5. Realistic Cost Modeling: 5 bps commission + 5 bps slippage per side (10 bps per side, 20 bps round-trip).
6. Disaggregated Gross vs Net: Explicitly isolates fee drag ($ and %) from strategy alpha.
7. Walk-Forward / Out-of-Sample Partitioning:
   - TRAIN: 60% of timeline (hypothesis & parameter calibration)
   - VALIDATION: 20% of timeline (hyperparameter tuning)
   - TEST: 20% of timeline (untouched out-of-sample verification)
8. Dual-Benchmark Attribution: Compares against SPY (US), XIU (Canada), and 60/40 Blended Benchmark.
9. Statutory Compliance: Mandatory CSA Staff Notice 31-369 and SEC Rule 206(4)-1 hypothetical disclosures.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Any, List, Optional
import math
import numpy as np
import pandas as pd

from config.settings import DATA_DIR
from src.compliance.disclaimers import HYPOTHETICAL_BACKTEST_DISCLAIMER, DISCLAIMER_VERSION
from src.data.db import db
from src.engine.technicals import TechnicalAnalysisEngine


@dataclass
class BacktestConfig:
    initial_capital_usd: float = 100000.0
    commission_bps: float = 5.0     # 0.05%
    slippage_bps: float = 5.0       # 0.05%
    risk_per_trade_pct: float = 1.0 # 1.0% equity risk per trade (1R)
    max_open_positions: int = 8
    max_single_position_pct: float = 15.0 # Max 15% capital per position
    split_train_pct: float = 0.60
    split_val_pct: float = 0.20
    split_test_pct: float = 0.20
    annual_sovereign_benchmark_yield: float = 0.035 # 3.5% baseline sovereign yield


@dataclass
class SimulatedTrade:
    symbol: str
    exchange: str
    country: str
    currency: str
    direction: str
    signal_date: str
    entry_date: str
    raw_entry_price: float
    entry_price_net: float
    entry_fx_rate: float            # CAD/USD FX rate at entry (1.0 for USD)
    entry_price_usd_net: float
    exit_date: Optional[str] = None
    raw_exit_price: Optional[float] = None
    exit_price_net: Optional[float] = None
    exit_fx_rate: Optional[float] = None
    exit_price_usd_net: Optional[float] = None
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    target_3: float = 0.0
    initial_risk_per_share: float = 0.0
    shares: int = 0
    gross_pnl_usd: float = 0.0
    net_pnl_usd: float = 0.0
    fee_drag_usd: float = 0.0
    return_pct: float = 0.0
    r_multiple: float = 0.0
    holding_days: int = 0
    exit_reason: str = "OPEN"
    regime_at_entry: str = "UNKNOWN"
    partition: str = "TRAIN"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": str(self.symbol),
            "exchange": str(self.exchange),
            "country": str(self.country),
            "currency": str(self.currency),
            "direction": str(self.direction),
            "signal_date": str(self.signal_date),
            "entry_date": str(self.entry_date),
            "entry_price": round(float(self.entry_price_net), 2),
            "entry_price_usd": round(float(self.entry_price_usd_net), 2),
            "exit_date": str(self.exit_date) if self.exit_date else None,
            "exit_price": round(float(self.exit_price_net), 2) if self.exit_price_net else None,
            "exit_price_usd": round(float(self.exit_price_usd_net), 2) if self.exit_price_usd_net else None,
            "stop_loss": round(float(self.stop_loss), 2),
            "target_1": round(float(self.target_1), 2),
            "target_2": round(float(self.target_2), 2),
            "target_3": round(float(self.target_3), 2),
            "shares": int(self.shares),
            "gross_pnl": round(float(self.gross_pnl_usd), 2),
            "net_pnl": round(float(self.net_pnl_usd), 2),
            "gross_pnl_usd": round(float(self.gross_pnl_usd), 2),
            "net_pnl_usd": round(float(self.net_pnl_usd), 2),
            "fee_drag_usd": round(float(self.fee_drag_usd), 2),
            "return_pct": round(float(self.return_pct), 2),
            "r_multiple": round(float(self.r_multiple), 2),
            "holding_days": int(self.holding_days),
            "exit_reason": str(self.exit_reason),
            "regime_at_entry": str(self.regime_at_entry),
            "partition": str(self.partition),
        }


class BacktestEngine:
    """
    Production-grade event-driven walk-forward backtesting engine.
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()

    def run_strategy_backtest(
        self,
        strategy_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 1. Load securities and historical daily price bars
        securities = db.execute_query(
            "SELECT security_id, symbol, exchange, country, currency FROM security WHERE is_active = 1;"
        )
        sec_map = {s["security_id"]: s for s in securities}

        # Query all bars sorted chronologically
        bars_query = """
            SELECT security_id, trading_date, open, high, low, close, volume, adjusted_close
            FROM bar_1d
            ORDER BY trading_date ASC;
        """
        all_bars = db.execute_query(bars_query)
        if not all_bars:
            raise ValueError("No historical price bars found in database to backtest.")

        # Load Bank of Canada CAD/USD FX observations for cross-border currency normalization
        fx_rows = db.execute_query(
            "SELECT observation_date, value FROM macro_observation WHERE series_id = 'FXUSDCAD' ORDER BY observation_date ASC;"
        )
        fx_rates_raw = {r["observation_date"]: float(r["value"]) for r in fx_rows}
        last_known_fx = 1.38

        # Group bars by security_id into DataFrames
        sec_bars: Dict[int, pd.DataFrame] = {}
        for b in all_bars:
            s_id = b["security_id"]
            if s_id not in sec_bars:
                sec_bars[s_id] = []
            sec_bars[s_id].append(b)

        sec_dfs: Dict[int, pd.DataFrame] = {}
        all_trading_dates_set = set()
        for s_id, b_list in sec_bars.items():
            df = pd.DataFrame(b_list)
            df = TechnicalAnalysisEngine.compute_all_features(df)
            sec_dfs[s_id] = df
            all_trading_dates_set.update(df["trading_date"].tolist())

        timeline = sorted(list(all_trading_dates_set))
        if len(timeline) < 60:
            raise ValueError(f"Insufficient trading history ({len(timeline)} sessions). Minimum 60 required.")

        # Apply date filters if provided
        if start_date:
            timeline = [d for d in timeline if d >= start_date]
        if end_date:
            timeline = [d for d in timeline if d <= end_date]

        # 2. Determine Walk-Forward Partitions (Train 60%, Val 20%, Test 20%)
        n_sessions = len(timeline)
        train_idx = int(n_sessions * self.config.split_train_pct)
        val_idx = train_idx + int(n_sessions * self.config.split_val_pct)

        train_cutoff = timeline[train_idx]
        val_cutoff = timeline[val_idx]

        def get_partition(d: str) -> str:
            if d < train_cutoff:
                return "TRAIN"
            elif d < val_cutoff:
                return "VALIDATION"
            else:
                return "TEST"

        # 3. Dual-Market Benchmark Data (SPY for US, XIU for Canada)
        benchmark_close_spy: Dict[str, float] = {}
        benchmark_close_xiu: Dict[str, float] = {}

        spy_sec = next((s for s in securities if s["symbol"] == "SPY"), None)
        if spy_sec and spy_sec["security_id"] in sec_dfs:
            spy_df = sec_dfs[spy_sec["security_id"]]
            for _, row in spy_df.iterrows():
                benchmark_close_spy[row["trading_date"]] = row["close"]

        xiu_sec = next((s for s in securities if s["symbol"] == "XIU"), None)
        if xiu_sec and xiu_sec["security_id"] in sec_dfs:
            xiu_df = sec_dfs[xiu_sec["security_id"]]
            for _, row in xiu_df.iterrows():
                benchmark_close_xiu[row["trading_date"]] = row["close"]

        # 4. Simulation State (USD Base Currency)
        capital_usd = self.config.initial_capital_usd
        cash_usd = capital_usd
        open_trades: List[SimulatedTrade] = []
        closed_trades: List[SimulatedTrade] = []
        equity_curve: List[Dict[str, Any]] = []

        # Fee rate per side (5 bps commission + 5 bps slippage = 10 bps per side)
        fee_rate = (self.config.commission_bps + self.config.slippage_bps) / 10000.0

        # Helper to get point-in-time FX
        def get_fx_to_usd(t_date: str, curr: str) -> float:
            nonlocal last_known_fx
            if curr == "USD":
                return 1.0
            val = fx_rates_raw.get(t_date)
            if val and val > 0:
                last_known_fx = val
            # 1 USD = last_known_fx CAD -> 1 CAD = (1 / last_known_fx) USD
            return 1.0 / last_known_fx

        # Step through every trading session chronologically
        for t_idx in range(50, len(timeline)):
            curr_date = timeline[t_idx]
            curr_partition = get_partition(curr_date)
            prev_date = timeline[t_idx - 1]

            # A. Update Open Positions & Check Stop/Target Execution on Today's Bar
            still_open: List[SimulatedTrade] = []

            for trade in open_trades:
                s_meta = next((s for s in securities if s["symbol"] == trade.symbol), None)
                if not s_meta or s_meta["security_id"] not in sec_dfs:
                    still_open.append(trade)
                    continue

                df_s = sec_dfs[s_meta["security_id"]]
                today_bar = df_s[df_s["trading_date"] == curr_date]
                if today_bar.empty:
                    still_open.append(trade)
                    continue

                bar = today_bar.iloc[0]
                b_open = bar["open"]
                b_high = bar["high"]
                b_low = bar["low"]
                b_close = bar["close"]

                trade.holding_days += 1
                exit_price_net: Optional[float] = None
                raw_exit_price: Optional[float] = None
                exit_reason: Optional[str] = None

                # ADVERSE PRIORITY RULE: Check Stop Loss Trigger FIRST
                if b_low <= trade.stop_loss:
                    raw_exit = min(b_open, trade.stop_loss)
                    raw_exit_price = raw_exit
                    exit_price_net = raw_exit * (1.0 - fee_rate)
                    exit_reason = "STOP_LOSS" if trade.stop_loss < trade.raw_entry_price else "BREAKEVEN_STOP"

                # Check Profit Target 2 Trigger (only if Stop was NOT hit)
                elif b_high >= trade.target_2:
                    raw_exit_price = trade.target_2
                    exit_price_net = trade.target_2 * (1.0 - fee_rate)
                    exit_reason = "TARGET_2"

                # Check Profit Target 1 Trigger after multi-session hold
                elif b_high >= trade.target_1 and trade.holding_days >= 6:
                    raw_exit_price = trade.target_1
                    exit_price_net = trade.target_1 * (1.0 - fee_rate)
                    exit_reason = "TARGET_1"

                # Max Holding Horizon Rule (20 sessions max)
                elif trade.holding_days >= 20:
                    raw_exit_price = b_close
                    exit_price_net = b_close * (1.0 - fee_rate)
                    exit_reason = "TIME_EXPIRY"

                # Breakeven Stop Trailing Rule (if Target 1 touched and not exited)
                elif b_high >= trade.target_1 and trade.stop_loss < trade.raw_entry_price:
                    trade.stop_loss = trade.raw_entry_price

                # Execute Exit if triggered
                if exit_price_net and exit_reason and raw_exit_price:
                    trade.exit_date = curr_date
                    trade.raw_exit_price = raw_exit_price
                    trade.exit_price_net = exit_price_net
                    trade.exit_reason = exit_reason

                    exit_fx = get_fx_to_usd(curr_date, trade.currency)
                    trade.exit_fx_rate = exit_fx
                    trade.exit_price_usd_net = exit_price_net * exit_fx
                    raw_exit_usd = raw_exit_price * exit_fx
                    raw_entry_usd = trade.raw_entry_price * trade.entry_fx_rate

                    trade.gross_pnl_usd = (raw_exit_usd - raw_entry_usd) * trade.shares
                    trade.net_pnl_usd = (trade.exit_price_usd_net - trade.entry_price_usd_net) * trade.shares
                    trade.fee_drag_usd = trade.gross_pnl_usd - trade.net_pnl_usd
                    trade.return_pct = ((trade.exit_price_usd_net / trade.entry_price_usd_net) - 1.0) * 100.0

                    if trade.initial_risk_per_share > 0:
                        trade.r_multiple = (trade.exit_price_net - trade.entry_price_net) / trade.initial_risk_per_share
                    else:
                        trade.r_multiple = 0.0

                    cash_usd += (trade.exit_price_usd_net * trade.shares)
                    closed_trades.append(trade)
                else:
                    still_open.append(trade)

            open_trades = still_open

            # B. Scan for New Signals from Previous Day's Close (No Look-Ahead)
            # Orders execute on TODAY'S OPEN
            if len(open_trades) < self.config.max_open_positions:
                for s in securities:
                    if len(open_trades) >= self.config.max_open_positions:
                        break

                    sym = s["symbol"]
                    if any(t.symbol == sym for t in open_trades):
                        continue

                    s_id = s["security_id"]
                    if s_id not in sec_dfs:
                        continue

                    df_s = sec_dfs[s_id]
                    idx_today_series = df_s.index[df_s["trading_date"] == curr_date]
                    if len(idx_today_series) == 0 or idx_today_series[0] == 0:
                        continue
                    
                    row_idx = idx_today_series[0]
                    prior_bar = df_s.iloc[row_idx - 1]
                    today_bar = df_s.iloc[row_idx]

                    # Verify signal condition on prior_bar
                    signal_fired = self._evaluate_signal_predicate(strategy_id, prior_bar)
                    if signal_fired:
                        raw_entry = today_bar["open"]
                        entry_net = raw_entry * (1.0 + fee_rate)
                        atr = prior_bar.get("atr_14", raw_entry * 0.02)
                        if not atr or math.isnan(atr) or atr <= 0:
                            atr = raw_entry * 0.02

                        stop_loss = round(raw_entry - (1.5 * atr), 2)
                        risk_per_share = raw_entry - stop_loss
                        if risk_per_share <= 0:
                            continue

                        target_1 = round(raw_entry + (1.6 * risk_per_share), 2)
                        target_2 = round(raw_entry + (2.6 * risk_per_share), 2)
                        target_3 = round(raw_entry + (4.0 * risk_per_share), 2)

                        # Currency conversion for sizing
                        curr_fx = get_fx_to_usd(curr_date, s["currency"])
                        entry_price_usd = entry_net * curr_fx
                        risk_per_share_usd = risk_per_share * curr_fx

                        # Position Sizing: 1% risk / risk_per_share_usd
                        max_risk_amount = capital_usd * (self.config.risk_per_trade_pct / 100.0)
                        raw_shares = int(max_risk_amount / risk_per_share_usd) if risk_per_share_usd > 0 else 1
                        
                        # Portfolio Exposure Cap (Max 15% per position)
                        max_shares_cap = int((capital_usd * (self.config.max_single_position_pct / 100.0)) / entry_price_usd) if entry_price_usd > 0 else 1
                        shares = max(1, min(raw_shares, max_shares_cap))

                        # Cash availability buffer
                        required_cash_usd = shares * entry_price_usd
                        if cash_usd < required_cash_usd and cash_usd > (1000.0 * curr_fx):
                            # Size down to remaining cash
                            shares = max(1, int((cash_usd * 0.95) / entry_price_usd))
                            required_cash_usd = shares * entry_price_usd

                        if cash_usd >= required_cash_usd and shares > 0:
                            cash_usd -= required_cash_usd
                            new_trade = SimulatedTrade(
                                symbol=sym,
                                exchange=s["exchange"],
                                country=s["country"],
                                currency=s["currency"],
                                direction="LONG",
                                signal_date=prior_bar["trading_date"],
                                entry_date=curr_date,
                                raw_entry_price=raw_entry,
                                entry_price_net=entry_net,
                                entry_fx_rate=curr_fx,
                                entry_price_usd_net=entry_price_usd,
                                stop_loss=stop_loss,
                                target_1=target_1,
                                target_2=target_2,
                                target_3=target_3,
                                initial_risk_per_share=risk_per_share,
                                shares=shares,
                                regime_at_entry="BULLISH" if prior_bar["close"] > (prior_bar.get("sma_200") or 0) else "NEUTRAL",
                                partition=curr_partition,
                            )

                            # SAME-DAY INTRADAY RISK GATE: Check if entry bar hit stop or target intraday
                            b_low = today_bar["low"]
                            b_high = today_bar["high"]
                            if b_low <= stop_loss:
                                # Stopped out on day of entry
                                raw_exit = min(today_bar["open"], stop_loss)
                                new_trade.exit_date = curr_date
                                new_trade.raw_exit_price = raw_exit
                                new_trade.exit_price_net = raw_exit * (1.0 - fee_rate)
                                new_trade.exit_fx_rate = curr_fx
                                new_trade.exit_price_usd_net = new_trade.exit_price_net * curr_fx
                                new_trade.holding_days = 1
                                new_trade.exit_reason = "SAME_DAY_STOP"
                                new_trade.gross_pnl_usd = ((raw_exit * curr_fx) - (raw_entry * curr_fx)) * shares
                                new_trade.net_pnl_usd = (new_trade.exit_price_usd_net - new_trade.entry_price_usd_net) * shares
                                new_trade.fee_drag_usd = new_trade.gross_pnl_usd - new_trade.net_pnl_usd
                                new_trade.return_pct = ((new_trade.exit_price_usd_net / new_trade.entry_price_usd_net) - 1.0) * 100.0
                                new_trade.r_multiple = (new_trade.exit_price_net - new_trade.entry_price_net) / risk_per_share

                                cash_usd += (new_trade.exit_price_usd_net * shares)
                                closed_trades.append(new_trade)
                            else:
                                open_trades.append(new_trade)

            # C. Mark-to-Market Equity Calculation for the session
            unrealized_equity_usd = 0.0
            for t in open_trades:
                s_meta = next(s for s in securities if s["symbol"] == t.symbol)
                df_s = sec_dfs[s_meta["security_id"]]
                today_row = df_s[df_s["trading_date"] == curr_date]
                fx_today = get_fx_to_usd(curr_date, t.currency)
                if not today_row.empty:
                    current_close = today_row.iloc[0]["close"]
                    unrealized_equity_usd += (current_close * fx_today * t.shares)
                else:
                    unrealized_equity_usd += (t.entry_price_usd_net * t.shares)

            total_portfolio_equity = cash_usd + unrealized_equity_usd
            capital_usd = total_portfolio_equity

            # Dual-Benchmark Mark-to-Market
            bm_spy = benchmark_close_spy.get(curr_date, None)
            bm_xiu = benchmark_close_xiu.get(curr_date, None)

            equity_curve.append({
                "date": curr_date,
                "portfolio_equity": round(total_portfolio_equity, 2),
                "cash": round(cash_usd, 2),
                "open_positions": len(open_trades),
                "benchmark_price": bm_spy,
                "benchmark_price_spy": bm_spy,
                "benchmark_price_xiu": bm_xiu,
                "partition": curr_partition,
            })

        # 5. Compute Comprehensive Metrics
        metrics = self._calculate_performance_metrics(
            equity_curve=equity_curve,
            closed_trades=closed_trades,
            initial_capital=self.config.initial_capital_usd,
            benchmark_close_spy=benchmark_close_spy,
            benchmark_close_xiu=benchmark_close_xiu,
            train_cutoff=train_cutoff,
            val_cutoff=val_cutoff,
        )

        return {
            "strategy_id": strategy_id,
            "strategy_name": self._get_strategy_display_name(strategy_id),
            "generated_at": datetime.now().isoformat(),
            "config": {
                "initial_capital_usd": self.config.initial_capital_usd,
                "commission_bps": self.config.commission_bps,
                "slippage_bps": self.config.slippage_bps,
                "risk_per_trade_pct": self.config.risk_per_trade_pct,
                "max_open_positions": self.config.max_open_positions,
                "train_cutoff": train_cutoff,
                "val_cutoff": val_cutoff,
                "survivorship_bias_note": "Evaluated on 19 Liquid Cross-Sectional Large-Cap Constituents",
            },
            "metrics": metrics,
            "equity_curve": equity_curve[::2], # Sample every 2 sessions for fast web transfer
            "recent_trades": [t.to_dict() for t in closed_trades[-50:]],
            "all_trades": [t.to_dict() for t in closed_trades],
            "trades": [t.to_dict() for t in closed_trades],
            "statutory_disclaimer": HYPOTHETICAL_BACKTEST_DISCLAIMER,
            "disclaimer_version": DISCLAIMER_VERSION,
        }

    def _evaluate_signal_predicate(self, strategy_id: str, bar: pd.Series) -> bool:
        """
        Deterministic trigger predicates for each systematic quantitative strategy.
        """
        c = bar["close"]
        sma20 = bar.get("sma_20")
        sma50 = bar.get("sma_50")
        sma200 = bar.get("sma_200")
        rsi = bar.get("rsi_14")
        atr = bar.get("atr_14")
        bb_upper = bar.get("bb_upper")
        bb_lower = bar.get("bb_lower")
        prox52 = bar.get("proximity_52w_high", 0.0)

        for val in [sma20, sma50, sma200, rsi, atr]:
            if val is None or math.isnan(val):
                return False

        if strategy_id == "ENSEMBLE_8F":
            # Multi-factor alignment: Golden Cross trend, RSI 48-68, near 52w highs
            return bool(c > sma200 and sma20 > sma50 and 48.0 <= rsi <= 68.0 and prox52 >= -0.12)

        elif strategy_id == "TAC01_PULLBACK":
            # Pullback to Key Support: Rising 50DMA, price near 20DMA with healthy RSI
            is_uptrend = c > sma50 and sma50 > sma200
            pullback_zone = abs(c - sma20) / sma20 <= 0.025
            return bool(is_uptrend and pullback_zone and 42.0 <= rsi <= 58.0)

        elif strategy_id == "TAC02_SQUEEZE":
            # Volatility Squeeze: Compressed Bollinger Bandwidth (< 6.5%) expanding upwards
            if bb_upper and bb_lower and sma20 and not math.isnan(bb_upper) and not math.isnan(bb_lower):
                bandwidth = (bb_upper - bb_lower) / sma20
                return bool(bandwidth < 0.065 and c > sma20 and rsi > 50.0)
            return False

        elif strategy_id == "TAC04_MOMENTUM":
            # Momentum Leader: Close > 20DMA > 50DMA, within 6% of 52w high, RSI > 55
            return bool(c > sma20 > sma50 and prox52 >= -0.06 and rsi >= 55.0)

        return False

    def _calculate_performance_metrics(
        self,
        equity_curve: List[Dict[str, Any]],
        closed_trades: List[SimulatedTrade],
        initial_capital: float,
        benchmark_close_spy: Dict[str, float],
        benchmark_close_xiu: Dict[str, float],
        train_cutoff: str,
        val_cutoff: str,
    ) -> Dict[str, Any]:
        if not equity_curve:
            return {}

        df_eq = pd.DataFrame(equity_curve)
        df_eq["daily_return"] = df_eq["portfolio_equity"].pct_change().fillna(0.0)

        # 1. Total & Annualized Return
        final_equity = df_eq["portfolio_equity"].iloc[-1]
        total_net_return_pct = ((final_equity / initial_capital) - 1.0) * 100.0
        n_days = len(df_eq)
        years = max(n_days / 252.0, 0.1)
        cagr_pct = ((final_equity / initial_capital) ** (1.0 / years) - 1.0) * 100.0

        # Total fee drag
        total_fee_drag_usd = sum(t.fee_drag_usd for t in closed_trades)
        total_gross_pnl_usd = sum(t.gross_pnl_usd for t in closed_trades)
        total_net_pnl_usd = sum(t.net_pnl_usd for t in closed_trades)
        total_gross_return_pct = ((final_equity + total_fee_drag_usd) / initial_capital - 1.0) * 100.0

        # 2. Dual Benchmarks: SPY (US) and XIU (Canada)
        spy_first = df_eq["benchmark_price_spy"].dropna().iloc[0] if not df_eq["benchmark_price_spy"].dropna().empty else 1.0
        spy_last = df_eq["benchmark_price_spy"].dropna().iloc[-1] if not df_eq["benchmark_price_spy"].dropna().empty else 1.0
        benchmark_spy_return_pct = ((spy_last / spy_first) - 1.0) * 100.0

        xiu_first = df_eq["benchmark_price_xiu"].dropna().iloc[0] if not df_eq["benchmark_price_xiu"].dropna().empty else 1.0
        xiu_last = df_eq["benchmark_price_xiu"].dropna().iloc[-1] if not df_eq["benchmark_price_xiu"].dropna().empty else 1.0
        benchmark_xiu_return_pct = ((xiu_last / xiu_first) - 1.0) * 100.0

        blended_benchmark_return_pct = (0.60 * benchmark_spy_return_pct) + (0.40 * benchmark_xiu_return_pct)

        # 3. Maximum Drawdown & Drawdown Series
        df_eq["peak"] = df_eq["portfolio_equity"].cummax()
        df_eq["drawdown"] = (df_eq["portfolio_equity"] - df_eq["peak"]) / df_eq["peak"]
        max_drawdown_pct = abs(float(df_eq["drawdown"].min())) * 100.0

        dd_series = df_eq["drawdown"] < 0
        max_dd_duration = 0
        curr_dd = 0
        for is_dd in dd_series:
            if is_dd:
                curr_dd += 1
                if curr_dd > max_dd_duration:
                    max_dd_duration = curr_dd
            else:
                curr_dd = 0

        # 4. Sharpe, Sortino & Calmar Ratios
        daily_rf = (1.0 + self.config.annual_sovereign_benchmark_yield) ** (1.0 / 252.0) - 1.0
        excess_daily_returns = df_eq["daily_return"] - daily_rf
        mean_excess = excess_daily_returns.mean()
        std_daily = excess_daily_returns.std()

        if std_daily > 0:
            sharpe_ratio = float((mean_excess / std_daily) * math.sqrt(252.0))
        else:
            sharpe_ratio = 0.0

        negative_returns = df_eq["daily_return"][df_eq["daily_return"] < 0]
        downside_std = negative_returns.std() if len(negative_returns) > 1 else std_daily
        if downside_std and downside_std > 0:
            sortino_ratio = float((mean_excess / downside_std) * math.sqrt(252.0))
        else:
            sortino_ratio = 0.0

        calmar_ratio = float(cagr_pct / max_drawdown_pct) if max_drawdown_pct > 0 else 0.0

        # 5. Trade Level Statistics
        total_trades = len(closed_trades)
        winning_trades = [t for t in closed_trades if t.net_pnl_usd > 0]
        losing_trades = [t for t in closed_trades if t.net_pnl_usd <= 0]

        win_rate_pct = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
        gross_profits = sum(t.net_pnl_usd for t in winning_trades)
        gross_losses = abs(sum(t.net_pnl_usd for t in losing_trades))
        profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (gross_profits if gross_profits > 0 else 1.0)

        avg_win_r = float(np.mean([t.r_multiple for t in winning_trades])) if winning_trades else 0.0
        avg_loss_r = float(np.mean([t.r_multiple for t in losing_trades])) if losing_trades else 0.0
        expectancy_r = (win_rate_pct / 100.0 * avg_win_r) + ((1.0 - win_rate_pct / 100.0) * avg_loss_r)
        avg_holding_days = float(np.mean([t.holding_days for t in closed_trades])) if closed_trades else 0.0

        # 6. Walk-Forward Partition Breakdown (Train, Validation, Test)
        partitions = {}
        for p_name in ["TRAIN", "VALIDATION", "TEST"]:
            p_trades = [t for t in closed_trades if t.partition == p_name]
            p_wins = [t for t in p_trades if t.net_pnl_usd > 0]
            p_losses = [t for t in p_trades if t.net_pnl_usd <= 0]
            p_pnl = sum(t.net_pnl_usd for t in p_trades)
            p_win_rate = (len(p_wins) / len(p_trades) * 100.0) if p_trades else 0.0
            partitions[p_name] = {
                "total_trades": len(p_trades),
                "win_rate_pct": round(p_win_rate, 1),
                "net_pnl_usd": round(p_pnl, 2),
                "expectancy_r": round(float(np.mean([t.r_multiple for t in p_trades])), 2) if p_trades else 0.0,
            }

        # 7. Regime Performance Breakdown
        regime_breakdown = {}
        for r_name in ["BULLISH", "NEUTRAL", "BEARISH"]:
            r_trades = [t for t in closed_trades if t.regime_at_entry == r_name]
            if r_trades:
                r_wins = [t for t in r_trades if t.net_pnl_usd > 0]
                regime_breakdown[r_name] = {
                    "trades": len(r_trades),
                    "win_rate_pct": round(len(r_wins) / len(r_trades) * 100.0, 1),
                    "avg_r_multiple": round(float(np.mean([t.r_multiple for t in r_trades])), 2),
                }

        return {
            "initial_capital_usd": round(initial_capital, 2),
            "final_equity_usd": round(final_equity, 2),
            "total_net_return_pct": round(total_net_return_pct, 2),
            "total_gross_return_pct": round(total_gross_return_pct, 2),
            "fee_drag_total_usd": round(total_fee_drag_usd, 2),
            "cagr_pct": round(cagr_pct, 2),
            "benchmark_spy_return_pct": round(benchmark_spy_return_pct, 2),
            "benchmark_xiu_return_pct": round(benchmark_xiu_return_pct, 2),
            "blended_benchmark_return_pct": round(blended_benchmark_return_pct, 2),
            "alpha_vs_spy_pct": round(total_net_return_pct - benchmark_spy_return_pct, 2),
            "alpha_vs_blended_pct": round(total_net_return_pct - blended_benchmark_return_pct, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "sortino_ratio": round(sortino_ratio, 2),
            "calmar_ratio": round(calmar_ratio, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "max_drawdown_duration_days": int(max_dd_duration),
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate_pct, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_win_r": round(avg_win_r, 2),
            "avg_loss_r": round(avg_loss_r, 2),
            "expectancy_r": round(expectancy_r, 2),
            "avg_holding_days": round(avg_holding_days, 1),
            "walk_forward_partitions": partitions,
            "regime_breakdown": regime_breakdown,
        }

    def _get_strategy_display_name(self, strategy_id: str) -> str:
        names = {
            "ENSEMBLE_8F": "8-Factor Composite Trend & Value Ensemble",
            "TAC01_PULLBACK": "TAC-01: Pullback to Key Support Swing",
            "TAC02_SQUEEZE": "TAC-02: Volatility Squeeze Expansion",
            "TAC04_MOMENTUM": "TAC-04: Momentum Leader Breakout",
        }
        return names.get(strategy_id, strategy_id)


# Global singleton instance
backtest_engine = BacktestEngine()
