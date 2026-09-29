# AgreementInterpreter

A reusable "plain English -> enforceable state machine" primitive. One party proposes an
agreement -- a rent lease, a freelance milestone contract, an NDA, or a type-agnostic generic
agreement -- by submitting both a natural-language text and the structured numbers/dates that will
actually govern money movement. A GenLayer consensus round checks that EACH consequential structured
term is supported by the text (one verdict per term, aggregated by code) before the counterparty can
accept. Once active, ordinary lifecycle actions run as deterministic code with no AI involved; a
second consensus round only activates when a party disputes whether a specific real-world action
complied with the agreed text -- and it decides from evidence the contract fetches itself, never
from either party's written description alone.

## Reviewer summary

- **Live app**: not included in this submission -- see "Scope" below.
- **Source**: part of this repository, under `nl-agreement-interpreter/`.
- **Contract**: add StudioNet contract address here when deployed.
- **Main workflow**: party A calls `propose_agreement(counterparty, type, raw_text, ...structured
  terms...)`, which immediately runs a per-term consensus consistency check -> if every term is
  CONSISTENT, party B calls `accept_agreement` (for FREELANCE_MILESTONE, only after the client funds
  escrow) -> the agreement runs its deterministic lifecycle (`record_rent_payment` /
  `submit_milestone` with a committed deliverable URL + `approve_milestone`) -> if either party
  contests an action, `dispute_milestone` or `raise_dispute` runs a second consensus round in which
  the contract fetches the evidence URLs and judges the action against the agreed terms; funds move
  or a breach is confirmed only when that fetched evidence supports the verdict.

## Why this is a genuine Intelligent Contract, not a form-filling dApp

A plain "fill in these fields and both parties click accept" contract needs no AI at all -- but it
also can't tell a proposer that the numbers they typed don't actually match the agreement text they
pasted in, and it has no way to resolve "did this deliverable satisfy this clause" without an
off-chain arbitrator. GenLayer's consensus machinery adds exactly those two things: a term-by-term
faithfulness check between free text and structured terms before anyone commits to the agreement,
and an evidence-grounded compliance judgment when a party disputes a specific action (the contract
fetches the evidence; party descriptions are only claims) -- both while never letting the model
itself determine a number that moves money (see DECISION.md's "Counterfactual"
section for why that specific line is drawn where it is).

## The asymmetric-risk design principle this contract is built around

Unlike ProofOfLifeVault and CrossChainEventCorroborator, which each apply one asymmetric rule
consistently, this contract applies two DIFFERENT safe defaults depending on what is actually at
stake in a given dispute:

1. **A milestone dispute has real escrowed money on both sides of the question.** Paying the
   contractor and refunding the client are equally consequential, equally hard-to-reverse actions,
   so neither is a safe default on an exhausted, unresolved dispute -- the milestone simply stays
   locked, an honest, stated limitation.
2. **A RENT/NDA/GENERIC dispute has no money riding on the verdict itself.** Rent already cleared
   when it was paid; a confidentiality breach is a status label. The only asymmetric harm there is
   a wrongful accusation sitting open indefinitely, so an exhausted, unresolved dispute safely
   defaults to DISMISSED.

