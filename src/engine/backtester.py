"""
Event-Driven Walk-Forward Backtesting Engine
US + Canada Market Intelligence Platform

Strict anti-bias controls:
1. No look-ahead bias: Signals generated on bar T close; orders executed on bar T+1 Open.
2. Realistic transaction costs: 5 bps commission + 5 bps slippage per side (10 bps per side, 20 bps round-trip).
3. Walk-Forward / Out-of-Sample Partitioning:
   - TRAIN: 60% of timeline (hypothesis & parameter calibration)
   - VALIDATION: 20% of timeline (hyperparameter tuning)
   - TEST: 20% of timeline (untouched out-of-sample verification)
4. Comprehensive metric calculation: CAGR, Sharpe, Sortino, Calmar, Max Drawdown, Win Rate, Profit Factor, Expectancy.
5. Mandatory regulatory disclosure: Statutory CSA 31-369 & SEC Rule 206(4)-1 hypothetical performance warnings.
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
    initial_capital: float = 100000.0
    commission_bps: float = 5.0     # 0.05%
    slippage_bps: float = 5.0       # 0.05%
    risk_per_trade_pct: float = 1.0 # 1.0% equity risk per trade (1R)
    max_open_positions: int = 8
    split_train_pct: float = 0.60
    split_val_pct: float = 0.20
    split_test_pct: float = 0.20
    annual_sovereign_benchmark_yield: float = 0.035 # 3.5% annualized baseline yield benchmark


@dataclass
class SimulatedTrade:
    symbol: str
    exchange: str
    country: str
    direction: str
    signal_date: str
    entry_date: str
    entry_price: float
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    target_3: float = 0.0
    initial_risk_per_share: float = 0.0
    shares: int = 0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
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
            "direction": str(self.direction),
            "signal_date": str(self.signal_date),
            "entry_date": str(self.entry_date),
            "entry_price": round(float(self.entry_price), 2),
            "exit_date": str(self.exit_date) if self.exit_date else None,
            "exit_price": round(float(self.exit_price), 2) if self.exit_price else None,
            "stop_loss": round(float(self.stop_loss), 2),
            "target_1": round(float(self.target_1), 2),
            "target_2": round(float(self.target_2), 2),
            "target_3": round(float(self.target_3), 2),
            "shares": int(self.shares),
            "gross_pnl": round(float(self.gross_pnl), 2),
            "net_pnl": round(float(self.net_pnl), 2),
            "return_pct": round(float(self.return_pct), 2),
            "r_multiple": round(float(self.r_multiple), 2),
            "holding_days": int(self.holding_days),
            "exit_reason": str(self.exit_reason),
            "regime_at_entry": str(self.regime_at_entry),
            "partition": str(self.partition),
        }


class BacktestEngine:
    """
    Production-grade event-driven backtesting engine for quantitative market strategies.
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()

    def run_strategy_backtest(
        self,
        strategy_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes a complete walk-forward backtest for the specified strategy.
        Supported strategies:
        - 'ENSEMBLE_8F': Multi-factor composite scoring model (Score >= 60 in bullish regimes)
        - 'TAC01_PULLBACK': Pullback to rising key moving averages (SMA20/50 + RSI digestion)
        - 'TAC02_SQUEEZE': Volatility squeeze bandwidth compression breakout
        - 'TAC04_MOMENTUM': Momentum leaders 52-week breakout continuation
        """
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

        # 3. Benchmark Data for Alpha/Beta tracking (SPY for US, XIU for CA)
        benchmark_close: Dict[str, float] = {}
        spy_sec = next((s for s in securities if s["symbol"] == "SPY"), None)
        if spy_sec and spy_sec["security_id"] in sec_dfs:
            spy_df = sec_dfs[spy_sec["security_id"]]
            for _, row in spy_df.iterrows():
                benchmark_close[row["trading_date"]] = row["close"]

        # 4. Simulation State
        capital = self.config.initial_capital
        cash = capital
        open_trades: List[SimulatedTrade] = []
        closed_trades: List[SimulatedTrade] = []
        equity_curve: List[Dict[str, Any]] = []

        # Fee factors
        fee_rate = (self.config.commission_bps + self.config.slippage_bps) / 10000.0

        # Step through every trading session chronologically
        for t_idx in range(50, len(timeline)):
            curr_date = timeline[t_idx]
            curr_partition = get_partition(curr_date)
            prev_date = timeline[t_idx - 1]

            # A. Update Open Positions & Check Stop/Target Execution on Today's Bar
            still_open: List[SimulatedTrade] = []
            daily_realized_pnl = 0.0

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
                exit_price: Optional[float] = None
                exit_reason: Optional[str] = None

                # Breakeven Stop Trailing Rule: Once Target 1 is hit, trail stop to breakeven
                if b_high >= trade.target_1 and trade.stop_loss < trade.entry_price:
                    trade.stop_loss = trade.entry_price

                # Check Stop Loss Trigger (hits Low)
                if b_low <= trade.stop_loss:
                    # Gapped below stop -> fill at Open; else fill at Stop Loss
                    exit_price = min(b_open, trade.stop_loss) * (1.0 - fee_rate)
                    exit_reason = "STOP_LOSS" if trade.stop_loss < trade.entry_price else "BREAKEVEN_STOP"

                # Check Profit Target 2 Trigger (hits High)
                elif b_high >= trade.target_2:
                    exit_price = trade.target_2 * (1.0 - fee_rate)
                    exit_reason = "TARGET_2"

                # Check Profit Target 1 Trigger after multi-session hold
                elif b_high >= trade.target_1 and trade.holding_days >= 6:
                    exit_price = trade.target_1 * (1.0 - fee_rate)
                    exit_reason = "TARGET_1"

                # Max Holding Horizon Rule (20 sessions max)
                elif trade.holding_days >= 20:
                    exit_price = b_close * (1.0 - fee_rate)
                    exit_reason = "TIME_EXPIRY"

                # Execute Exit if triggered
                if exit_price and exit_reason:
                    trade.exit_date = curr_date
                    trade.exit_price = exit_price
                    trade.exit_reason = exit_reason

                    trade.gross_pnl = (exit_price - trade.entry_price) * trade.shares
                    trade.net_pnl = trade.gross_pnl
                    trade.return_pct = ((exit_price / trade.entry_price) - 1.0) * 100.0

                    if trade.initial_risk_per_share > 0:
                        trade.r_multiple = (exit_price - trade.entry_price) / trade.initial_risk_per_share
                    else:
                        trade.r_multiple = 0.0

                    cash += (exit_price * trade.shares)
                    daily_realized_pnl += trade.net_pnl
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
                    # Skip if already holding a position in this security
                    if any(t.symbol == sym for t in open_trades):
                        continue

                    s_id = s["security_id"]
                    if s_id not in sec_dfs:
                        continue

                    df_s = sec_dfs[s_id]
                    # We need the prior bar (signal generated at close)
                    idx_today_series = df_s.index[df_s["trading_date"] == curr_date]
                    if len(idx_today_series) == 0 or idx_today_series[0] == 0:
                        continue
                    
                    row_idx = idx_today_series[0]
                    prior_bar = df_s.iloc[row_idx - 1]
                    today_bar = df_s.iloc[row_idx]

                    # Verify signal condition on prior_bar
                    signal_fired = self._evaluate_signal_predicate(strategy_id, prior_bar)
                    if signal_fired:
                        # Entry executes at Today's Open with slippage & commission
                        entry_price = today_bar["open"] * (1.0 + fee_rate)
                        atr = prior_bar.get("atr_14", entry_price * 0.02)
                        if not atr or math.isnan(atr) or atr <= 0:
                            atr = entry_price * 0.02

                        # Structural stop at 1.5 ATR below entry
                        stop_loss = round(entry_price - (1.5 * atr), 2)
                        risk_per_share = entry_price - stop_loss
                        if risk_per_share <= 0:
                            continue

                        # Multi-stage targets
                        target_1 = round(entry_price + (1.6 * risk_per_share), 2)
                        target_2 = round(entry_price + (2.6 * risk_per_share), 2)
                        target_3 = round(entry_price + (4.0 * risk_per_share), 2)

                        # Position Sizing: 1% equity risk / risk_per_share
                        max_risk_amount = capital * (self.config.risk_per_trade_pct / 100.0)
                        raw_shares = int(max_risk_amount / risk_per_share)
                        # Cap at 15% of portfolio capital
                        max_shares_cap = int((capital * 0.15) / entry_price)
                        shares = max(1, min(raw_shares, max_shares_cap))

                        required_capital = shares * entry_price
                        if cash >= required_capital:
                            cash -= required_capital
                            new_trade = SimulatedTrade(
                                symbol=sym,
                                exchange=s["exchange"],
                                country=s["country"],
                                direction="LONG",
                                signal_date=prior_bar["trading_date"],
                                entry_date=curr_date,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                target_1=target_1,
                                target_2=target_2,
                                target_3=target_3,
                                initial_risk_per_share=risk_per_share,
                                shares=shares,
                                regime_at_entry="BULLISH" if prior_bar["close"] > (prior_bar.get("sma_200") or 0) else "NEUTRAL",
                                partition=curr_partition,
                            )
                            open_trades.append(new_trade)

            # C. Mark-to-Market Equity Calculation for the session
            unrealized_equity = 0.0
            for t in open_trades:
                s_meta = next(s for s in securities if s["symbol"] == t.symbol)
                df_s = sec_dfs[s_meta["security_id"]]
                today_row = df_s[df_s["trading_date"] == curr_date]
                if not today_row.empty:
                    current_close = today_row.iloc[0]["close"]
                    unrealized_equity += (current_close * t.shares)
                else:
                    unrealized_equity += (t.entry_price * t.shares)

            total_portfolio_equity = cash + unrealized_equity
            capital = total_portfolio_equity

            # Benchmark mark-to-market comparison
            bm_price = benchmark_close.get(curr_date, None)
            equity_curve.append({
                "date": curr_date,
                "portfolio_equity": round(total_portfolio_equity, 2),
                "cash": round(cash, 2),
                "open_positions": len(open_trades),
                "benchmark_price": bm_price,
                "partition": curr_partition,
            })

        # 5. Compute Comprehensive Metrics
        metrics = self._calculate_performance_metrics(
            equity_curve=equity_curve,
            closed_trades=closed_trades,
            initial_capital=self.config.initial_capital,
            benchmark_close=benchmark_close,
            train_cutoff=train_cutoff,
            val_cutoff=val_cutoff,
        )

        return {
            "strategy_id": strategy_id,
            "strategy_name": self._get_strategy_display_name(strategy_id),
            "generated_at": datetime.now().isoformat(),
            "config": {
                "initial_capital": self.config.initial_capital,
                "commission_bps": self.config.commission_bps,
                "slippage_bps": self.config.slippage_bps,
                "risk_per_trade_pct": self.config.risk_per_trade_pct,
                "max_open_positions": self.config.max_open_positions,
                "train_cutoff": train_cutoff,
                "val_cutoff": val_cutoff,
            },
            "metrics": metrics,
            "equity_curve": equity_curve[::2], # Sample every 2 sessions for fast web transfer
            "recent_trades": [t.to_dict() for t in closed_trades[-20:]],
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

        # Check required fields are present and not NaN
        for val in [sma20, sma50, sma200, rsi, atr]:
            if val is None or math.isnan(val):
                return False

        if strategy_id == "ENSEMBLE_8F":
            # Multi-factor alignment: Above 200DMA, SMA20 > SMA50, RSI 48-68, within 12% of 52w high
            return bool(c > sma200 and sma20 > sma50 and 48.0 <= rsi <= 68.0 and prox52 >= -0.12)

        elif strategy_id == "TAC01_PULLBACK":
            # Pullback to Key Support: Rising 50DMA, price within 2.5% of 20DMA with healthy RSI
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
        benchmark_close: Dict[str, float],
        train_cutoff: str,
        val_cutoff: str,
    ) -> Dict[str, Any]:
        """
        Computes the complete quantitative metric set across walk-forward partitions.
        """
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

        # 2. Benchmark Comparison (SPY)
        bm_first = df_eq["benchmark_price"].dropna().iloc[0] if not df_eq["benchmark_price"].dropna().empty else 1.0
        bm_last = df_eq["benchmark_price"].dropna().iloc[-1] if not df_eq["benchmark_price"].dropna().empty else 1.0
        benchmark_return_pct = ((bm_last / bm_first) - 1.0) * 100.0

        # 3. Maximum Drawdown & Drawdown Series
        df_eq["peak"] = df_eq["portfolio_equity"].cummax()
        df_eq["drawdown"] = (df_eq["portfolio_equity"] - df_eq["peak"]) / df_eq["peak"]
        max_drawdown_pct = abs(float(df_eq["drawdown"].min())) * 100.0

        # Calculate Max Drawdown Duration (sessions)
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
        std_daily = df_eq["daily_return"].std()

        if std_daily > 0:
            sharpe_ratio = float((mean_excess / std_daily) * math.sqrt(252.0))
        else:
            sharpe_ratio = 0.0

        # Downside Deviation for Sortino
        negative_returns = df_eq["daily_return"][df_eq["daily_return"] < 0]
        downside_std = negative_returns.std() if len(negative_returns) > 1 else std_daily
        if downside_std and downside_std > 0:
            sortino_ratio = float((mean_excess / downside_std) * math.sqrt(252.0))
        else:
            sortino_ratio = 0.0

        calmar_ratio = float(cagr_pct / max_drawdown_pct) if max_drawdown_pct > 0 else 0.0

        # 5. Trade Level Statistics
        total_trades = len(closed_trades)
        winning_trades = [t for t in closed_trades if t.net_pnl > 0]
        losing_trades = [t for t in closed_trades if t.net_pnl <= 0]

        win_rate_pct = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
        gross_profits = sum(t.net_pnl for t in winning_trades)
        gross_losses = abs(sum(t.net_pnl for t in losing_trades))
        profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (gross_profits if gross_profits > 0 else 1.0)

        avg_win_r = float(np.mean([t.r_multiple for t in winning_trades])) if winning_trades else 0.0
        avg_loss_r = float(np.mean([t.r_multiple for t in losing_trades])) if losing_trades else 0.0
        expectancy_r = (win_rate_pct / 100.0 * avg_win_r) + ((1.0 - win_rate_pct / 100.0) * avg_loss_r)
        avg_holding_days = float(np.mean([t.holding_days for t in closed_trades])) if closed_trades else 0.0

        # 6. Walk-Forward Partition Breakdown (Train, Validation, Test)
        partitions = {}
        for p_name in ["TRAIN", "VALIDATION", "TEST"]:
            p_trades = [t for t in closed_trades if t.partition == p_name]
            p_wins = [t for t in p_trades if t.net_pnl > 0]
            p_losses = [t for t in p_trades if t.net_pnl <= 0]
            p_pnl = sum(t.net_pnl for t in p_trades)
            p_win_rate = (len(p_wins) / len(p_trades) * 100.0) if p_trades else 0.0
            partitions[p_name] = {
                "total_trades": len(p_trades),
                "win_rate_pct": round(p_win_rate, 1),
                "net_pnl": round(p_pnl, 2),
                "expectancy_r": round(float(np.mean([t.r_multiple for t in p_trades])), 2) if p_trades else 0.0,
            }

        # 7. Regime Performance Breakdown
        regime_breakdown = {}
        for r_name in ["BULLISH", "NEUTRAL", "BEARISH"]:
            r_trades = [t for t in closed_trades if t.regime_at_entry == r_name]
            if r_trades:
                r_wins = [t for t in r_trades if t.net_pnl > 0]
                regime_breakdown[r_name] = {
                    "trades": len(r_trades),
                    "win_rate_pct": round(len(r_wins) / len(r_trades) * 100.0, 1),
                    "avg_r_multiple": round(float(np.mean([t.r_multiple for t in r_trades])), 2),
                }

        return {
            "initial_capital": round(initial_capital, 2),
            "final_equity": round(final_equity, 2),
            "total_net_return_pct": round(total_net_return_pct, 2),
            "cagr_pct": round(cagr_pct, 2),
            "benchmark_return_pct": round(benchmark_return_pct, 2),
            "alpha_pct": round(total_net_return_pct - benchmark_return_pct, 2),
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
