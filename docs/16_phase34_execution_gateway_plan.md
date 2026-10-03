# QUANT INTEL™ Phase 34 Implementation Plan
## Multi-Broker Paper Execution Gateway & Pre-Trade Risk Governor

**Status: PLAN ONLY — awaiting approval. No code written.**

US (NYSE / NASDAQ / AMEX) + Canada (TSX / TSXV / CSE)

---

## 0. WHY THIS PHASE — THREE VERIFIED GAPS

### Gap 1 — The paper book has no buying-power enforcement at all

`src/engine/paper_trading.py` sizes positions as:

```python
# line 293
shares = max(5, int(1000.0 / risk_per_share))
```

A fixed $1,000 risk per trade with a floor of 5 shares. `cash_usd` is written **once** at
seed and is **never read, decremented, or reserved** — verified by grepping every
occurrence of `cash_usd` in the file (2 hits: the column definition and the seed INSERT).

Consequence: if `risk_per_share` is small, `shares` grows without bound and position
notional can exceed the $100,000 book with no rejection, no warning, and no audit
record. **Any pre-trade limit is currently unenforceable.**

### Gap 2 — No order lifecycle exists

There is no order state machine, no rejection reason codes, no partial-fill accounting,
and no cancellation path. `paper_trade` records are written already-filled. A system
that cannot represent a rejected order cannot report *why* an order was rejected.

### Gap 3 — No adapter boundary

`execution_algo.py` (624 lines) already simulates Market / Limit-Passive / TWAP / VWAP /
POV against historical bars, but it is wired directly to the platform's own data. There
is no seam at which a real broker could ever be attached, and no capability negotiation —
so nothing today degrades gracefully when an execution venue lacks an order type.

---

## 1. REGULATORY FINDINGS (VERIFIED THIS SESSION — THESE CONSTRAIN THE DESIGN)

### 1.1 Canada: a hard prohibition, not a technical limitation

Interactive Brokers publishes an explicit TWS API limitation titled **"Canadian Residents
Restricted From Programmatically Trading Canadian Products"**:

> *"Interactive Brokers Canada Inc. (IBC) does not allow users to use your own trading
> application to electronically submit order for products traded on a Canadian exchange
> or other marketplace through API, which would include Third Party Integrations. This
> decision was made through multiple and extensive communications between IBC compliance
> and personnel and senior management of the Canadian Investment Regulatory Organization
> (CIRO)... CIRO has implemented IIROC Dealer Member Rule (DMR) 3200 A. 1. (b) (i) which
> prohibits CIRO registrants, including IBC, from allowing its clients to use their own
> automated order systems to generated orders. Unfortunately, these restrictions would be
> also applicable with third-party applications like TradingView, NinjaTrader, or other
> such groups as they use an API connection."*
> — interactivebrokers.com/docs/tws-api/doc/notes-limitations/tws-api-limitations

The underlying rule, per CIRO's own notices on *Provisions Respecting Third-Party
Electronic Access to Marketplaces*: a Dealer Member providing an **order execution only
service (OES)** must not allow its clients to

- **use their own automated order system** to generate orders sent to the dealer, or send
  orders on a pre-determined basis; or
- manually send orders exceeding a CIRO threshold — identified elsewhere in the same
  notice as a **daily average of 500 orders per trading day in any calendar month**.

The definition of *automated order system* (NI 23-103 §1.2(1), quoted by CIRO) is
deliberately broad: *"a system used to automatically generate or electronically transmit
orders that are made on a pre-determined basis"*, explicitly including *"trading
algorithms that are used by marketplace participants, offered by marketplace participants
to clients **or developed or used by clients**."*

**This platform's signal engine generates orders on a pre-determined basis.** It is
textbook an automated order system. Wiring it to any CIRO-registered Canadian
order-execution-only dealer — even for paper, even through a third party — is the exact
activity the rule prohibits.

**Design consequence (non-negotiable):** Canadian symbols are **never** routed to an
external adapter. This must be machine-enforced in code and schema, in the same posture
as the Phase 33 Cboe index firewall, not merely documented.

