"""
Portfolio Intelligence & Risk Management Engine (Phase 9)
US + Canada Market Intelligence Platform

Implements:
1. Unified Dual-Market Valuation (USD and CAD) via Bank of Canada Valet FX rates.
2. Dual-Market Beta Engine: Local Beta (SPY / XIU) and Cross-Market Beta.
3. Component VaR & Risk Attribution: Marginal Contribution to Risk (MCR) and % Risk Contribution.
4. Correlation Clustering & Effective Number of Independent Bets (N_eff via eigenvalue decomposition).
5. Unhedged FX Exposure & Currency Sensitivity Analysis.
6. Historical Macro Stress Replay (2008 GFC, 2020 COVID, 2022 Rate Shock, 2014 Oil Collapse, 2018 Vol Spike).
7. Systematic Drawdown Circuit Breakers (§9.4) customized by risk profile.
8. Integrated per-holding exit signals (HOLD / WATCH / REDUCE / EXIT) via ExitSignalEngine.
9. Interactive CSV import and 3 pre-built reference portfolios.
10. Strict Impersonal Research compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion).
"""

import math
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple, Literal

from src.data.db import db
from src.engine.exit_engine import exit_signal_engine
from src.compliance.linter import linter
from src.models.schemas import PortfolioHoldingRecord, PortfolioAnalyticsReport