See DECISION.md for the full reasoning behind why the same instinct ("don't let ambiguity resolve
toward the more harmful outcome") produces two different concrete rules here.

## Architecture

- `contracts/AgreementInterpreter.py` -- a single Intelligent Contract: proposal, revision, and
  acceptance with a per-term consensus consistency check (`_consequential_terms` builds one
  id-labelled line per money-moving term, `_consensus_check_consistency` gets one verdict each, and
  `_normalize_term_verdicts` / `_aggregate_term_verdicts` enforce full coverage in code) gating every
  transition into an accept-able state; deterministic, non-consensus RENT payment and
  FREELANCE_MILESTONE escrow/approval lifecycles; a shared evidence-backed action-interpretation
  consensus round (`_consensus_interpret_action`, which fetches evidence URLs via
  `gl.nondet.web.render` and fails closed without acquired evidence) reused by both the milestone
  dispute path and the RENT/NDA/GENERIC dispute path, with the safe default tailored to what each
  path has at stake. Evidence URLs are stored on the milestone / agreement and exposed in the views.
- `tests/direct/` -- direct-VM `gltest` tests covering proposal validation per agreement type,
  the per-term consistency-check matrix (a single contradicted or unstated milestone amount /
  deadline / description, rent or NDA term flags or blocks the agreement; a term the model omits
  fails closed; INCONSISTENT outranks COULD_NOT_DETERMINE; GENERIC skips the round), revision and
  re-check, acceptance gating (including escrow-before-accept for FREELANCE_MILESTONE), the RENT
  on-time/late payment and fee math, the milestone submit -> approve and submit -> dispute ->
  auto-pay-or-lock paths, evidence rules (https/public-host URL validation, no payment or breach
  when evidence can't be fetched or the model relies on claims only, evidence URLs recorded), the
  RENT/NDA/GENERIC dispute path's confirm/dismiss/exhausted-cap-dismisses matrix, and views.

### Contract methods

| Method | Kind | Consensus round? | What it does |
| --- | --- | --- | --- |
| `propose_agreement(...)` | write, permissionless | **Yes -- per-term consistency check** (skipped for GENERIC) | Opens a new agreement; immediately checks each structured term against the text. |
| `revise_agreement(...)` | write, party A only | **Yes -- per-term consistency check** (skipped for GENERIC) | Amends a not-yet-accepted agreement and re-checks every term. |
| `accept_agreement(id)` | write, party B only | No | Activates an agreement whose every term passed the consistency check (and, for FREELANCE_MILESTONE, whose escrow is funded). |
| `reject_agreement(id)` | write, either party | No | Withdraws a not-yet-active agreement; refunds any escrow. |
| `record_rent_payment(id)` | payable write, party B only | No | RENT only. Exact on-time or late+fee amount, forwarded immediately to party A. |
| `complete_lease(id)` | write, permissionless | No | RENT only. Marks the agreement COMPLETED once the lease term has elapsed. |
| `fund_milestone_escrow(id)` | payable write, party A only | No | FREELANCE_MILESTONE only. Deposits the exact sum of all milestone amounts. |
| `submit_milestone(id, index, description, deliverable_url)` / `approve_milestone` | write | No | The deterministic happy path: contractor submits (committing an https evidence URL for the deliverable), client approves and is paid. |
| `dispute_milestone(id, index, reason, client_evidence_url)` | write, party A only | **Yes -- evidence-backed interpretation** | Client's alternative to approving: the contract fetches the contractor's deliverable URL (plus the client's optional URL) and consensus decides pay-out or locks the milestone. Never decided from party-written text alone. |
| `confirm_milestone_refund(id, index)` | write, either party | No | Mutual-consent path for a locked DISPUTED milestone: refunds the client once BOTH parties confirm. |
| `raise_dispute(id, description, evidence_urls)` | write, either party | **Yes -- evidence-backed interpretation** | RENT/NDA/GENERIC only, repeatable over the agreement's life. Consensus decides BREACH_CONFIRMED, DISMISSED, or stays OPEN; `breach_count` accumulates across rounds. |
| `get_agreement` / `get_milestone` / `list_milestones` / `list_agreements` | view | No | Reads. |

## Scope of this submission

This submission is **Contract + Tests**. A frontend (draft an agreement in plain language, review
the consistency check, track milestones, file a dispute) is a natural next step but is not
included here.

## v1.1 upgrade notes

A self-review pass found and fixed three gaps between what v1.0's docs promised and what the code
actually did -- see DECISION.md's "v1.1 self-review" section for the full reasoning:

- `approve_milestone` now also accepts a `DISPUTED` milestone (v1.0 only accepted `SUBMITTED`,
  silently breaking the documented "client can pay anyway" escape hatch).
- `confirm_milestone_refund` is new -- a 2-of-2 mutual-consent path that lets a locked `DISPUTED`
  milestone resolve toward refunding the client, which v1.0 had no method for at all.
- `raise_dispute` (RENT/NDA/GENERIC) now supports repeated dispute rounds over an agreement's life,
  with a new `breach_count` field preserving history across rounds, instead of permanently locking
  after the agreement's first dispute ever resolved.

## v1.2 upgrade notes (steward review fixes)

The v1.1 submission was rejected for two reasons; both are fixed in v1.2 (see DECISION.md,
"v1.2 steward-review fixes"):

- **Every consequential structured term is now checked independently.** The consistency round
  returns one verdict per term id (rent, due day, lease dates, late fee, NDA duration, escrow
  total, milestone count, and each milestone's description, amount and deadline). Code enforces
  coverage: a term the model skips or mislabels counts as COULD_NOT_DETERMINE, any INCONSISTENT term
  flags the agreement, and only all-CONSISTENT reaches `PROPOSED`. A money-moving term the text never
  states can no longer pass. GENERIC agreements have no structured terms and skip the round.
- **Factual dispute decisions require contract-acquired evidence.** `dispute_milestone` and
  `raise_dispute` fetch evidence URLs inside the consensus round (each validator fetches
  independently). Party descriptions are unverified claims. Escrow release and BREACH_CONFIRMED
  only take effect when the required evidence was actually fetched *and* the model reports the
  evidence itself (not a claim) supports the verdict; otherwise the verdict is forced to
  COULD_NOT_DETERMINE in code.
  The evidence URLs (`deliverable_url`, `client_evidence_url`, `dispute_evidence_urls`) are stored
  on-chain and returned by `get_milestone` / `get_agreement` so every decision is auditable.

## Honest limitations

- **Evidence URLs are party-chosen.** The contract guarantees it reads the evidence itself, not that
  the chosen page is authoritative or unchanged since it was committed; pages are treated as
  untrusted text. Pages needing login or JavaScript-only content may fail to fetch (which safely
  yields no verdict).
- **Prose priced in fiat can't pass consistency.** Amounts are compared against wei/ETH equivalents;
  text that only states a fiat price leaves the term COULD_NOT_DETERMINE until the text states the
  ETH/wei amount.
- **A dispute cannot claw back funds already moved.** See DECISION.md.
- **The consistency check is English comprehension, not legal review**, and does not evaluate
  enforceability, unconscionability, or jurisdictional compliance.
- **A milestone dispute with no confident consensus verdict has no automatic resolution**, by
  design -- there is deliberately no arbitrator or admin override. `approve_milestone` and
  `confirm_milestone_refund` are always available once the parties agree off-chain; if they can't
  agree, the funds genuinely stay locked.
- **No identity verification** -- parties are wallet addresses only.
