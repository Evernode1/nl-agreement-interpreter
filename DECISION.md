# AgreementInterpreter Decision Record

## The product

A reusable "plain English -> enforceable state machine" primitive. One party proposes an
agreement (RENT, FREELANCE_MILESTONE, NDA, or a type-agnostic GENERIC) with both a natural-language
text and the structured numbers/dates that will actually govern money movement. A consensus round
checks each consequential structured term against the text, one verdict per term, before the
counterparty can accept. Once ACTIVE, ordinary lifecycle actions (paying rent, submitting and
approving milestones) are pure deterministic code; a second consensus round only activates when a
party disputes that some real-world action complied with the agreed text, and it decides from
evidence the contract fetches itself -- party-written descriptions are unverified claims.

## Counterfactual: why not just let the model extract the numbers directly

The obvious first design has the model read "$1,200 a month, due on the 1st" out of prose and
produce `monthly_rent_wei=..., due_day_of_month=1` directly, the same way ProofOfLifeVault's
life-signal check classifies a fetched page. Two things make that the wrong choice here, and both
are specific to this contract's subject matter rather than being general objections to extraction:

1. **There is no independent second source.** A life-signal check or a cross-chain corroboration
   round can cross-check one source's claim against another's. A single piece of prose has no
   second, independently-fetchable copy to compare against -- if the model misreads "$1,200" as
   1200 wei instead of `1200 * 10**18`, or silently drops a zero, there is no mechanism in this
   series' existing toolkit that would catch it.
2. **The number IS the thing at stake.** A life-signal misclassification delays an inheritance
   release a bit longer -- annoying, not harmful. A misread rent amount or milestone payment
   *is* the harm: it directly changes how much money moves, to whom, and when.

So this contract inverts the usual flow: the proposing party supplies the exact numbers directly,
structurally, as ordinary function arguments -- the same trust level as any other smart contract
call -- and the model's job is narrowed to a question it cannot get numerically wrong: does this
prose support these numbers, or does it clearly contradict them? That is a classification task with
the same shape as every other consensus round in this series, just applied to consistency-checking
instead of extraction.

## Why CONSISTENT / INCONSISTENT / COULD_NOT_DETERMINE, per term, and how omission is treated

Each consequential structured term is judged on its own. INCONSISTENT is reserved for a genuine
clash (the text names a different rent figure, due day, amount, or deadline) -- that is the failure
mode this contract most needs to catch: a proposer's typo or deliberate misstatement of the true
terms. A term the text does not state, or states too vaguely or in a unit that can't be verified
(a fiat price with no ETH/wei figure), is COULD_NOT_DETERMINE: it is not an accusation of
contradiction, but it is also not a pass, so the agreement lands in NEEDS_REVIEW and cannot be
accepted until the text supports the term. (v1.0/v1.1 treated omission as harmless; v1.2 does not,
because an unstated money-moving term is exactly a term the agreement text does not back.)

## Why the safe default differs between a milestone dispute and every other dispute

This is the sharpest asymmetry-related design decision in this contract, and it cuts a different
way than every prior contract in this series, which is worth stating plainly rather than papering
over with a single rule reused everywhere:

- **`dispute_milestone` has real escrowed money on BOTH sides of the question.** A confident,
  evidence-backed CONSISTENT_WITH_TERMS verdict pays the contractor; nothing pays the client back automatically on
  the opposite verdict -- the milestone simply stays DISPUTED, locked, forever if the attempt cap
  is reached without resolution. There is no third option that is safe to default to: auto-paying
  on an ambiguous verdict risks paying for an inadequate deliverable, and auto-refunding on an
  ambiguous verdict risks stiffing a contractor who actually delivered. Freezing and requiring
  off-chain resolution is the only default that doesn't unilaterally decide a disputed sum in
  either party's favor.