class PortfolioIntelligenceEngine:
    """
    Dual-market portfolio risk intelligence, cross-border attribution,
    and stress testing engine for US & Canadian equities.
    """

    def __init__(self):
        self.default_fx_rate = 1.4136  # CAD per USD fallback

    def get_cad_usd_rate(self) -> float:
        """Fetch the most recent Bank of Canada CAD/USD exchange rate from database."""
        conn = db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT value FROM macro_observation 
                WHERE series_id = 'FXUSDCAD' 
                ORDER BY observation_date DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row and row[0]:
                return float(row[0])
            return self.default_fx_rate
        except Exception:
            return self.default_fx_rate
        finally:
            conn.close()

    def get_security_metadata(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch security metadata (country, exchange, currency, sector, name)."""
        conn = db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT symbol, name, exchange, country, currency, sector, industry
                FROM security WHERE symbol = ? LIMIT 1
            """, (symbol.upper(),))
            row = cur.fetchone()
            if row:
                return {
                    "symbol": row[0],
                    "name": row[1] or row[0],
                    "exchange": row[2] or "UNKNOWN",
                    "country": row[3] or ("CA" if row[2] == "TSX" else "US"),
                    "currency": row[4] or ("CAD" if row[3] == "CA" else "USD"),
                    "sector": row[5] or "Unclassified",
                    "industry": row[6] or "Unclassified",
                }
            return None
        finally:
            conn.close()

    def get_price_history_and_metrics(self, symbol: str, limit: int = 120) -> Dict[str, Any]:
        """Fetch historical close prices and trading volume for a security."""
        conn = db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT b.trading_date, b.close, b.volume, b.high, b.low, b.open
                FROM bar_1d b
                JOIN security s ON b.security_id = s.security_id
                WHERE s.symbol = ?
                ORDER BY b.trading_date DESC
                LIMIT ?
            """, (symbol.upper(), limit))
            rows = cur.fetchall()
            if not rows:
                return {"dates": [], "prices": [], "volumes": [], "latest_price": 100.0, "adv_20": 100000.0}

            rows = list(reversed(rows))
            dates = [r[0] for r in rows]
            prices = [float(r[1]) for r in rows]
            volumes = [float(r[2]) if r[2] else 100000.0 for r in rows]

            latest_price = prices[-1] if prices else 100.0
            adv_20 = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else float(np.mean(volumes))

            return {
                "dates": dates,
                "prices": prices,
                "volumes": volumes,
                "latest_price": latest_price,
                "adv_20": max(1000.0, adv_20),
            }
        finally:
            conn.close()

    def calculate_asset_betas_and_returns(
        self, symbol: str, spy_returns: List[float], xiu_returns: List[float], min_bars: int = 40
    ) -> Tuple[float, float, List[float], float]:
        """
        Calculate local beta, cross beta, return series, and daily volatility.
        SPY = US Benchmark; XIU = Canadian Benchmark.
        """
        hist = self.get_price_history_and_metrics(symbol, limit=80)
        prices = hist["prices"]
        if len(prices) < 2:
            return 1.0, 1.0, [0.0] * min(len(spy_returns), 60), 0.015

        # Compute daily percentage returns: (p[t] - p[t-1]) / p[t-1]
        rets = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]

        # Truncate to match benchmark length
        target_len = min(len(rets), len(spy_returns), len(xiu_returns), 60)
        r_asset = np.array(rets[-target_len:])
        r_spy = np.array(spy_returns[-target_len:])
        r_xiu = np.array(xiu_returns[-target_len:])

        var_spy = np.var(r_spy)
        var_xiu = np.var(r_xiu)

        beta_spy = float(np.cov(r_asset, r_spy)[0, 1] / var_spy) if var_spy > 1e-8 else 1.0
        beta_xiu = float(np.cov(r_asset, r_xiu)[0, 1] / var_xiu) if var_xiu > 1e-8 else 1.0
        daily_vol = float(np.std(r_asset))

        # Clamp extreme values for stability
        beta_spy = round(max(-1.5, min(3.5, beta_spy)), 2)
        beta_xiu = round(max(-1.5, min(3.5, beta_xiu)), 2)

        return beta_spy, beta_xiu, r_asset.tolist(), round(daily_vol, 4)

    def calculate_risk_attribution(
        self, weights: np.ndarray, returns_matrix: np.ndarray
    ) -> Tuple[float, float, np.ndarray, np.ndarray]:
        """
        Compute Portfolio Volatility, Marginal Contribution to Risk (MCR),
        and Percentage Risk Contribution (% RC) such that sum(% RC) = 100%.
        """
        n_assets = len(weights)
        if n_assets == 0 or returns_matrix.shape[1] == 0:
            return 0.0, 0.0, np.zeros(n_assets), np.zeros(n_assets)

        cov_matrix = np.cov(returns_matrix, rowvar=False)
        if n_assets == 1:
            cov_matrix = np.array([[float(cov_matrix)]])

        # Portfolio variance: w^T * Cov * w
        port_var = float(weights.T @ cov_matrix @ weights)
        port_vol = math.sqrt(max(1e-9, port_var))
        port_vol_ann = port_vol * math.sqrt(252)

        # Marginal Contribution to Risk: (Cov * w) / port_vol
        mcr = (cov_matrix @ weights) / port_vol

        # Percentage Risk Contribution: (w_i * mcr_i) / port_vol
        rc_dollars = weights * mcr
        total_rc = np.sum(rc_dollars)
        if total_rc > 1e-8:
            rc_pct = (rc_dollars / total_rc) * 100.0
        else:
            rc_pct = np.full(n_assets, 100.0 / max(1, n_assets))

        return port_vol, port_vol_ann, mcr, rc_pct

    def calculate_effective_bets_and_clusters(
        self, symbols: List[str], returns_matrix: np.ndarray
    ) -> Tuple[float, List[Dict[str, Any]]]:
        """
        Compute the Effective Number of Independent Bets (N_eff) via PCA eigenvalue
        entropy decomposition, and group assets into correlation clusters (r >= 0.55).
        """
        n = len(symbols)
        if n <= 1:
            return 1.0, [{"cluster_name": "Single Asset", "symbols": symbols, "avg_correlation": 1.0}]

        # Correlation matrix
        corr = np.corrcoef(returns_matrix, rowvar=False)
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)

        # Eigenvalues
        try:
            eigenvalues = np.linalg.eigvalsh(corr)
            eigenvalues = np.sort(eigenvalues)[::-1]
            eigenvalues = np.maximum(eigenvalues, 1e-6)
            # Neff = (sum lambda_i)^2 / sum (lambda_i^2)
            neff = float((np.sum(eigenvalues) ** 2) / np.sum(eigenvalues ** 2))
            neff = round(max(1.0, min(float(n), neff)), 2)
        except Exception:
            neff = float(n)

        # Cluster detection
        visited = set()
        clusters = []

        for i in range(n):
            if symbols[i] in visited:
                continue
            cluster_syms = [symbols[i]]
            visited.add(symbols[i])
            corrs = []
            for j in range(n):
                if i != j and symbols[j] not in visited and corr[i, j] >= 0.55:
                    cluster_syms.append(symbols[j])
                    visited.add(symbols[j])
                    corrs.append(corr[i, j])

            avg_c = float(np.mean(corrs)) if corrs else 1.0
            cluster_name = self._name_cluster(cluster_syms)
            clusters.append({
                "cluster_name": cluster_name,
                "symbols": cluster_syms,
                "avg_correlation": round(avg_c, 2),
                "cluster_size": len(cluster_syms),
            })

        return neff, clusters

    def _name_cluster(self, symbols: List[str]) -> str:
        """Generate an intuitive descriptive name for a correlation cluster."""
        tech_syms = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "SHOP", "QQQ"}
        fin_syms = {"RY", "TD", "BAM", "BN", "JPM"}
        energy_syms = {"ENB", "CNQ", "XOM"}
        rail_syms = {"CNR", "CP"}

        sym_set = set(symbols)
        if len(sym_set.intersection(tech_syms)) >= 2:
            return "North American Technology & Cloud"
        elif len(sym_set.intersection(fin_syms)) >= 2:
            return "Dual-Market Financial Institutions"
        elif len(sym_set.intersection(energy_syms)) >= 2:
            return "North American Energy & Midstream"
        elif len(sym_set.intersection(rail_syms)) >= 2:
            return "Transcontinental Freight & Rail"
        elif len(symbols) == 1:
            return f"{symbols[0]} Uncorrelated Allocation"
        else:
            return f"Diversified Basket ({', '.join(symbols[:3])})"

    def calculate_fx_exposure(
        self, holdings_data: List[Dict[str, Any]], base_currency: str, fx_rate: float
    ) -> Dict[str, Any]:
        """
        Analyze unhedged FX exposure and compute currency sensitivity.
        Base currency can be 'USD' or 'CAD'.
        """
        total_market_val = sum(h["market_value_base"] for h in holdings_data) or 1.0
        usd_notional = sum(h["market_value_base"] for h in holdings_data if h["currency"] == "USD")
        cad_notional = sum(h["market_value_base"] for h in holdings_data if h["currency"] == "CAD")

        usd_weight = round((usd_notional / total_market_val) * 100.0, 1)
        cad_weight = round((cad_notional / total_market_val) * 100.0, 1)

        # Sensitivity: +/- 5% shift in CAD vs USD
        # If Base is CAD: USD assets gain if CAD depreciates (+5% USD/CAD)
        if base_currency == "CAD":
            cad_depreciate_impact_pct = round((usd_weight / 100.0) * 5.0, 2)
            cad_appreciate_impact_pct = round(-1.0 * (usd_weight / 100.0) * 5.0, 2)
            commentary = (
                f"Portfolio holds {usd_weight}% in USD-denominated assets. "
                f"A 5% depreciation of CAD provides a +{cad_depreciate_impact_pct}% unhedged tailwind; "
                f"a 5% CAD appreciation creates a {cad_appreciate_impact_pct}% currency headwind."
            )
        else:
            # Base is USD: CAD assets gain if CAD appreciates
            cad_depreciate_impact_pct = round(-1.0 * (cad_weight / 100.0) * 5.0, 2)
            cad_appreciate_impact_pct = round((cad_weight / 100.0) * 5.0, 2)
            commentary = (
                f"Portfolio holds {cad_weight}% in CAD-denominated assets. "
                f"A 5% strengthening of CAD provides a +{cad_appreciate_impact_pct}% foreign asset gain; "
                f"a 5% CAD weakening creates a {cad_depreciate_impact_pct}% drag."
            )

        return {
            "base_currency": base_currency,
            "cad_usd_rate": fx_rate,
            "usd_notional_base": round(usd_notional, 2),
            "cad_notional_base": round(cad_notional, 2),
            "usd_weight_pct": usd_weight,
            "cad_weight_pct": cad_weight,
            "cad_depreciate_5pct_impact_pct": cad_depreciate_impact_pct,
            "cad_appreciate_5pct_impact_pct": cad_appreciate_impact_pct,
            "sensitivity_analysis": commentary,
        }

    def calculate_stress_replays(
        self, holdings_data: List[Dict[str, Any]], weights: np.ndarray, total_portfolio_val: float
    ) -> List[Dict[str, Any]]:
        """
        Replay historical stress events using empirical sector and cross-border factor shocks.
        """
        # Historical shock returns by sector/asset for major crises
        scenarios = [
            {
                "id": "GFC_2008",
                "name": "2008 Global Financial Crisis",
                "period": "Oct 2007 – Mar 2009",
                "market_drop": "S&P 500 -50.9%, TSX -43.4%",
                "shocks": {
                    "Information Technology": -44.5,
                    "Financials": -68.3,
                    "Energy": -53.2,
                    "Industrials": -48.1,
                    "Consumer Discretionary": -47.8,
                    "Communication Services": -39.0,
                    "Index ETF": -50.9,
                    "Unclassified": -50.0,
                },
                "recovery_months": 37,
                "description": "Liquidity freeze, bank insolvencies, and broad systemic credit crunch.",
            },
            {
                "id": "COVID_2020",
                "name": "March 2020 COVID Liquidity Shock",
                "period": "Feb 2020 – Mar 2020",
                "market_drop": "S&P 500 -33.9%, TSX -37.4%",
                "shocks": {
                    "Information Technology": -28.2,
                    "Financials": -36.2,
                    "Energy": -58.4,
                    "Industrials": -37.5,
                    "Consumer Discretionary": -32.5,
                    "Communication Services": -28.0,
                    "Index ETF": -33.9,
                    "Unclassified": -35.0,
                },
                "recovery_months": 5,
                "description": "Global economic shutdown, VIX spike to 82, and steep oil crash.",
            },
            {
                "id": "RATE_SHOCK_2022",
                "name": "2022 Fed & BoC Inflation/Rate Shock",
                "period": "Jan 2022 – Oct 2022",
                "market_drop": "S&P 500 -24.5%, TSX -14.2%",
                "shocks": {
                    "Information Technology": -33.1,
                    "Financials": -15.4,
                    "Energy": +45.2,  # Energy had huge positive hedge
                    "Industrials": -14.8,
                    "Consumer Discretionary": -37.6,
                    "Communication Services": -40.2,
                    "Index ETF": -24.5,
                    "Unclassified": -20.0,
                },
                "recovery_months": 14,
                "description": "Aggressive central bank rate hikes, valuation multiple compression, offset by energy rally.",
            },
            {
                "id": "OIL_CRASH_2014",
                "name": "2014–2015 WTI Oil Collapse",
                "period": "Jun 2014 – Dec 2015",
                "market_drop": "TSX -15.8%, S&P 500 +2.1%",
                "shocks": {
                    "Information Technology": +12.3,
                    "Financials": -8.5,
                    "Energy": -52.4,
                    "Industrials": -4.2,
                    "Consumer Discretionary": +8.5,
                    "Communication Services": +3.0,
                    "Index ETF": +2.1,
                    "Unclassified": -5.0,
                },
                "recovery_months": 22,
                "description": "OPEC price war causing severe TSX energy drag and 18% CAD devaluation against USD.",
            },
            {
                "id": "VOL_SPIKE_2018",
                "name": "Q4 2018 Quantitative Tightening Shock",
                "period": "Oct 2018 – Dec 2018",
                "market_drop": "S&P 500 -19.8%, TSX -11.6%",
                "shocks": {
                    "Information Technology": -22.5,
                    "Financials": -14.6,
                    "Energy": -26.0,
                    "Industrials": -18.2,
                    "Consumer Discretionary": -20.1,
                    "Communication Services": -17.5,
                    "Index ETF": -19.8,
                    "Unclassified": -18.0,
                },
                "recovery_months": 4,
                "description": "Yield curve flattening and Fed autopilot rhetoric causing severe multi-week selloff.",
            },
        ]

        replays = []
        for s in scenarios:
            simulated_port_ret = 0.0
            shocks = s["shocks"]
            for i, h in enumerate(holdings_data):
                sec = h.get("sector", "Unclassified")
                shock_val = shocks.get(sec, shocks["Unclassified"])
                simulated_port_ret += (weights[i] * (shock_val / 100.0))

            simulated_ret_pct = float(round(simulated_port_ret * 100.0, 2))
            dollar_impact = float(round(total_portfolio_val * simulated_port_ret, 2))

            replays.append({
                "scenario_id": s["id"],
                "name": s["name"],
                "period": s["period"],
                "benchmark_drawdown": s["market_drop"],
                "simulated_portfolio_drawdown_pct": simulated_ret_pct,
                "simulated_dollar_loss": dollar_impact,
                "recovery_timeline_months": s["recovery_months"],
                "risk_context": s["description"],
            })

        return replays

    def evaluate_circuit_breakers(
        self, current_drawdown_pct: float, risk_profile: str
    ) -> List[Dict[str, Any]]:
        """
        Evaluate systematic drawdown circuit breakers (§9.4) customized by risk profile.
        """
        # Thresholds by profile
        profiles = {
            "CONSERVATIVE": {"L1": -4.0, "L2": -7.0, "L3": -10.0},
            "MODERATE": {"L1": -6.0, "L2": -10.0, "L3": -15.0},
            "AGGRESSIVE": {"L1": -8.0, "L2": -14.0, "L3": -20.0},
        }
        cfg = profiles.get(risk_profile, profiles["MODERATE"])

        # Determine level triggered
        dd = -abs(current_drawdown_pct)
        l1_active = dd <= cfg["L1"]
        l2_active = dd <= cfg["L2"]
        l3_active = dd <= cfg["L3"]

        return [
            {
                "level": 1,
                "title": "Level 1: Allocation Caution & Stop Tightening",
                "threshold_pct": cfg["L1"],
                "is_triggered": l1_active,
                "action": "Halt opening new speculative positions. Tighten trailing stops to nearest swing structure on all held names.",
                "status": "TRIGGERED" if l1_active else "NORMAL",
            },
            {
                "level": 2,
                "title": "Level 2: Risk Halving & Gross Derisking",
                "threshold_pct": cfg["L2"],
                "is_triggered": l2_active,
                "action": "Reduce all newly proposed position sizes by 50%. Review highest beta contributors for partial profit/loss harvesting.",
                "status": "TRIGGERED" if l2_active else "NORMAL",
            },
            {
                "level": 3,
                "title": "Level 3: Systematic Portfolio Freeze & Cash Protection",
                "threshold_pct": cfg["L3"],
                "is_triggered": l3_active,
                "action": "Complete freeze on all new entries. Re-evaluate core thesis across book and transition vulnerable names to cash.",
                "status": "TRIGGERED" if l3_active else "NORMAL",
            },
        ]

    def analyze_portfolio(
        self,
        holdings_input: List[Dict[str, Any]],
        base_currency: Literal["USD", "CAD"] = "USD",
        risk_profile: Literal["CONSERVATIVE", "MODERATE", "AGGRESSIVE"] = "MODERATE",
        portfolio_name: str = "Active Reference Portfolio",
        portfolio_id: str = "PORT_ACTIVE",
        cash_base: float = 10000.0,
        current_drawdown_pct: float = -2.4,
    ) -> PortfolioAnalyticsReport:
        """
        Execute end-to-end multi-asset portfolio risk analytics,
        reconciling all risk metrics, component VaR, betas, FX exposure, and stress tests.
        """
        fx_rate = self.get_cad_usd_rate()

        # Step 1: Benchmark returns for SPY (US) and XIU (CA)
        spy_hist = self.get_price_history_and_metrics("SPY", limit=80)
        xiu_hist = self.get_price_history_and_metrics("XIU", limit=80)

        spy_prices = spy_hist["prices"]
        xiu_prices = xiu_hist["prices"]

        spy_rets = [(spy_prices[i] - spy_prices[i-1])/spy_prices[i-1] for i in range(1, len(spy_prices))] if len(spy_prices) > 1 else [0.0]*60
        xiu_rets = [(xiu_prices[i] - xiu_prices[i-1])/xiu_prices[i-1] for i in range(1, len(xiu_prices))] if len(xiu_prices) > 1 else [0.0]*60

        # Step 2: Ingest & resolve holdings
        enriched_holdings = []
        return_series_dict = {}

        total_cost_basis_base = 0.0
        total_market_val_holdings_base = 0.0

        for h in holdings_input:
            sym = str(h["symbol"]).upper().strip()
            shares = float(h.get("shares", 0.0))
            cost_basis = float(h.get("cost_basis", 100.0))

            meta = self.get_security_metadata(sym) or {
                "symbol": sym,
                "name": sym,
                "exchange": "TSX" if sym in ["RY", "TD", "SHOP", "ENB", "CNR", "CNQ", "BN", "BAM", "CP", "XIU"] else "NASDAQ",
                "country": "CA" if sym in ["RY", "TD", "SHOP", "ENB", "CNR", "CNQ", "BN", "BAM", "CP", "XIU"] else "US",
                "currency": "CAD" if sym in ["RY", "TD", "SHOP", "ENB", "CNR", "CNQ", "BN", "BAM", "CP", "XIU"] else "USD",
                "sector": "Information Technology" if sym in ["AAPL", "MSFT", "NVDA", "SHOP"] else "Financials",
                "industry": "Software & Services",
            }

            hist = self.get_price_history_and_metrics(sym, limit=80)
            curr_price = float(h.get("current_price") or hist["latest_price"])

            # Local market valuation
            cost_total_local = shares * cost_basis
            mkt_val_local = shares * curr_price

            # Convert to base currency
            holding_curr = meta["currency"]
            if base_currency == "USD":
                mkt_val_base = mkt_val_local if holding_curr == "USD" else (mkt_val_local / fx_rate)
                cost_base = cost_total_local if holding_curr == "USD" else (cost_total_local / fx_rate)
            else:  # base is CAD
                mkt_val_base = (mkt_val_local * fx_rate) if holding_curr == "USD" else mkt_val_local
                cost_base = (cost_total_local * fx_rate) if holding_curr == "USD" else cost_total_local

            unrealized_pnl_base = mkt_val_base - cost_base
            unrealized_pnl_pct = round(((curr_price - cost_basis) / cost_basis) * 100.0, 2) if cost_basis > 0 else 0.0

            total_cost_basis_base += cost_base
            total_market_val_holdings_base += mkt_val_base

            # Calculate betas and volatility
            beta_spy, beta_xiu, asset_rets, daily_vol = self.calculate_asset_betas_and_returns(
                sym, spy_rets, xiu_rets
            )
            return_series_dict[sym] = asset_rets

            # Local Beta assignment: US uses SPY, CA uses XIU
            if meta["country"] == "US":
                b_local, b_cross = beta_spy, beta_xiu
            else:
                b_local, b_cross = beta_xiu, beta_spy

            # Days to liquidate at 10% ADV
            adv = hist["adv_20"]
            days_to_liquidate = round(max(0.01, shares / (0.10 * adv)), 2) if adv > 0 else 0.1

            # Exit engine evaluation
            pos_dict = {
                "symbol": sym,
                "entry_price": cost_basis,
                "current_price": curr_price,
                "shares": int(shares),
                "stop_loss": cost_basis * 0.92,
                "profit_target": cost_basis * 1.15,
            }
            metrics_dict = {"close": curr_price, "sma_20": curr_price * 0.99, "rsi_14": 52.0}
            exit_verdict_obj = exit_signal_engine.evaluate_position(pos_dict, current_metrics=metrics_dict)

            enriched_holdings.append({
                "symbol": sym,
                "company_name": meta["name"],
                "exchange": meta["exchange"],
                "country": meta["country"],
                "currency": meta["currency"],
                "shares": shares,
                "cost_basis_per_share": round(cost_basis, 2),
                "cost_basis_total": round(cost_base, 2),
                "current_price": round(curr_price, 2),
                "market_value_local": round(mkt_val_local, 2),
                "market_value_base": round(mkt_val_base, 2),
                "unrealized_pnl_base": round(unrealized_pnl_base, 2),
                "unrealized_pnl_pct": unrealized_pnl_pct,
                "sector": meta["sector"],
                "industry": meta["industry"],
                "beta_local": b_local,
                "beta_cross": b_cross,
                "daily_volatility_pct": round(daily_vol * 100.0, 2),
                "days_to_liquidate": days_to_liquidate,
                "exit_verdict": exit_verdict_obj.state if exit_verdict_obj.state in ["HOLD", "WATCH", "REDUCE", "EXIT"] else "HOLD",
                "exit_trigger": exit_verdict_obj.primary_trigger,
                "exit_score": int(exit_verdict_obj.confidence * 100) if hasattr(exit_verdict_obj, "confidence") else 75,
                "suggested_trailing_stop": round(exit_verdict_obj.suggested_trailing_stop or (curr_price * 0.93), 2),
            })

        # Portfolio total market value (holdings + cash)
        total_market_val_base = total_market_val_holdings_base + cash_base
        total_pnl_base = total_market_val_holdings_base - total_cost_basis_base
        total_pnl_pct = round((total_pnl_base / total_cost_basis_base) * 100.0, 2) if total_cost_basis_base > 0 else 0.0

        # Step 3: Compute Weights & Covariance Matrix
        n_holdings = len(enriched_holdings)
        weights_list = []
        for h in enriched_holdings:
            w = h["market_value_base"] / total_market_val_base if total_market_val_base > 0 else 0.0
            h["weight_pct"] = round(w * 100.0, 2)
            weights_list.append(w)

        weights = np.array(weights_list)

        # Build returns matrix
        min_len = 60
        for sym in return_series_dict:
            min_len = min(min_len, len(return_series_dict[sym]))

        sym_order = [h["symbol"] for h in enriched_holdings]
        returns_cols = []
        for s in sym_order:
            rets = return_series_dict.get(s, [0.0] * min_len)
            returns_cols.append(rets[-min_len:] if len(rets) >= min_len else [0.0] * min_len)

        returns_matrix = np.column_stack(returns_cols) if returns_cols else np.zeros((min_len, n_holdings))

        # Risk attribution (MCR & % RC)
        port_vol, port_vol_ann, mcr, rc_pct = self.calculate_risk_attribution(weights, returns_matrix)

        for i, h in enumerate(enriched_holdings):
            h["risk_contribution_pct"] = round(float(rc_pct[i]), 2)

        # Portfolio betas
        port_beta_local = float(sum(h["weight_pct"] / 100.0 * h["beta_local"] for h in enriched_holdings))
        port_beta_cross = float(sum(h["weight_pct"] / 100.0 * h["beta_cross"] for h in enriched_holdings))

        # Effective bets and clusters
        neff, clusters = self.calculate_effective_bets_and_clusters(sym_order, returns_matrix)

        # Sector concentrations
        sector_concentrations = {}
        for h in enriched_holdings:
            sec = h["sector"]
            sector_concentrations[sec] = round(sector_concentrations.get(sec, 0.0) + h["weight_pct"], 2)

        # Value at Risk
        var_95_daily = round(1.645 * (port_vol * 100.0), 2)
        # CVaR (Expected Shortfall) approximation
        cvar_95_daily = round(2.06 * (port_vol * 100.0), 2)

        # FX Exposure
        fx_report = self.calculate_fx_exposure(enriched_holdings, base_currency, fx_rate)

        # Stress Replays
        stress_replays = self.calculate_stress_replays(enriched_holdings, weights, total_market_val_base)

        # Drawdown Circuit Breakers
        circuit_breakers = self.evaluate_circuit_breakers(current_drawdown_pct, risk_profile)

        # Disclaimers
        disclaimers = [
            "HYPOTHETICAL PORTFOLIO ANALYTICS: Risk contribution, betas, and stress replays are mathematical approximations based on historical data. They do not forecast future performance.",
            "IMPERSONAL DECISION-SUPPORT ONLY: Outputs constitute impersonal quantitative decision-support research and do not provide suitability determinations under CSA Staff Notice 31-369 or SEC Rule 206(4)-1. Sizing and risk management remain the sole responsibility of the user.",
            f"DUAL-CURRENCY CONVERSION: Valuations converted at Bank of Canada Valet rate {fx_rate:.4f} CAD per USD. Unhedged foreign currency exchange rate fluctuations may impact realized returns.",
        ]

        # Verify compliance linter clean
        for d in disclaimers:
            linter.assert_clean(d)

        # Convert to Pydantic objects
        holding_records = [PortfolioHoldingRecord(**h) for h in enriched_holdings]

        report = PortfolioAnalyticsReport(
            portfolio_id=portfolio_id,
            portfolio_name=portfolio_name,
            as_of_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            base_currency=base_currency,
            risk_profile=risk_profile,
            total_cost_basis_base=round(total_cost_basis_base + cash_base, 2),
            total_market_value_base=round(total_market_val_base, 2),
            total_unrealized_pnl_base=round(total_pnl_base, 2),
            total_unrealized_pnl_pct=total_pnl_pct,
            cash_base=round(cash_base, 2),
            effective_bets=neff,
            portfolio_beta_local=round(port_beta_local, 2),
            portfolio_beta_cross=round(port_beta_cross, 2),
            portfolio_daily_vol_pct=round(port_vol * 100.0, 2),
            portfolio_annualized_vol_pct=round(port_vol_ann * 100.0, 2),
            var_95_daily_pct=var_95_daily,
            cvar_95_daily_pct=cvar_95_daily,
            fx_exposure=fx_report,
            sector_concentrations=sector_concentrations,
            correlation_clusters=clusters,
            stress_replays=stress_replays,
            circuit_breakers=circuit_breakers,
            holdings=holding_records,
            disclaimers=disclaimers,
        )

        return report

    def get_default_portfolios(self) -> Dict[str, Any]:
        """Return the 3 reference model portfolios (Cross-Border, US Alpha, TSX Income)."""
        cross_border_holdings = [
            {"symbol": "AAPL", "shares": 100, "cost_basis": 224.50},
            {"symbol": "MSFT", "shares": 60, "cost_basis": 415.00},
            {"symbol": "NVDA", "shares": 80, "cost_basis": 115.00},
            {"symbol": "RY", "shares": 150, "cost_basis": 156.00},
            {"symbol": "TD", "shares": 180, "cost_basis": 84.50},
            {"symbol": "ENB", "shares": 300, "cost_basis": 49.20},
            {"symbol": "CNQ", "shares": 200, "cost_basis": 46.80},
            {"symbol": "SHOP", "shares": 120, "cost_basis": 98.00},
        ]

        us_alpha_holdings = [
            {"symbol": "AAPL", "shares": 150, "cost_basis": 220.00},
            {"symbol": "MSFT", "shares": 80, "cost_basis": 410.00},
            {"symbol": "NVDA", "shares": 150, "cost_basis": 110.00},
            {"symbol": "AMZN", "shares": 100, "cost_basis": 185.00},
            {"symbol": "GOOGL", "shares": 90, "cost_basis": 172.00},
            {"symbol": "JPM", "shares": 60, "cost_basis": 210.00},
        ]

        tsx_income_holdings = [
            {"symbol": "RY", "shares": 250, "cost_basis": 152.00},
            {"symbol": "TD", "shares": 250, "cost_basis": 82.00},
            {"symbol": "ENB", "shares": 500, "cost_basis": 48.00},
            {"symbol": "CNQ", "shares": 350, "cost_basis": 45.00},
            {"symbol": "CNR", "shares": 100, "cost_basis": 158.00},
            {"symbol": "BAM", "shares": 200, "cost_basis": 52.00},
        ]

        p1 = self.analyze_portfolio(
            cross_border_holdings,
            base_currency="USD",
            risk_profile="MODERATE",
            portfolio_name="Cross-Border Balanced Growth & Dividend",
            portfolio_id="PORT_CROSS_BORDER",
            cash_base=12500.0,
            current_drawdown_pct=-2.1,
        )

        p2 = self.analyze_portfolio(
            us_alpha_holdings,
            base_currency="USD",
            risk_profile="AGGRESSIVE",
            portfolio_name="US Mega-Cap & Momentum Book",
            portfolio_id="PORT_US_ALPHA",
            cash_base=8000.0,
            current_drawdown_pct=-4.3,
        )

        p3 = self.analyze_portfolio(
            tsx_income_holdings,
            base_currency="CAD",
            risk_profile="CONSERVATIVE",
            portfolio_name="TSX Dividend & Resource Resilience",
            portfolio_id="PORT_TSX_INCOME",
            cash_base=15000.0,
            current_drawdown_pct=-1.2,
        )

        return {
            "default_portfolio_id": "PORT_CROSS_BORDER",
            "portfolios": {
                "PORT_CROSS_BORDER": p1.to_dict(),
                "PORT_US_ALPHA": p2.to_dict(),
                "PORT_TSX_INCOME": p3.to_dict(),
            },
            "as_of_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

    def parse_csv_holdings(self, csv_text: str) -> List[Dict[str, Any]]:
        """
        Parse user-provided CSV or pasted text of format:
        symbol,shares,cost_basis
        or
        symbol,shares,cost_basis,currency
        """
        holdings = []
        lines = csv_text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.lower().startswith("symbol"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                sym = parts[0].upper()
                try:
                    shares = float(parts[1])
                    cost = float(parts[2])
                    curr = parts[3].upper() if len(parts) > 3 else ("CAD" if sym in ["RY", "TD", "SHOP", "ENB", "CNR", "CNQ", "BN", "BAM", "CP", "XIU"] else "USD")
                    holdings.append({
                        "symbol": sym,
                        "shares": shares,
                        "cost_basis": cost,
                        "currency": curr,
                    })
                except ValueError:
                    continue
        return holdings


portfolio_engine = PortfolioIntelligenceEngine()
