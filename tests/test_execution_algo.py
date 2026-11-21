"""
Execution algorithm tests — reduced to the parts that survive the honesty overhaul.

HONESTY OVERHAUL (Phase 2 follow-up): this module used to build a simulated order
book with `microstructure_engine.build_symbol_dossier()` and then exercise the
order-sweep, limit-fill, TWAP, VWAP and POV algorithms against it. That book was
generated, not observed: the platform holds an end-of-day price series with no
quote feed, no depth and no fills. Nine of the eleven tests here asserted that
invented numbers were internally consistent, which proves nothing about markets.
They are deleted along with the engine that produced them.

Two things this module tested are real and still used:

  * the dual-market exchange fee schedule, consumed by the internal simulator
    adapter in src/execution/adapters/internal_simulator.py; and
  * the sentinel's execution-slippage predicate, whose contract changed — the
    slippage field is now None because nothing measures it, and a None must not
    be treated as zero or raise.
"""

from src.engine.execution_algo import execution_algo_engine


class TestExchangeFeeSchedules:
    """The fee tables are declarative inputs, not market estimates."""

    def test_taker_fees_are_positive_and_differ_by_market(self):
        us = execution_algo_engine.get_exchange_fee(1000, "US", is_maker=False)
        ca = execution_algo_engine.get_exchange_fee(1000, "CA", is_maker=False)

        assert us > 0.0, "US taker fee should be a cost"
        assert ca > 0.0, "Canadian taker fee should be a cost"
        assert us != ca, "US and Canadian fee schedules should not be identical"

    def test_maker_orders_net_a_rebate_in_both_markets(self):
        """A maker fee is negative by construction — it is a rebate, not a cost.

        The docstring promises "positive value for fee paid, negative for rebate
        earned", so asserting positivity here would be asserting the opposite of
        the contract.
        """
        us = execution_algo_engine.get_exchange_fee(1000, "US", is_maker=True)
        ca = execution_algo_engine.get_exchange_fee(1000, "CA", is_maker=True)
        assert us < 0.0
        assert ca < 0.0

    def test_maker_is_cheaper_than_taker_in_both_markets(self):
        for country in ("US", "CA"):
            maker = execution_algo_engine.get_exchange_fee(1000, country, is_maker=True)
            taker = execution_algo_engine.get_exchange_fee(1000, country, is_maker=False)
            assert maker < taker, f"{country}: maker should cost less than taker"

    def test_fee_is_rate_times_shares_rounded_to_four_places(self):
        """The helper rounds per call, so linearity must be checked against the
        declared rate rather than against a one-share sample scaled up."""
        e = execution_algo_engine
        for country, rate in (("US", e.us_taker_fee_per_share + e.us_regulatory_fee_per_share),
                              ("CA", e.ca_taker_fee_per_share + e.ca_regulatory_fee_per_share)):
            for shares in (1, 1000, 4096):
                assert e.get_exchange_fee(shares, country, is_maker=False) == \
                    round(shares * rate, 4)


def test_slippage_predicate_guards_against_a_missing_measurement():
    """The wired predicate must skip a None slippage instead of comparing it.

    `tca_expected_slippage_bps` is None in production now, and `None >= 15.0`
    raises in Python 3. The guard is asserted in the source that actually runs,
    rather than by driving a stub dossier through the full predicate chain.
    """
    import inspect

    from src.engine.quant_intel_sentinel import quant_intel_sentinel

    source = inspect.getsource(quant_intel_sentinel.evaluate_ticker_sentinel)
    assert "EXECUTION_SLIPPAGE_WARNING" in source, "predicate was deleted outright"
    assert "slip_bps is not None and slip_bps >= 15.0" in source, (
        "the slippage predicate compares a value that is now None"
    )


def test_liquidity_void_predicate_cannot_be_fed_synthesised_data():
    """The liquidity-void predicate read a synthesised liquidity state.

    It stays in the engine (deleting a predicate silently is its own kind of
    dishonesty) but the field it read no longer exists on the dossier, so it can
    never fire.
    """
    import dataclasses
    import inspect

    from src.engine.quant_intel import QuantIntelDossier
    from src.engine.quant_intel_sentinel import quant_intel_sentinel

    source = inspect.getsource(quant_intel_sentinel.evaluate_ticker_sentinel)
    assert "LIQUIDITY_VOID_SPIKE" in source, "predicate was deleted outright"
    assert 'getattr(dossier, "microstructure_metrics", None)' in source, (
        "the predicate no longer reads its input through a None-safe getattr"
    )

    fields = {f.name for f in dataclasses.fields(QuantIntelDossier)}
    assert "microstructure_metrics" not in fields, (
        "the dossier still carries a microstructure_metrics field, so synthesised "
        "data could be attached to it again"
    )


def test_trade_plan_execution_fields_default_to_none():
    """Execution quality is unmeasured, so the fields must default to None."""
    import dataclasses

    from src.engine.quant_intel import QuantIntelTradePlan

    defaults = {f.name: f.default for f in dataclasses.fields(QuantIntelTradePlan)}
    for field in (
        "tca_execution_strategy",
        "tca_expected_slippage_bps",
        "tca_effective_fill_price",
        "tca_optimal_slices",
    ):
        assert defaults[field] is None, f"{field} defaults to {defaults[field]!r}"
