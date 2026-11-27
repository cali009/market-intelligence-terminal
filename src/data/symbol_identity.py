"""
Symbol identity verification for market-data ingestion.

Why this exists
---------------
A ticker is not a permanent identifier. It is reassigned when a company is acquired,
renamed, or delisted, so the same five letters can point at a different security five
years later. Probing Yahoo for a list of known-delisted symbols on 2026-11-22 returned:

    PCLN  expected Booking Holdings        returned Pictet Cleaner Planet ETF
    INFO  expected IHS Markit              returned Harbor PanAgora Dynamic Large Cap Core ETF
    CS    expected Credit Suisse           returned Capstone Copper (TSX)
    APC   expected Anadarko                returned ARKO Petroleum Corp
    TIF   expected Tiffany                 returned NinePoint Target Income Fund ETF (TSX)

Yahoo answers HTTP 200 with a full price series for all of these. Ingesting by ticker
alone would have written an ETF's prices into the database under a delisted bank's name,
and nothing downstream would have noticed. So every bar write is gated on the identity
of the security the endpoint actually returned.

Matching is deliberately asymmetric: it is easy to prove two names are the same company
and easy to prove they are unrelated, and the cases in between are reported for review
rather than decided. A false MISMATCH costs one skipped symbol; a false VERIFIED costs a
silently corrupted price series.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Set, Tuple

VERIFIED = "VERIFIED"
MISMATCH = "MISMATCH"
NEEDS_REVIEW = "NEEDS_REVIEW"
UNVERIFIED = "UNVERIFIED"

# Words that carry no identifying information. Removed before comparison so that
# "International Business Machines Corporation" and "IBM" are not penalised for the
# suffix, and "The Bank of New York Mellon Corporation" and "BNY Mellon" can still meet.
NOISE = {
    "the", "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "llc", "lp", "llp", "plc", "sa", "nv", "ag", "gmbh", "class",
    "common", "shares", "share", "stock", "holding", "holdings", "group", "plc.",
    "a", "b", "subordinate", "voting", "multiple", "of", "and", "de",
}
# "new" is deliberately NOT noise: removing it collapses "Bank of New York" to
# (bank, york) and so destroys the "bny" initialism that identifies BNY Mellon.

# Suffixes that mark a pooled vehicle. If the endpoint returns one of these for a
# symbol we believe is an operating company, that is a mismatch regardless of names,
# because ticker recycling onto an ETF is the single most common failure.
POOLED_WORDS = {"etf", "fund", "trust", "index", "portfolio", "mutual", "exchange",
                "traded", "royalty", "split"}
# "lp" and "partners" are deliberately absent. Plenty of operating partnerships use them,
# so their presence proves nothing; instrument_type is the reliable pooled-vehicle signal
# and the words above carry the rest.


def _normalise(name: str) -> str:
    # Fold diacritics before stripping them. Regex-stripping "Mondel\u0113z" removed the
    # \u0113 entirely and left "mondel", which then failed to match "Mondelez" -- a
    # spurious review flag on a real constituent. NFKD decomposes \u0113 into e + a
    # combining macron, and only the combining mark is discarded. The same applies to
    # Nestl\u00e9, Bradesco and any other non-ASCII issuer name.
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def _significant_tokens(name: str) -> Set[str]:
    return {t for t in _normalise(name).split() if t not in NOISE and not t.isdigit()}


def _looks_pooled(name: str, instrument_type: Optional[str] = None) -> bool:
    if instrument_type and str(instrument_type).upper() in {"ETF", "MUTUALFUND", "INDEX"}:
        return True
    toks = _significant_tokens(name)
    return bool(toks & POOLED_WORDS)


def _acronym(tokens: Iterable[str]) -> str:
    """Initials of the given tokens, in document order.

    Order matters: sorting turned "International Business Machines" into "bim" and so
    failed to match "IBM".
    """
    return "".join(t[0] for t in tokens if t)


def _ordered_tokens(name: str) -> List[str]:
    toks = _normalise(name).split()
    return [t for t in toks if t not in NOISE and not t.isdigit()]


def _window_acronyms(name: str) -> Set[str]:
    """Every acronym formed by a contiguous run of 2..4 significant tokens.

    This is what lets "BNY Mellon" match "The Bank of New York Mellon Corporation":
    the run (bank, new, york) yields "bny". Checking only the whole name, or only
    single tokens, misses it.
    """
    toks = _ordered_tokens(name)
    out: Set[str] = set()
    for size in (2, 3, 4):
        for i in range(0, len(toks) - size + 1):
            out.add(_acronym(toks[i:i + size]))
    return out


def compare_names(expected: str, returned: str,
                  returned_instrument_type: Optional[str] = None) -> Dict:
    """
    Compare the company we asked for with the company the endpoint actually returned.

    Returns a dict with a verdict and the evidence for it. Never raises: an
    unparseable name is NEEDS_REVIEW, not a crash and not a pass.
    """
    exp_norm, ret_norm = _normalise(expected), _normalise(returned)
    result: Dict = {
        "expected_name": expected,
        "returned_name": returned,
        "returned_instrument_type": returned_instrument_type,
        "evidence": [],
    }

    if not exp_norm:
        result["verdict"] = UNVERIFIED
        result["evidence"].append("No expected name recorded, so nothing to compare against.")
        return result
    if not ret_norm:
        result["verdict"] = NEEDS_REVIEW
        result["evidence"].append("Endpoint returned no security name.")
        return result

    if exp_norm == ret_norm:
        result["verdict"] = VERIFIED
        result["evidence"].append("Names identical after normalisation.")
        return result

    exp_tok, ret_tok = _significant_tokens(expected), _significant_tokens(returned)
    exp_ordered, ret_ordered = _ordered_tokens(expected), _ordered_tokens(returned)
    shared = exp_tok & ret_tok

    # A pooled vehicle where an operating company was expected is a mismatch on its
    # own, even if a word happens to overlap.
    exp_is_pooled = _looks_pooled(expected)
    ret_is_pooled = _looks_pooled(returned, returned_instrument_type)
    if ret_is_pooled and not exp_is_pooled:
        result["verdict"] = MISMATCH
        result["evidence"].append(
            "Endpoint returned a pooled vehicle (ETF/fund/trust) for a symbol recorded "
            "as an operating company -- the classic signature of a recycled ticker.")
        return result

    # Token-level containment, NOT character-level. Comparing the normalised strings
    # let "ARKO Petroleum Corp." through as a containment of "Anadarko Petroleum
    # Corporation", because "arko petroleum" is a character-substring of "anadarko
    # petroleum". Every identifying token must be present as a whole token.
    if exp_tok and ret_tok:
        shorter, longer = (exp_tok, ret_tok) if len(exp_tok) <= len(ret_tok) else (ret_tok, exp_tok)
        # Equal token sets always verify, however short. A strict subset only verifies
        # when the shorter side has at least two tokens: a single shared word proves
        # almost nothing. "Brookfield Corporation" and "Brookfield Renewable Partners
        # L.P." are different companies in the same family, and a one-token match would
        # have written one's price series under the other's symbol.
        if exp_tok == ret_tok or (len(shorter) >= 2 and shorter <= longer):
            result["verdict"] = VERIFIED
            result["evidence"].append(
                "Every identifying token of one name appears as a whole token in the "
                "other.")
            return result

    # Acronyms. "IBM" vs "International Business Machines Corporation" matches on the
    # initials of the whole name; "BNY Mellon" vs "The Bank of New York Mellon
    # Corporation" matches because (bank, new, york) is a contiguous run whose initials
    # spell "bny".
    ret_windows = _window_acronyms(returned)
    for tok in exp_ordered:
        if len(tok) <= 5 and tok in ret_windows:
            result["verdict"] = VERIFIED
            result["evidence"].append(
                "Expected token '%s' is the initialism of a run of words in the "
                "returned name." % tok)
            return result
    # Whole-name initialism, but only for names of three or more words. At two words
    # the collision rate is far too high: "Anadarko Petroleum" and "ARKO Petroleum"
    # both reduce to "ap" and are unrelated companies.
    if len(exp_ordered) >= 3 and len(ret_ordered) >= 3 \
            and _acronym(exp_ordered) == _acronym(ret_ordered):
        result["verdict"] = VERIFIED
        result["evidence"].append("Both names reduce to the same initialism.")
        return result

    union = exp_tok | ret_tok
    jaccard = len(shared) / len(union) if union else 0.0
    result["shared_tokens"] = sorted(shared)
    result["jaccard"] = round(jaccard, 3)

    if jaccard >= 0.5 and shared:
        result["verdict"] = VERIFIED
        result["evidence"].append(
            "Strong token overlap (%s, Jaccard %.2f)." % (", ".join(sorted(shared)), jaccard))
        return result

    if not shared:
        result["verdict"] = MISMATCH
        result["evidence"].append(
            "No identifying word in common between '%s' and '%s'." % (expected, returned))
        return result

    result["verdict"] = NEEDS_REVIEW
    result["evidence"].append(
        "Partial overlap only (%s, Jaccard %.2f). Not confident enough to accept or "
        "reject; a human should look at this pair." % (", ".join(sorted(shared)), jaccard))
    return result


def compare_exchanges(expected_calendar: str, returned_exchange: Optional[str]) -> bool:
    """
    Whether the venue the endpoint returned belongs to the calendar we recorded.

    Only checks the family, not the exact venue: a security can be recorded as NASDAQ
    and reported as NasdaqGS, and that is not a mismatch.
    """
    if not returned_exchange:
        return False
    rx = str(returned_exchange).upper()
    cal = str(expected_calendar).upper()
    if cal == "XNYS":
        return any(k in rx for k in ("NYSE", "NASDAQ", "AMEX", "ARCA", "CBOE", "BATS", "US"))
    if cal == "XTSE":
        return any(k in rx for k in ("TORONTO", "TSX", "TMX", "VENTURE", "CANADA"))
    return False


def verify_identity(expected_name: Optional[str], expected_calendar: Optional[str],
                    returned_name: Optional[str], returned_exchange: Optional[str],
                    returned_instrument_type: Optional[str] = None) -> Dict:
    """
    Full gate: name plus venue.

    Both must agree for VERIFIED. A name match on the wrong venue is NEEDS_REVIEW,
    because it usually means the symbol resolved to a different listing of a similarly
    named security rather than a recycled ticker.
    """
    name_result = compare_names(expected_name or "", returned_name or "",
                               returned_instrument_type)
    venue_ok = compare_exchanges(expected_calendar or "", returned_exchange)

    result = dict(name_result)
    result["returned_exchange"] = returned_exchange
    result["expected_calendar"] = expected_calendar
    result["venue_ok"] = venue_ok

    if name_result["verdict"] == MISMATCH:
        result["verdict"] = MISMATCH
    elif name_result["verdict"] == VERIFIED and venue_ok:
        result["verdict"] = VERIFIED
    elif name_result["verdict"] == VERIFIED and not venue_ok:
        result["verdict"] = NEEDS_REVIEW
        result["evidence"].append(
            "Name matched but the venue %r is not on the recorded calendar %r."
            % (returned_exchange, expected_calendar))
    elif name_result["verdict"] == NEEDS_REVIEW:
        result["verdict"] = NEEDS_REVIEW
        if not venue_ok:
            result["evidence"].append("Venue also did not match.")
    return result
