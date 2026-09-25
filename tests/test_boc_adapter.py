"""
Tests for Bank of Canada Valet Adapter
Verifies JSON parsing, series normalization, and live API connectivity.
"""

from src.data.boc_valet import BankOfCanadaValetAdapter


def test_boc_valet_live_fetch():
    adapter = BankOfCanadaValetAdapter()
    # Fetch recent CAD/USD rate and benchmark 10-year yield
    obs = adapter.fetch_observations(["FXUSDCAD", "BD.CDN.10YR.DQ.YLD"], recent=3)
    assert len(obs) > 0

    series_ids = {o.series_id for o in obs}
    assert "FXUSDCAD" in series_ids
    # Verify values are positive floats
    for o in obs:
        assert o.value > 0
        assert o.source == "BOC_VALET"
        assert o.observation_date is not None


def test_boc_latest_cad_usd():
    adapter = BankOfCanadaValetAdapter()
    latest_fx = adapter.get_latest_cad_usd_fx()
    assert latest_fx is not None
    assert latest_fx.series_id == "FXUSDCAD"
    assert 1.0 < latest_fx.value < 2.0  # Realistic CAD/USD range
