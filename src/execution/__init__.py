"""
QUANT INTEL™ Execution Gateway Package (Phase 34)

Multi-adapter paper execution with a regulator-aligned pre-trade risk governor.

Design invariants for the whole package:

1. THE INTERNAL SIMULATOR IS THE ONLY ADAPTER THAT SHIPS. External venues are closed for
   two independent reasons: Alpaca's Terms of Conditions prohibit commercial use and
   Content redistribution absent express prior written consent, and CIRO / IIROC Dealer
   Member Rule 3200 bars CIRO-registered order-execution-only dealers from letting clients
   generate orders through their own automated order system.
2. CANADIAN SYMBOLS NEVER ROUTE EXTERNALLY. Machine-enforced, not documented.
3. NO CONTROL SILENTLY RESIZES AN ORDER. A limit breach is reported, not quietly fixed.
"""