### 1.2 The pre-trade control categories are regulatorily specified

UMIR Rule 7.1 / Policy 7.1 Part 7 requires *"automated controls to examine each order
before entry on a marketplace"* to prevent the entry of an order which would result in:

1. the Participant or Access Person **exceeding pre-determined credit or capital
   thresholds**;
2. **a client exceeding pre-determined credit or other limits** assigned by the
   Participant; or
3. **exceeding pre-determined limits on the value or volume of unexecuted orders** for a
   particular security or class of securities.

Policy 7.1 Part 8 (automated order systems) additionally requires:

- every automated order system be **tested before use and at least annually thereafter**,
  with a written record of the testing;
- the **ability to immediately override or disable** the automated order system and
  thereby prevent orders reaching a marketplace;
- and confirms the Participant remains responsible for orders from a **"runaway algo"**,
  including where the cause was not independently testable.

**Design consequence:** the Pre-Trade Risk Governor is not an arbitrary invention. Its
control taxonomy is taken directly from UMIR 7.1 Parts 7 and 8, so the platform's internal
controls are structurally aligned with what a Canadian Participant would be required to
operate. That alignment is itself a compliance asset and should be surfaced in the UI.

### 1.3 US: paper trading is available, but scoped and unverified for commercial use

- **Alpaca paper trading** — free, available globally, no brokerage account required.
  Sandbox endpoint `paper-api.alpaca.markets`. Rate limit 200 requests/minute. Covers
  **US-listed equities, options, and crypto only — no Canadian listings**. Alpaca states:
  *"The Paper Trading API is offered by AlpacaDB, Inc. and does not require real money or
  permit a user to conduct real transactions in the market."*
- **IBKR paper trading** — free with an approved and funded account. TWS port **7497**,
  IB Gateway port **4002** (live: 7496 / 4001). Documented simulator limitations:
  **no VWAP, Auction, RFQ, or Pegged-to-Market order types**; fills simulated **from the
  top of the book with no deep book access**; limited combo trading; the simulator
  **rejects the remainder of any exchange-directed market order that partially executes**;
  no mutual fund trading.

### 1.4 Alpaca Terms of Service — REVIEWED. Determination: NOT commercially usable

Reviewed in full (all three sections) at
`files.alpaca.markets/disclosures/library/TermsAndConditions.pdf`. Per your standing
instruction that a free API must not be assumed legally usable in a commercial
application, this review was completed **before** any adapter code was written. The
finding is dispositive and it **reverses the adapter-scope decision**.

**§ Personal and Non-Commercial Usage** — verbatim:

> *"Other than as set forth herein, you agree to use the Services and Content **solely for
> your own personal and non-commercial purposes**. Should you wish to use the Services and
> Content for any other purposes, including without limitation commercial usage, or making
> the Services and Content available to others through your own application (a "User
> Application"), you shall provide Alpaca with **30 days advance written notice** prior to
> making such User Application available to others. Alpaca reserves the right to restrict
> your User Application's connectivity to the Service and Content, and **may disallow any
> connectivity entirely**, if Alpaca determines the User Application may interfere with
> Alpaca's Services or otherwise be detrimental to Alpaca, as may be determined in
> **Alpaca's sole discretion**."*

**§ Content** — verbatim, and stricter still:

> *"Content is provided **exclusively for personal and noncommercial access and use**. No
> part of the Service or Content may be copied, reproduced, republished, uploaded, posted,
> publicly displayed, encoded, translated, transmitted or distributed in any way
> (including "mirroring") to any other computer, server, web site or other medium for
> publication or distribution or for any commercial enterprise, **without Alpaca's express
> prior written consent**."*

Five further findings compound this:

| # | Finding | Clause | Consequence |
|---|---|---|---|
| 1 | **"Content" expressly includes market data** — *"market data such as quotations for securities transactions and/or last sale information"* | § General | Even derived display of Alpaca quotes falls inside the redistribution ban |
| 2 | *"The Content and the Service are intended for **United States residents only**"* | § U.S. Residents Only | Conflicts with a Canadian-operated platform serving Canadian users |
| 3 | Alpaca *"may terminate these Terms... or suspend your access... **with or without cause at any time and effective immediately**"* | § Termination | Unacceptable as a product dependency |
| 4 | Broad indemnity runs **to** Alpaca for any use of the Content | § Indemnification | Transfers risk to the platform |
| 5 | 'Pro' market data additionally requires the **NASDAQ OMX Global Subscriber Agreement** | § Data Plans | A second, separate exchange redistribution regime |

Also noted: California law with exclusive venue in San Mateo County, and Alpaca may revise
the terms at any time with the user bound by subsequent revisions.

**DETERMINATION: `LICENSE_CLEARED = False`.** There is no default commercial grant, the
30-day-notice path leaves connectivity at Alpaca's sole discretion, and Content
redistribution requires **express prior written consent** that the platform does not hold.
The US-residents-only clause additionally conflicts with this platform's Canadian
operation.

**Consequence: no Alpaca adapter ships in Phase 34.** The `BrokerAdapter` ABC and router
are still built — that seam was the real value of the adapter decision — and the Alpaca
adapter can be written against it later **if and only if** express prior written consent is
obtained. A `LICENSE_CLEARED` constant gates the import path so the adapter cannot be
enabled by configuration alone.

> This is precisely the case your instruction anticipated. "Free" described the price, not
> the licence.

---

## 2. ARCHITECTURE

### 2.1 The central principle: the simulator is the product, adapters are optional

Most systems get this backwards — they build the broker integration first and treat local
simulation as a fallback. Here the ordering is forced by §1.1 and §1.3:

- The **Internal Simulator Adapter is the default and only always-available adapter.**
  It is the product. It works for both markets, needs no credentials, and carries zero
  third-party licensing exposure.
- **External adapters are opt-in, credential-gated, capability-declared, and US-only.**
- **Canadian symbols are hard-blocked from every external adapter at the router level**,
  enforced by schema and tested, never by convention.

```
        Signal / Exit Engines
                 │
                 ▼
   ┌───────────────────────────┐
   │  OrderRequest (normalized)│   symbol, side, qty, type, TTL, strategy_id
   └─────────────┬─────────────┘
                 ▼
   ╔═══════════════════════════╗
   ║  PRE-TRADE RISK GOVERNOR  ║   UMIR 7.1 Parts 7 & 8 control taxonomy
   ║  every check → decision   ║   APPROVE / SOFT_LIMIT / HARD_REJECT
   ║  every decision audited   ║   + reason codes + limits snapshot
   ╚═════════════╤═════════════╝
                 │ (kill switch sits across this seam)
                 ▼
   ┌───────────────────────────┐
   │     EXECUTION ROUTER      │   market-aware, capability-negotiating
   └──────────────┬────────────┘
                  │
                  ▼
   ┌───────────────────────────┐   ┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐
   │    INTERNAL SIMULATOR     │     FUTURE EXTERNAL ADAPTERS
   │  default · only adapter   │   │  gated by LICENSE_CLEARED       │
   │  US + CA · no credentials │     none licensed as of Phase 34:   │
   │  zero licensing exposure  │   │  · Alpaca → ToS prohibits (§1.4)│
   └───────────────────────────┘   │  · IBKR CA → CIRO DMR 3200(§1.1)│
                                   └─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┘
                  │
                  ▼
     Unified Order State Machine
     + append-only audit ledger
                  │
                  ▼
        Feeds / UI / Sentinel 28
```

Both routes to an external venue are closed, for different reasons: Alpaca by contract
(§1.4) and IBKR Canada by regulation (§1.1). The router and ABC are built anyway so that a
licensed venue can be attached later without touching the governor, the state machine, or
the audit ledger.

### 2.2 Order state machine

```
NEW → RISK_APPROVED → SUBMITTED → ACKED → PARTIALLY_FILLED → FILLED
 │         │              │            │            │
 │         │              │            │            └→ EXPIRED
 │         │              │            └→ CANCELLED
 │         │              └→ REJECTED (venue)
 │         └→ RISK_REJECTED (governor)      ← terminal, with reason codes
 └→ INVALID (malformed)
```

Terminal states are immutable. Every transition is written to the audit ledger with a
monotonic sequence number so replay and drift detection are possible.

### 2.3 BrokerAdapter interface

```
class BrokerAdapter(ABC):
    adapter_id: str
    markets: frozenset[str]          # {"US"} or {"US","CA"}
    capabilities: AdapterCapabilities  # order types, fractions, shorts, streaming
    is_external: bool                # True → subject to the CA firewall
    is_configured() -> bool          # credentials present and non-empty
    validate_order(req) -> ValidationResult
    submit_order(req) -> OrderAck
    cancel_order(order_id) -> CancelAck
    get_positions() -> List[Position]
    get_account() -> AccountSnapshot
    poll_events(since) -> List[ExecutionEvent]
```

`AdapterCapabilities` is declared, not inferred: `{market, limit, stop, stop_limit,
bracket, oco, twap, vwap, pov, fractional, short, streaming}`. The router refuses to send
an order type the target adapter does not declare, and reports it as
`CAPABILITY_UNSUPPORTED` rather than silently substituting a market order.

This matters concretely: IBKR paper documents **no VWAP support**, yet
`execution_algo.py` already simulates VWAP. Without capability negotiation the platform
would appear to support an order type it cannot route.

---

## 3. PRE-TRADE RISK GOVERNOR

### 3.1 Control taxonomy — mapped to UMIR 7.1, not invented

| # | Control | UMIR basis | Default (MODERATE) | Action on breach |
|---|---|---|---|---|
| C1 | **Buying power / capital threshold** | Part 7 (a) | order notional ≤ 90% free cash | HARD_REJECT |
| C2 | **Single-position weight cap** | Part 7 (b) | ≤ 18% book value | HARD_REJECT |
| C3 | **Country exposure cap** | Part 7 (b) | US ≤ 75%, CA ≤ 75% | HARD_REJECT |
| C4 | **Unexecuted order value per security** | Part 7 (c) | ≤ 25% book | HARD_REJECT |
| C5 | **Open order count cap** | Part 7 (c) | ≤ 25 concurrent | SOFT_LIMIT |
| C6 | **Per-order notional ceiling** | Part 7 (b) | ≤ $25,000 | HARD_REJECT |
| C7 | **Sector / factor concentration** | platform | ≤ 30% single sector | SOFT_LIMIT |
| C8 | **Correlation-to-book check** | platform | ρ ≤ 0.85 vs existing book | SOFT_LIMIT |
| C9 | **Beta-adjusted exposure** | platform | portfolio β ≤ 1.30 | SOFT_LIMIT |
| C10 | **Max daily gross traded** | platform | ≤ 200% book/day | HARD_REJECT |
| C11 | **Restricted-list / stale-data guard** | platform | data age > 1 session | HARD_REJECT |
| C12 | **Canadian external-routing firewall** | CIRO DMR 3200 | never external | HARD_REJECT |

`SOFT_LIMIT` records the breach, flags the order, and **requires explicit override**
rather than silently resizing. This directly serves your standing requirement that
choosing "Aggressive" must never silently mean larger positions without explaining the
risk: **no control silently resizes an order.** Every soft breach is surfaced with its
limit, the observed value, and the reason code.

### 3.2 Risk profiles parameterize the limits — they do not bypass them

| Control | CONSERVATIVE | MODERATE | AGGRESSIVE |
|---|---|---|---|
| C1 buying power | 70% | 90% | 95% |
| C2 position cap | 10% | 18% | 25% |
| C3 country cap | 60% | 75% | 85% |
| C6 per-order ceiling | $10k | $25k | $40k |
| C9 portfolio β cap | 1.10 | 1.30 | 1.50 |
| C10 daily gross | 100% | 200% | 300% |

"AGGRESSIVE" widens the envelope; it never disables a control, and the UI shows the exact
limits in force. This is the same discipline the risk-management requirement demands.

### 3.3 Kill switch (UMIR 7.1 Part 8)

A single `KILL_SWITCH_ENGAGED` flag that, when set:

- rejects every new order at the governor with reason `KILL_SWITCH_ACTIVE`;
- emits a `kill_switch_event` audit row (who/when/reason/scope);
- **immediately disables the automated order system** — no order reaches any adapter,
  including the internal simulator;
- and is surfaced prominently in the UI with the reason and the timestamp.

Scope options: `ALL`, `EXTERNAL_ONLY`, `SYMBOL:<x>`, `STRATEGY:<y>`.

### 3.4 Annual AOS testing attestation (UMIR 7.1 Part 8)

Policy 7.1 Part 8 requires that automated order systems be tested before use and **at
least annually**, with a written record. The platform will therefore ship:

- `scripts/aos_annual_attestation.py` — runs a deterministic control-coverage suite,
  writes a signed JSON attestation to `data/attestations/aos_YYYY.json`, and records the
  control matrix, test counts, pass/fail, and version hashes;
- a UI badge showing **attestation date and days until expiry**, turning
  `EXPIRED` after 365 days.

This is a genuine compliance artifact, not theatre: it is the written record the rule
contemplates.

---

## 4. DATABASE SCHEMA

| Table | Purpose | Key columns |
|---|---|---|
| `execution_order` | Full lifecycle, one row per order | `order_id PK`, `client_order_id UNIQUE`, `adapter_id`, `symbol`, `market`, `side`, `qty`, `order_type`, `state`, `strategy_id`, `risk_profile`, `submitted_at`, `terminal_at`, `rejection_reason`, `state_seq` |
| `execution_fill` | Partial-fill accounting | `fill_id PK`, `order_id FK`, `qty`, `price`, `fee`, `liquidity_flag`, `filled_at` |
| `execution_state_transition` | Append-only audit ledger | `seq PK`, `order_id FK`, `from_state`, `to_state`, `reason_code`, `actor`, `at` |
| `risk_governor_decision` | Every control evaluation | `decision_id PK`, `order_id FK`, `control_id`, `verdict`, `limit_value`, `observed_value`, `breach`, `overridden`, `limits_snapshot JSON`, `decided_at` |
| `execution_account` | Per-adapter account snapshots | `adapter_id`, `cash_free`, `cash_reserved`, `equity`, `buying_power`, `snapshot_at` |
| `kill_switch_event` | Kill-switch audit | `event_id PK`, `scope`, `engaged`, `reason`, `actor`, `at` |
| `aos_attestation` | Annual testing record | `year PK`, `attested_at`, `controls_tested`, `passed`, `failed`, `artifact_path`, `code_version` |

`cash_reserved` is the column that closes Gap 1: buying power is computed as
`cash_free − Σ(reserved notional of open orders)`, so C1 becomes enforceable.

All tables are append-only in practice; no `UPDATE` deletes history, and
`execution_state_transition` has no delete path at all.

---

## 5. PHASE-BY-PHASE PLAN

### Phase 34.1 — Order Model, State Machine & Audit Ledger
**Objective:** represent an order honestly before executing one.
**Features:** normalized `OrderRequest`; the 11-state machine (5 non-terminal, 6 terminal); terminal-state immutability;
append-only transition ledger; deterministic `client_order_id` generation.
**Architecture:** `src/execution/order_model.py`, `src/execution/state_machine.py`.
**DB:** `execution_order`, `execution_state_transition`.
**Tasks:** model + transitions + illegal-transition rejection + ledger writer.
**Testing:** every legal transition accepted; every illegal transition raises; ledger
monotonic; terminal states immutable under re-entry.
**Security:** `client_order_id` collision rejection; no PII in ledger.
**Cost:** $0.
**DoD:** an order can traverse its full lifecycle with a complete, replayable audit trail,
and illegal transitions are impossible.

### Phase 34.2 — Pre-Trade Risk Governor
**Objective:** make every pre-trade limit enforceable and explainable.
**Features:** controls C1–C12; three risk-profile parameterizations; `APPROVE` /
`SOFT_LIMIT` / `HARD_REJECT` verdicts; reason codes; limits snapshot per decision;
override requiring explicit acknowledgement.
**Architecture:** `src/execution/risk_governor.py`; consumes Phase 26 portfolio analytics
for C7–C9 and Phase 11 FX for C3.
**DB:** `risk_governor_decision`, `execution_account`.
**Tasks:** control registry, per-control evaluators, verdict aggregation (most severe
wins), snapshot serialization.
**Testing:** each control fires at its documented boundary and **not** one tick inside;
verdict aggregation; no silent resizing (assert quantity is unchanged in every soft case);
profile parameterization.
**Security:** overrides are attributed and audited; limits are read from config, never
from request payload.
**Cost:** $0.
**DoD:** every order carries a per-control decision record; no order can bypass the
governor; soft breaches never resize silently.

### Phase 34.3 — Internal Simulator Adapter (default)
**Objective:** an always-available execution venue with no credentials and no licensing
exposure.
**Features:** next-bar-open fills reusing the platform's existing bar data; per-side
friction; partial fills; exchange fees via the existing `get_exchange_fee`; realistic
rejection of market orders outside the bar range; both US and CA.
**Architecture:** `src/execution/adapters/internal_simulator.py`; reuses
`execution_algo.get_exchange_fee` rather than duplicating fee logic.
**DB:** `execution_fill`.
**Tasks:** adapter implementation, fill simulation, fee application, cash reservation.
**Testing:** fill price always within `[low, high]` of the fill bar; friction applied both
sides; fees match the existing fee table exactly; buying power decreases on reservation
and releases on cancel; **no look-ahead** — fills use only bars dated after submission.
**Security:** n/a (no external surface).
**Cost:** $0.
**DoD:** the paper book cannot be over-drawn; every fill is reproducible from the seed.

### Phase 34.4 — Adapter Seam, Capability Negotiation & the Canadian Firewall
**Objective:** build the seam at which a licensed venue could attach, while making it
structurally impossible to route a Canadian symbol externally or to enable an unlicensed
venue by configuration.

> **Scope changed after the ToS review (§1.4).** No external adapter ships. The Alpaca
> adapter is not written: its terms prohibit commercial use and Content redistribution
> without express prior written consent. IBKR Canada is barred by CIRO DMR 3200 (§1.1).
> What ships is the ABC, the capability model, the router, and both firewalls.

**Features:** `BrokerAdapter` ABC; `AdapterCapabilities` declaration; market-aware router;
`CAPABILITY_UNSUPPORTED` rejection; **Canadian external-routing firewall**;
**`LICENSE_CLEARED` venue firewall**; registry that refuses unlicensed adapter classes.
**Architecture:** `src/execution/adapters/base.py`, `src/execution/router.py`.
**DB:** none new (`execution_account` remains multi-adapter-ready).
**Tasks:** ABC + capability model + router + registry + CA firewall + license firewall +
graceful degradation for unsupported order types.
**Testing:** CA symbol blocked from every external adapter on every code path; adapter
class with `LICENSE_CLEARED = False` cannot be registered or instantiated; unsupported
order type yields `CAPABILITY_UNSUPPORTED`, never a silent market-order substitution;
platform fully functional with the internal simulator as the sole adapter.
**Security:** credentials from environment only, never config files or logs; no secret in
any audit row; the licence gate is a code constant, not a setting, so it cannot be flipped
by environment or payload.
**Cost:** $0.
**DoD:** a Canadian symbol can never reach an external adapter, and an unlicensed adapter
can never be registered — both verified by test, not convention.

### Phase 34.5 — Predicate 28, Feeds, UI & Annual Attestation
**Objective:** surface execution risk in the terminal and close the compliance loop.
**Features:** **Predicate 28**; `execution_gateway.json` feed; terminal workstation tab;
kill-switch control; AOS attestation script + badge.
**Architecture:** `src/engine/execution_gateway.py`; sentinel extension; edge exporter;
UI tab 21.
**DB:** `kill_switch_event`, `aos_attestation`.
**Tasks:** governor metrics → sentinel; feed schema; exporter + build staging; UI;
attestation script.
**Testing:** Predicate 28 boundaries exclusive; linter zero violations; feed schema
validation; clean-route byte equality; UI sync; attestation expires after 365 days.
**Security:** kill switch cannot be disengaged without an audit record; feed contains no
credentials or account numbers.
**Cost:** $0.
**DoD:** full suite green against the current baseline of **590 passed, 1 skipped**;
attestation artifact generated.

---

## 6. PREDICATE 28 — `PRE_TRADE_RISK_BREACH`

**Trigger:** governor hard-rejections in the current session ≥ 3, **or** rejected notional
exceeds 25% of submitted notional.
**Severity:** `WARNING` → `CRITICAL` when rejections ≥ 10.
**Action required:** `REVIEW_POSITION_SIZING`.
**Rationale:** this is decision-support, not an execution error. Repeated hard rejections
mean the systematic signals are generating orders the declared risk envelope cannot
absorb — a genuine, actionable finding about strategy/risk alignment, and exactly the kind
of signal this platform exists to surface.
**Boundary:** exclusive, consistent with Predicates 26 and 27.

---

## 7. WHAT IS EXPLICITLY OUT OF SCOPE

- **No live-money trading.** Ever. Paper only.
- **No Canadian order routing to any external venue.** Regulatory, not technical
  (CIRO DMR 3200, §1.1).
- **No Alpaca integration.** Contractual, not technical (§1.4) — commercial use and
  Content redistribution are both prohibited absent express prior written consent.
- **No external broker adapter of any kind ships.** The internal simulator is the only
  adapter. The ABC and router exist so a licensed venue can be attached later.
- **No order routing to a broker from this sandbox.** No credentials exist here. The
  internal simulator is what runs.
- **No change to signal generation.** The governor gates orders; it does not alter signals.
- **No profitability claim.** Execution analytics describe fill quality and limit
  breaches, not expected returns.

---

## 8. COST SUMMARY

| Item | Cost |
|---|---|
| Internal simulator adapter | $0 |
| Alpaca paper API | **Not used** — ToS prohibits commercial use (§1.4) |
| IBKR paper API | **Not used** — CIRO DMR 3200 bars CA routing (§1.1); no funded account |
| Additional infrastructure | $0 — SQLite, same process, same deploy |
| Build-time impact | Est. +3–6s to feed export |

The phase adds **no** third-party cost, because it adds no third-party dependency.

---

## 9. DECISIONS — RESOLVED 2026-10-02

| # | Question | Your decision | Effect |
|---|---|---|---|
| 1 | External adapter scope | Internal simulator + Alpaca paper | **Superseded by decision 2** — see below |
| 2 | Alpaca ToS handling | Review the ToS first | Reviewed in full; **commercial use prohibited** |
| 3 | Governor posture on soft breaches | Flag and continue | Soft breaches flag, never silently resize; C1/C6/C10/C12 always hard-reject |

**Decision 1 was superseded by the outcome of decision 2.** You chose to review the Alpaca
terms before writing adapter code, and that review found no commercial grant: use is
"solely for your own personal and non-commercial purposes," Content redistribution needs
"Alpaca's express prior written consent," and the service is "intended for United States
residents only." Building an adapter against those terms would have created exactly the
exposure your standing instruction exists to prevent.

**Revised scope:** the `BrokerAdapter` ABC, capability model, router, and both firewalls
are built; the **internal simulator is the only adapter that ships**. The seam remains, so
a properly licensed venue can be attached later without touching the governor, state
machine, or audit ledger.

This preserves the architectural intent of your decision 1 — a real adapter boundary with
capability negotiation — while not shipping an integration the licence does not permit.
