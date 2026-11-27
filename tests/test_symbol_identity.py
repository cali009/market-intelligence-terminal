"""
Tests for the symbol identity gate.

Every case is drawn from a real observation. The MISMATCH cases are recycled tickers
that Yahoo answered with HTTP 200 and a full price series. The VERIFIED cases are
legitimate naming variants from the same probe run, which must not be rejected -- a
gate that rejects half the real universe is worse than no gate, because people turn it
off.
"""

from src.data.symbol_identity import (
    MISMATCH,
    NEEDS_REVIEW,
    VERIFIED,
    compare_names,
    verify_identity,
)

# (expected company, what Yahoo actually returned, instrument type)
RECYCLED_TICKERS = [
    ("Booking Holdings Inc.", "Pictet Cleaner Planet ETF", "ETF"),
    ("IHS Markit Ltd.", "Harbor PanAgora Dynamic Large Cap Core ETF", "ETF"),
    ("Credit Suisse Group AG", "Capstone Copper Corp.", "EQUITY"),
    ("Anadarko Petroleum Corporation", "ARKO Petroleum Corp.", "EQUITY"),
    ("Tiffany & Co.", "NinePoint Target Income Fund ETF", "ETF"),
    ("Activision Blizzard, Inc.", "Vegashares Trillions ETF", "ETF"),
    ("Monsanto Company", "ProShares S&P 500 Dynamic Daily Buffer ETF", "ETF"),
    ("Celgene Corporation", "Global X Emerging Markets Great Consumer ETF", "ETF"),
    ("EMC Corporation", "AB California Intermediate Municipal ETF", "MUTUALFUND"),
    ("Plum Creek Timber Co.", "PGIM Corporate Bond 10+ Year ETF", "ETF"),
    ("SunTrust Banks, Inc.", "Solidion Technology, Inc.", "EQUITY"),
    ("BB&T Corporation", "Beacon Financial Corporation", "EQUITY"),
]

LEGITIMATE_VARIANTS = [
    ("Apple Inc.", "Apple Inc."),
    ("IBM", "International Business Machines Corporation"),
    ("BNY Mellon", "The Bank of New York Mellon Corporation"),
    ("Alphabet Inc.", "Alphabet Inc."),
    ("Agnc Investment Corp.", "AGNC Investment Corp."),
    ("Berkshire Hathaway Inc.", "Berkshire Hathaway Inc."),
    ("Canadian National Railway", "Canadian National Railway Company"),
    ("Alimentation Couche-Tard", "Alimentation Couche-Tard Inc."),
    ("Shopify Inc.", "Shopify Inc."),
    ("Teck Resources Limited", "Teck Resources Limited"),
    ("Rogers Communications Inc.", "Rogers Communications Inc."),
    ("Brookfield Infrastructure Partners L.P.", "Brookfield Infrastructure Partners L.P."),
    # Diacritics. The first version of the normaliser regex-stripped the macron out of
    # "Mondel\u0113z" entirely, leaving "mondel", and flagged a real S&P 100 constituent
    # for review. Folding first, then stripping, is the fix.
    ("Mondel\u0113z International", "Mondelez International, Inc."),
    ("Nestl\u00e9 S.A.", "NESTLE SA"),
    ("Brookfield Corporation", "Brookfield Corporation"),
]


def test_recycled_tickers_are_all_rejected():
    """
    The failure mode the gate exists for. The contract is that bars are only written on
    a VERIFIED verdict, so the assertion is that none of these reaches VERIFIED -- a
    NEEDS_REVIEW also blocks the write, and is the honest verdict where two genuinely
    unrelated companies happen to share a word (Anadarko Petroleum vs ARKO Petroleum).
    """
    for expected, returned, itype in RECYCLED_TICKERS:
        r = compare_names(expected, returned, itype)
        assert r["verdict"] != VERIFIED, (
            "%s -> %s was ACCEPTED (%s)" % (expected, returned, r["evidence"]))