- **`raise_dispute` (RENT / NDA / GENERIC) has no escrowed money riding on the verdict at all.**
  Rent already cleared the moment it was paid -- a dispute here is a status label, not a fund
  movement, and cannot claw back a payment already made (see "Honest limitations"). An NDA breach
  finding is likewise a label, not a transfer. The only asymmetric harm on this side is a wrongful
  BREACH_CONFIRMED sitting on the record indefinitely, which is why `raise_dispute` safely defaults
  an attempt-cap-exhausted, still-ambiguous dispute to DISMISSED -- the equivalent of "not proven"
  rather than leaving an accusation open forever with no path to resolution.

Same underlying instinct as every asymmetric design in this series -- don't let ambiguity resolve
toward the more harmful outcome -- applied correctly to two structurally different situations
instead of copying one rule everywhere it doesn't fit.

## Why NDA breach is a status label, not an automatic fund clawback

An NDA in this contract has no escrow at all; `raise_dispute` only ever sets `status = BREACHED`
and leaves `dispute_status = BREACH_CONFIRMED` as the on-chain record. This is a deliberate scope
limit, not an oversight -- an NDA's real-world remedy (damages, injunctive relief) is not something
any smart contract can execute unilaterally, and pretending otherwise (e.g. an automatic penalty
transfer) would require this contract to also decide a damages AMOUNT, reintroducing exactly the
"the model must not determine a number that moves money" problem this contract's entire design
exists to avoid. A confirmed breach is a trustworthy fact other systems (an off-chain court process,
a separate penalty contract with its own, independently-reasoned amount) can build on -- exactly
the same "reusable fact" pattern CrossChainEventCorroborator's `is_confirmed` established for a
different domain.

## Why there is no admin

Every parameter here belongs to exactly one agreement between exactly two parties; nothing about
one agreement's structured terms, dispute history, or escrow ever touches another agreement's
economics or safety margins. The only genuinely shared parameters (the cooldowns, the attempt cap,
the milestone/text length bounds) are protocol-wide constants identical for every agreement -- the
same category ProofOfLifeVault and CrossChainEventCorroborator already established needs no owner.

## v1.1 self-review: three bugs the first pass's own docs exposed

Re-reading the contract against its own README/DECISION claims after v1.0 turned up three real
gaps, not just polish -- each is a case where the written design said one thing and the code did
another:

1. **`approve_milestone` couldn't actually do what the docs said it could.** v1.0's README claimed
   "the client can still call `approve_milestone` directly at any time" as the escape hatch for a
   milestone a dispute round couldn't resolve. The code only accepted a milestone in `SUBMITTED`
   status -- once a dispute round moved it to `DISPUTED`, that method was no longer callable at
   all. Fixed by accepting `DISPUTED` too; paying more than a consensus round required never needed
   the contractor's consent, so there was no safety reason for the original restriction -- it was
   simply a bug.
2. **There was no way to resolve a stuck milestone toward a refund, only toward eventual payment or
   permanent freeze.** The "honest limitation" that a `DISPUTED` milestone locks funds indefinitely
   was true, but incompletely -- it locked them in one direction only, since only the pay-the-
   contractor path had a method at all. `confirm_milestone_refund` fills the other half: both
   parties must independently confirm before the escrow returns to the client, so it adds a real
   resolution path without ever letting one side unilaterally claim funds. This is the same
   asymmetric caution as everywhere else in this series applied to who gets to decide, not just
   what gets decided: a 2-of-2 requirement is the only way to add a refund path that can't itself
   become the unilateral-seizure problem the whole DISPUTED-locks-forever design was built to avoid.
3. **A RENT or GENERIC agreement could only ever be disputed once, permanently.** v1.0 gated
   `raise_dispute` on `dispute_status in (NONE, OPEN)`, so once a round resolved to DISMISSED or
   BREACH_CONFIRMED, that single dispute "slot" was used up forever, even for an agreement meant to
   run for a year. Fixed by making dispute rounds repeatable -- a resolved round can be followed by
   a fresh one, each restarting its own cooldown and attempt budget -- while a new `breach_count`
   field accumulates every confirmed breach across every round, so opening round two never erases
   what round one established. An NDA is unaffected in practice: its first `BREACH_CONFIRMED`
   already flips `status` away from `ACTIVE`, which `raise_dispute` still requires.

