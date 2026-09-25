"""
Compliance Disclaimers & Statutory Framework
US + Canada Regulatory Alignment (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

# Version tag for audit logging
DISCLAIMER_VERSION = "2026.1"

# Canadian General Advice Registration Exemption Disclosure
# Adheres strictly to CSA/CIRO Staff Notice 31-369 (December 2025)
CSA_31_369_GENERAL_ADVICE_DISCLAIMER = (
    "STATUTORY NOTICE (CANADA): This platform provides impersonal quantitative research and "
    "decision-support data intended exclusively for self-directed investors. It does not provide "
    "personalized investment advice, financial planning, or account management services. The scores, "
    "regime classifications, indicators, and model outputs presented herein are calculated by deterministic "
    "mathematical models and rule-based algorithms without consideration of your personal financial "
    "situation, risk tolerance, or investment objectives. Past hypothetical performance or paper-trading "
    "metrics are not indicative of future returns. The platform, its operators, and its algorithms do "
    "not hold any financial interest or proprietary position in the securities analyzed unless explicitly "
    "disclosed. You are solely responsible for evaluating the merits and risks of any trade or strategy."
)

# US Publisher Exclusion & SEC Marketing Rule (Rule 206(4)-1) Disclaimer
SEC_PUBLISHER_EXCLUSION_DISCLAIMER = (
    "REGULATORY NOTICE (UNITED STATES): Content published by this system constitutes impersonal, "
    "bona fide investment research and general circulation financial commentary under the publisher "
    "exclusion of Section 202(a)(11)(D) of the Investment Advisers Act of 1940. This software is not "
    "a registered broker-dealer, investment adviser, or commodity trading advisor. No content or calculation "
    "constitutes an offer to buy or sell, or a solicitation of an offer to buy or sell, any security or "
    "derivative instrument."
)

# SEC Marketing Rule 206(4)-1 Backtest / Hypothetical Performance Disclaimer
HYPOTHETICAL_BACKTEST_DISCLAIMER = (
    "HYPOTHETICAL & MODEL PERFORMANCE DISCLOSURE: Backtested, simulated, or paper-trading performance "
    "results have inherent limitations. Unlike actual trading records, simulated results do not represent "
    "actual execution and do not account for liquidity constraints, market impact, sudden margin changes, "
    "or behavioral discipline. All results reflect gross returns unless explicitly labeled net of estimated "
    "commissions and borrow fees. Material market conditions may have occurred that are not captured in "
    "the historical sample. No representation is made that any account will or is likely to achieve profits "
    "similar to those shown."
)

# Non-Professional Data Class Attestation Text
NON_PROFESSIONAL_ATTESTATION_TEXT = (
    "I hereby attest and confirm that: (1) I am accessing market intelligence and data solely in a personal, "
    "non-business capacity for the management of my own personal investment accounts; (2) I am not registered "
    "or qualified with any securities agency, exchange, association, or regulatory body (such as CIRO, SEC, "
    "FINRA, or provincial securities commissions) as an investment advisor, broker, or dealer; (3) I am not "
    "acting on behalf of any corporation, trust, partnership, or commercial institution."
)

DATA_CONFIDENCE_LEGEND = {
    "HIGH": "Primary source, bitemporally cross-checked, verified PIT fundamentals, unbroken price history.",
    "MEDIUM": "Single official source, fresh and internally consistent; or history length between 1 and 3 years.",
    "LOW": "Thin historical depth, missing non-critical fundamental facts, or unconfirmed corporate action.",
    "UNKNOWN": "Source unavailable or failed verification. Values suppressed as N/A to prevent hallucination.",
}