def test_clear_cut_recycling_is_a_hard_mismatch_not_a_review():
    """A pooled vehicle, or names with nothing in common, must not be softened to
    NEEDS_REVIEW -- those cases are decidable and should say so."""
    clear = [(e, r, i) for e, r, i in RECYCLED_TICKERS if i != "EQUITY"]
    clear += [("Credit Suisse Group AG", "Capstone Copper Corp.", "EQUITY"),
              ("Activision Blizzard, Inc.", "Vegashares Trillions ETF", "ETF")]
    for expected, returned, itype in clear:
        r = compare_names(expected, returned, itype)
        assert r["verdict"] == MISMATCH, (
            "%s -> %s gave %s, expected a hard MISMATCH (%s)"
            % (expected, returned, r["verdict"], r["evidence"]))


def test_legitimate_variants_are_all_accepted():
    """A gate that rejects the real universe gets switched off."""
    for expected, returned in LEGITIMATE_VARIANTS:
        r = compare_names(expected, returned, "EQUITY")
        assert r["verdict"] == VERIFIED, (
            "%s -> %s was rejected as %s (%s)"
            % (expected, returned, r["verdict"], r["evidence"]))


def test_pooled_vehicle_is_a_mismatch_even_when_a_word_overlaps():
    """Recycling onto an ETF must not be rescued by a shared word."""
    r = compare_names("Energy Transfer LP", "ProShares Energy Transfer ETF", "ETF")
    assert r["verdict"] == MISMATCH
    assert "pooled vehicle" in " ".join(r["evidence"])


def test_an_etf_expected_as_an_etf_is_not_a_mismatch():
    r = compare_names("SPDR S&P 500 ETF Trust", "SPDR S&P 500 ETF Trust", "ETF")
    assert r["verdict"] == VERIFIED


def test_missing_expected_name_is_unverified_not_verified():
    r = compare_names("", "Anything At All Corp.")
    assert r["verdict"] == "UNVERIFIED"
    assert "No expected name" in r["evidence"][0]


def test_missing_returned_name_needs_review():
    r = compare_names("Apple Inc.", "")
    assert r["verdict"] == NEEDS_REVIEW


def test_diacritics_do_not_cause_a_false_review_flag():
    """Found by running the gate over all 138 reachable index constituents."""
    assert compare_names("Mondel\u0113z International",
                         "Mondelez International, Inc.", "EQUITY")["verdict"] == VERIFIED
    assert compare_names("Nestl\u00e9 S.A.", "NESTLE SA", "EQUITY")["verdict"] == VERIFIED


def test_single_shared_word_does_not_verify_a_same_family_name():
    """Brookfield Corporation and Brookfield Renewable Partners are different issuers."""
    r = compare_names("Brookfield Corporation", "Brookfield Renewable Partners L.P.", "EQUITY")
    assert r["verdict"] != VERIFIED


def test_partial_overlap_is_needs_review_not_a_silent_pass():
    r = compare_names("Brookfield Corporation", "Brookfield Renewable Partners L.P.")
    assert r["verdict"] == NEEDS_REVIEW
    assert "brookfield" in r["shared_tokens"]


def test_verdict_never_comes_without_evidence():
    for expected, returned, itype in RECYCLED_TICKERS + [
            (e, r, "EQUITY") for e, r in LEGITIMATE_VARIANTS]:
        r = compare_names(expected, returned, itype)
        assert r["evidence"], "%s -> %s has no evidence" % (expected, returned)


def test_name_match_on_the_wrong_venue_needs_review():
    r = verify_identity("Credit Suisse Group AG", "XNYS",
                        "Credit Suisse Group AG", "SIX Swiss Exchange", "EQUITY")
    assert r["verdict"] == NEEDS_REVIEW
    assert r["venue_ok"] is False


def test_name_match_on_the_right_venue_verifies():
    r = verify_identity("Apple Inc.", "XNYS", "Apple Inc.", "NasdaqGS", "EQUITY")
    assert r["verdict"] == VERIFIED
    assert r["venue_ok"] is True


def test_canadian_venue_recognised_for_the_tsx_calendar():
    for venue in ("Toronto", "TSXV", "TMX"):
        assert verify_identity("Royal Bank of Canada", "XTSE",
                               "Royal Bank of Canada", venue, "EQUITY")["verdict"] == VERIFIED


def test_venue_mismatch_cannot_override_a_name_mismatch():
    r = verify_identity("Credit Suisse Group AG", "XNYS",
                        "Capstone Copper Corp.", "Toronto", "EQUITY")
    assert r["verdict"] == MISMATCH