None of these three changes touch the core asymmetric-aggregation logic or the model/code trust
boundary from the sections above -- they correct places where the *plumbing* around that logic
didn't yet match what the design already called for.

## v1.2 steward-review fixes

The v1.1 submission was rejected for two design gaps, both real:

1. **The freelance consistency check only saw "Number of milestones".** Milestone descriptions,
   amounts and deadlines -- the values that actually move escrow -- never reached the model, so they
   could contradict the agreement text and still pass consensus. Now every consequential term is a
   separate, id-labelled line in the prompt and gets its own verdict (`_consequential_terms`).
   Amounts are shown as wei plus a code-computed ETH equivalent, so the model compares numbers it can
   actually read rather than converting 18-digit integers. The aggregation is code-side and
   fail-closed: a missing/duplicated/unknown verdict is COULD_NOT_DETERMINE, any INCONSISTENT wins,
   and only all-CONSISTENT passes. This deliberately tightens one v1.0 rule: an *unstated*
   consequential term is no longer a pass. It is still not INCONSISTENT (no accusation of
   contradiction), but it lands in NEEDS_REVIEW so the proposer must put the term in the text
   before the counterparty can accept. GENERIC agreements have no structured terms, so they skip the
   round.
2. **Dispute paths decided factual questions from party-written descriptions.** A milestone could be
   paid, or a breach confirmed, because one side's prose sounded plausible. Now the contract acquires
   evidence itself inside the consensus round (`gl.nondet.web.render`, fetched independently by each
   validator): the contractor commits a deliverable URL at `submit_milestone` (before any dispute
   exists), the client may add counter-evidence, and `raise_dispute` requires 1-3 evidence URLs.
   Party text is passed to the model explicitly labelled as unverified claims. Fail-closed in code:
   if the primary evidence can't be fetched, or the model doesn't affirm that the *evidence* (not a
   claim) supports its verdict, the verdict becomes COULD_NOT_DETERMINE -- so nothing is paid, no
   breach is confirmed, and the existing safe defaults (milestone stays locked; rent/NDA/GENERIC
   dismisses at the attempt cap) apply unchanged. URLs must be https, no whitespace, public hosts.

Unchanged: the model never determines a number that moves money, the two different safe defaults,
mutual-refund and approve-anyway resolution paths, and the absence of an admin.

## Honest limitations

- **A dispute cannot claw back rent already paid, or reverse a milestone already approved.** Both
  `record_rent_payment` and `approve_milestone` forward funds immediately on the happy path,
  before any dispute could exist. `raise_dispute` on a RENT agreement can only create an on-chain
  record of a confirmed breach for off-chain recourse; it is not a refund mechanism.
- **The consistency check is best-effort English comprehension, not legal review.** It catches
  clear numeric/date contradictions between the submitted text and parameters; it does not, and
  cannot, evaluate whether the underlying agreement is legally enforceable, unconscionable, or
  compliant with any jurisdiction's law. `terms_summary`-level review by the parties themselves
  remains essential before accepting.
- **A milestone dispute with no confident consensus verdict has no AUTOMATIC resolution**, by
  design (see above) -- this contract deliberately does not include an arbitrator or admin
  override. `approve_milestone` (client pays anyway) and `confirm_milestone_refund` (both parties
  agree to return the funds) are always available as manual resolution paths once the parties agree
  off-chain, but if they can't agree, the funds genuinely stay locked -- there is no on-chain
  tie-breaker, and this contract does not attempt to be one.
- **Evidence is party-selected and unauthenticated.** The contract reads the evidence itself, but it
  cannot prove a chosen page is authoritative, or unchanged since it was committed. Fetched pages are
  untrusted text; a fetch failure yields no verdict rather than a guess.
- **No identity verification on either party.** `party_a`/`party_b` are wallet addresses; nothing
  ties them to real-world identities, the same limitation every wallet-addressed agreement in this
  space shares.
