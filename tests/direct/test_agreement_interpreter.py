"""Direct-VM tests for AgreementInterpreter (v1.2).

Consensus rounds are mocked: `mock_consistency` returns one verdict per structured term id (the
contract aggregates them in code), and `mock_interpretation` mocks both the fetched evidence pages
(`mock_evidence`) and the model's evidence-backed dispute verdict. Fixture agreement texts state
the same figures as the structured terms the helpers submit, so they read like real agreements.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import warp_to

NOW = "2099-01-01T00:00:00Z"

RENT_TEXT = (
    "This is a residential lease between the landlord and the tenant. Rent is 1000 wei per month, "
    "payable by the 5th of each month, from 2099-01-01 to 2099-12-31. A payment made after the 5th "
    "carries a late fee of 10% of one month's rent."
)
FREELANCE_TEXT = (
    "This is a freelance services agreement between the client and the contractor, delivered in two "
    "milestones. Milestone 1, design mockups, pays 600 wei and is due 2099-03-01. Milestone 2, the "
    "final build, pays 400 wei and is due 2099-06-01. Each is payable upon the client's approval."
)
NDA_TEXT = (
    "This is a mutual non-disclosure agreement. The receiving party agrees to keep all "
    "confidential information disclosed by the disclosing party strictly confidential for 365 days."
)
GENERIC_TEXT = (
    "This is a general agreement between two parties covering an arrangement not captured "
    "by any of the other structured agreement types offered by this contract."
)

CONSISTENCY_PROMPT_RE = r".*FAITHFUL REFLECTION.*"
INTERPRET_PROMPT_RE = r".*judging whether a specific real-world action.*"


def _iso_plus(iso: str, seconds: int) -> str:
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (dt + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


ALL_TERM_IDS = (
    ["monthly_rent", "due_day", "lease_start", "lease_end", "late_fee", "confidentiality_duration",
     "escrow_total", "milestone_count"]
    + [f"milestone_{i}_{field}" for i in range(5) for field in ("description", "amount", "deadline")]
)

EVIDENCE_URL = "https://evidence.example.com/deliverable"
EVIDENCE_URL_2 = "https://evidence.example.org/report"
EVIDENCE_RE = r".*evidence\.example\.(com|org).*"


def mock_consistency(direct_vm, verdict="CONSISTENT", reason="Reviewed.", overrides=None, omit=()):
    """Mocks the per-term consistency round. Ids not belonging to the agreement under test are
    ignored by the contract, so one mock covers every agreement type. `overrides` maps a term id to
    a different verdict; `omit` lists term ids the model 'forgot' to return."""
    overrides = overrides or {}
    terms = [
        {"id": tid, "verdict": overrides.get(tid, verdict), "passage": "text"}
        for tid in ALL_TERM_IDS if tid not in omit
    ]
    direct_vm.mock_llm(CONSISTENCY_PROMPT_RE, json.dumps({"terms": terms, "rationale": reason}))


def mock_evidence(direct_vm, body="Repository contains the delivered design mockups, all pages present."):
    direct_vm.mock_web(EVIDENCE_RE, {"response": {"status": 200, "headers": {}, "body": body}, "method": "GET"})


def mock_interpretation(direct_vm, verdict="CONSISTENT_WITH_TERMS", reason="Reviewed.", supports=True, fetch=True):
    """Mocks the evidence-backed dispute round. `fetch=False` simulates the evidence sources
    returning nothing; `supports=False` simulates a model that could only rely on party claims."""
    mock_evidence(direct_vm, body="Repository contains the delivered design mockups, all pages present." if fetch else "")
    direct_vm.mock_llm(
        INTERPRET_PROMPT_RE,
        json.dumps({"verdict": verdict, "evidence_supports_verdict": supports, "rationale": reason}),
    )


def propose(
    contract, direct_vm, sender, counterparty, agreement_type="RENT", raw_text=RENT_TEXT,
    monthly_rent_wei=0, due_day_of_month=0, lease_start="", lease_end="", late_fee_bps=0,
    milestone_descriptions=None, milestone_amounts=None, milestone_deadlines=None,
    confidentiality_days=0,
):
    if milestone_descriptions is None:
        milestone_descriptions = []
    if milestone_amounts is None:
        milestone_amounts = []
    if milestone_deadlines is None:
        milestone_deadlines = []
    direct_vm.sender = sender
    return contract.propose_agreement(
        counterparty, agreement_type, raw_text, monthly_rent_wei, due_day_of_month,
        lease_start, lease_end, late_fee_bps, milestone_descriptions, milestone_amounts,
        milestone_deadlines, confidentiality_days,
    )


def propose_rent(contract, direct_vm, landlord, tenant, rent=1000, due_day=5,
                  start="2099-01-01", end="2099-12-31", late_fee_bps=1000):
    return propose(
        contract, direct_vm, landlord, tenant, agreement_type="RENT", raw_text=RENT_TEXT,
        monthly_rent_wei=rent, due_day_of_month=due_day, lease_start=start, lease_end=end,
        late_fee_bps=late_fee_bps,
    )


def propose_freelance(contract, direct_vm, client, contractor,
                       descriptions=("Design mockups", "Final build"),
                       amounts=(600, 400), deadlines=("2099-03-01", "2099-06-01")):
    return propose(
        contract, direct_vm, client, contractor, agreement_type="FREELANCE_MILESTONE",
        raw_text=FREELANCE_TEXT, milestone_descriptions=list(descriptions),
        milestone_amounts=list(amounts), milestone_deadlines=list(deadlines),
    )


def propose_nda(contract, direct_vm, discloser, receiver, duration_days=365):
    return propose(
        contract, direct_vm, discloser, receiver, agreement_type="NDA", raw_text=NDA_TEXT,
        confidentiality_days=duration_days,
    )


# --- proposal + consistency check ---

def test_propose_consistent_becomes_proposed(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    a = contract.get_agreement(aid)
    assert a["status"] == "PROPOSED"
    assert a["consistency_verdict"] == "CONSISTENT"
    assert a["party_a"] == str(direct_bob)
    assert a["party_b"] == str(direct_carol)


def test_propose_inconsistent_flags(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "INCONSISTENT", "The text names a different rent amount.")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "FLAGGED_INCONSISTENT"


def test_propose_could_not_determine_needs_review(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "COULD_NOT_DETERMINE", "Text too vague to judge.")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "NEEDS_REVIEW"


def test_propose_unexpected_verdict_fails_closed_to_needs_review(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "SOMETHING_UNEXPECTED", "n/a")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "NEEDS_REVIEW"


def test_propose_rejects_short_text(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose(contract, direct_vm, direct_bob, direct_carol, agreement_type="GENERIC", raw_text="too short")


def test_propose_rent_rejects_zero_rent(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose_rent(contract, direct_vm, direct_bob, direct_carol, rent=0)


def test_propose_rent_rejects_due_day_out_of_range(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose_rent(contract, direct_vm, direct_bob, direct_carol, due_day=29)


def test_propose_rent_rejects_end_before_start(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose_rent(contract, direct_vm, direct_bob, direct_carol, start="2099-12-31", end="2099-01-01")


def test_propose_rent_rejects_nonzero_milestone_fields(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose(
            contract, direct_vm, direct_bob, direct_carol, agreement_type="RENT", raw_text=RENT_TEXT,
            monthly_rent_wei=1000, due_day_of_month=5, lease_start="2099-01-01", lease_end="2099-12-31",
            milestone_descriptions=["stray"], milestone_amounts=[1], milestone_deadlines=["2099-01-01"],
        )


def test_propose_freelance_requires_matching_list_lengths(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose(
            contract, direct_vm, direct_bob, direct_carol, agreement_type="FREELANCE_MILESTONE",
            raw_text=FREELANCE_TEXT, milestone_descriptions=["only one"],
            milestone_amounts=[100, 200], milestone_deadlines=["2099-01-01"],
        )


def test_propose_freelance_rejects_zero_amount_milestone(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(0, 400))


def test_propose_freelance_creates_milestones(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    milestones = contract.list_milestones(aid)
    assert len(milestones) == 2
    assert milestones[0]["amount_wei"] == "600"
    assert milestones[1]["amount_wei"] == "400"


def test_propose_nda_requires_duration_bounds(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose_nda(contract, direct_vm, direct_bob, direct_carol, duration_days=0)


def test_propose_nda_rejects_nonzero_rent_fields(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose(
            contract, direct_vm, direct_bob, direct_carol, agreement_type="NDA", raw_text=NDA_TEXT,
            monthly_rent_wei=100, confidentiality_days=365,
        )


def test_propose_generic_needs_no_structured_fields(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose(contract, direct_vm, direct_bob, direct_carol, agreement_type="GENERIC", raw_text=GENERIC_TEXT)
    assert contract.get_agreement(aid)["status"] == "PROPOSED"


def test_propose_generic_skips_consistency_round(contract, direct_vm, direct_bob, direct_carol):
    """GENERIC has no structured terms, so even a model that would answer INCONSISTENT for every
    term is never consulted and the agreement is PROPOSED."""
    mock_consistency(direct_vm, "INCONSISTENT")
    aid = propose(contract, direct_vm, direct_bob, direct_carol, agreement_type="GENERIC", raw_text=GENERIC_TEXT)
    a = contract.get_agreement(aid)
    assert a["status"] == "PROPOSED"
    assert a["consistency_verdict"] == "CONSISTENT"


# --- revision ---

def test_revise_agreement_reruns_consistency(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "INCONSISTENT", "Rent mismatch.")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "FLAGGED_INCONSISTENT"

    mock_consistency(direct_vm, "CONSISTENT", "Fixed.")
    direct_vm.sender = direct_bob
    contract.revise_agreement(
        aid, RENT_TEXT, 1000, 5, "2099-01-01", "2099-12-31", 1000, [], [], [], 0,
    )
    a = contract.get_agreement(aid)
    assert a["status"] == "PROPOSED"
    assert a["revision_count"] == 1


def test_revise_requires_party_a(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "INCONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    mock_consistency(direct_vm, "CONSISTENT")
    direct_vm.sender = direct_carol
    with pytest.raises(Exception):
        contract.revise_agreement(aid, RENT_TEXT, 1000, 5, "2099-01-01", "2099-12-31", 1000, [], [], [], 0)


# --- acceptance ---

def test_accept_agreement_requires_consistent_status(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "COULD_NOT_DETERMINE")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_carol
    with pytest.raises(Exception):
        contract.accept_agreement(aid)


def test_accept_agreement_requires_party_b(contract, direct_vm, direct_bob, direct_carol, direct_dave):
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_dave
    with pytest.raises(Exception):
        contract.accept_agreement(aid)


def test_accept_agreement_activates(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    a = contract.get_agreement(aid)
    assert a["status"] == "ACTIVE"
    assert a["accepted_at"] == NOW


def test_accept_freelance_requires_escrow_funded(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_carol
    with pytest.raises(Exception):
        contract.accept_agreement(aid)


def test_reject_agreement_refunds_escrow(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.reject_agreement(aid)
    a = contract.get_agreement(aid)
    assert a["status"] == "REJECTED"
    assert a["escrow_balance"] == "0"


# --- RENT lifecycle ---

def test_record_rent_payment_on_time(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)

    warp_to(direct_vm, "2099-02-03T00:00:00Z")  # day 3, due by day 5
    direct_vm.sender = direct_carol
    direct_vm.value = 1000
    on_time = contract.record_rent_payment(aid)
    direct_vm.value = 0
    assert on_time is True
    assert contract.get_agreement(aid)["rent_payments_made"] == 1


def test_record_rent_payment_late_requires_fee(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol, rent=1000, late_fee_bps=1000)
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)

    warp_to(direct_vm, "2099-02-10T00:00:00Z")  # day 10, past the day-5 due date
    direct_vm.sender = direct_carol
    direct_vm.value = 1000  # exact rent only, no late fee -- should be rejected
    with pytest.raises(Exception):
        contract.record_rent_payment(aid)
    direct_vm.value = 0

    direct_vm.value = 1100  # rent + 10% late fee
    on_time = contract.record_rent_payment(aid)
    direct_vm.value = 0
    assert on_time is False


def test_record_rent_payment_requires_tenant(contract, direct_vm, direct_bob, direct_carol, direct_dave):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    warp_to(direct_vm, "2099-02-03T00:00:00Z")
    direct_vm.sender = direct_dave
    direct_vm.value = 1000
    with pytest.raises(Exception):
        contract.record_rent_payment(aid)
    direct_vm.value = 0


def test_complete_lease_after_end_date(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol, end="2099-12-31")
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    warp_to(direct_vm, "2100-06-01T00:00:00Z")
    contract.complete_lease(aid)
    assert contract.get_agreement(aid)["status"] == "COMPLETED"


def test_complete_lease_too_early_fails(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol, end="2099-12-31")
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    with pytest.raises(Exception):
        contract.complete_lease(aid)


# --- FREELANCE_MILESTONE lifecycle ---

def test_fund_milestone_escrow_wrong_amount_fails(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 500  # should be exactly 1000
    with pytest.raises(Exception):
        contract.fund_milestone_escrow(aid)
    direct_vm.value = 0


def test_submit_and_approve_milestone_pays_contractor(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)

    direct_vm.sender = direct_carol
    contract.submit_milestone(aid, 0, "Delivered the design mockups as agreed.", EVIDENCE_URL)
    direct_vm.sender = direct_bob
    contract.approve_milestone(aid, 0)

    m = contract.get_milestone(aid, 0)
    assert m["status"] == "APPROVED"
    assert contract.get_agreement(aid)["escrow_balance"] == "400"


def test_all_milestones_approved_completes_agreement(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)

    for i in (0, 1):
        direct_vm.sender = direct_carol
        contract.submit_milestone(aid, i, f"Delivered milestone {i}.", EVIDENCE_URL)
        direct_vm.sender = direct_bob
        contract.approve_milestone(aid, i)

    assert contract.get_agreement(aid)["status"] == "COMPLETED"
    assert contract.get_agreement(aid)["escrow_balance"] == "0"


def test_dispute_milestone_consistent_autopays(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    direct_vm.sender = direct_carol
    contract.submit_milestone(aid, 0, "Delivered the design mockups as agreed.", EVIDENCE_URL)

    mock_interpretation(direct_vm, "CONSISTENT_WITH_TERMS", "Deliverable matches the milestone.")
    direct_vm.sender = direct_bob
    result = contract.dispute_milestone(aid, 0, "I wasn't sure this counted, please confirm.", "")
    assert result == "APPROVED"
    assert contract.get_milestone(aid, 0)["status"] == "APPROVED"


def test_dispute_milestone_violates_locks_and_does_not_pay(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    direct_vm.sender = direct_carol
    contract.submit_milestone(aid, 0, "Placeholder text, not the agreed design mockups.", EVIDENCE_URL)

    mock_interpretation(direct_vm, "VIOLATES_TERMS", "Deliverable does not match the milestone.")
    direct_vm.sender = direct_bob
    result = contract.dispute_milestone(aid, 0, "This isn't what we agreed on.", "")
    assert result == "DISPUTED"
    a = contract.get_agreement(aid)
    assert a["escrow_balance"] == "1000"  # untouched -- nothing paid out


def test_dispute_milestone_cooldown_blocks_immediate_retry(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    direct_vm.sender = direct_carol
    contract.submit_milestone(aid, 0, "Delivered.", EVIDENCE_URL)

    mock_interpretation(direct_vm, "COULD_NOT_DETERMINE")
    direct_vm.sender = direct_bob
    contract.dispute_milestone(aid, 0, "Please review.", "")
    warp_to(direct_vm, _iso_plus(NOW, 60))
    with pytest.raises(Exception):
        contract.dispute_milestone(aid, 0, "Please review again.", "")


# --- shared dispute path (RENT / NDA / GENERIC) ---

def test_raise_dispute_confirms_breach_for_nda(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_nda(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)

    mock_interpretation(direct_vm, "VIOLATES_TERMS", "Confidential info was shared publicly.")
    direct_vm.sender = direct_bob
    result = contract.raise_dispute(aid, "The receiving party posted our confidential roadmap publicly.", [EVIDENCE_URL])
    assert result == "BREACH_CONFIRMED"
    a = contract.get_agreement(aid)
    assert a["status"] == "BREACHED"
    assert a["breach_count"] == 1


def test_raise_dispute_can_reopen_a_new_round_after_dismissal(contract, direct_vm, direct_bob, direct_carol):
    """RENT/GENERIC agreements can span months and face more than one unrelated incident -- a
    dismissed round must not permanently block every later dispute."""
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)

    mock_interpretation(direct_vm, "CONSISTENT_WITH_TERMS", "No issue found.")
    direct_vm.sender = direct_bob
    assert contract.raise_dispute(aid, "First incident, turned out to be nothing.", [EVIDENCE_URL]) == "DISMISSED"

    # No cooldown wait needed -- a fresh round starts immediately after a resolved one.
    mock_interpretation(direct_vm, "VIOLATES_TERMS", "This time the lease was actually violated.")
    result = contract.raise_dispute(aid, "Second, unrelated incident months later.", [EVIDENCE_URL])
    assert result == "BREACH_CONFIRMED"
    a = contract.get_agreement(aid)
    assert a["breach_count"] == 1  # only the second round counted as a confirmed breach
    assert a["dispute_action_description"] == "Second, unrelated incident months later."


def test_raise_dispute_dismissed_on_consistent(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_nda(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)

    mock_interpretation(direct_vm, "CONSISTENT_WITH_TERMS", "No breach found.")
    direct_vm.sender = direct_bob
    result = contract.raise_dispute(aid, "I thought this might be a breach but I'm not certain.", [EVIDENCE_URL])
    assert result == "DISMISSED"
    assert contract.get_agreement(aid)["status"] == "ACTIVE"  # NDA not marked BREACHED


def test_raise_dispute_exhausts_to_dismissed(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_nda(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)

    t = NOW
    for i in range(5):  # MAX_DISPUTE_ATTEMPTS
        mock_interpretation(direct_vm, "COULD_NOT_DETERMINE")
        direct_vm.sender = direct_bob
        result = contract.raise_dispute(aid, "Possible breach, please review.", [EVIDENCE_URL])
        t = _iso_plus(t, 601)
        warp_to(direct_vm, t)
        if i < 4:
            assert result == "OPEN"
        else:
            assert result == "DISMISSED"
    assert contract.get_agreement(aid)["dispute_status"] == "DISMISSED"


def test_raise_dispute_not_allowed_for_freelance(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    with pytest.raises(Exception):
        contract.raise_dispute(aid, "This should use dispute_milestone instead.", [EVIDENCE_URL])


# --- v1.1 fixes: approve-after-dispute, mutual refund, repeatable rounds, extra validation ---

def test_approve_milestone_after_failed_dispute(contract, direct_vm, direct_bob, direct_carol):
    """v1.1 fix: the client's documented 'pay anyway' escape hatch must actually work once a
    milestone has moved to DISPUTED, not only while it's still SUBMITTED."""
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    direct_vm.sender = direct_carol
    contract.submit_milestone(aid, 0, "Delivered, though not exactly as specified.", EVIDENCE_URL)

    mock_interpretation(direct_vm, "VIOLATES_TERMS", "Deliverable does not match the milestone.")
    direct_vm.sender = direct_bob
    assert contract.dispute_milestone(aid, 0, "This isn't quite right.", "") == "DISPUTED"

    # Client decides, off-chain, to pay anyway -- this must be callable on a DISPUTED milestone.
    direct_vm.sender = direct_bob
    contract.approve_milestone(aid, 0)
    m = contract.get_milestone(aid, 0)
    assert m["status"] == "APPROVED"


def test_confirm_milestone_refund_requires_both_parties(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    direct_vm.sender = direct_carol
    contract.submit_milestone(aid, 0, "Incomplete work.", EVIDENCE_URL)
    mock_interpretation(direct_vm, "VIOLATES_TERMS", "Not satisfied.")
    direct_vm.sender = direct_bob
    contract.dispute_milestone(aid, 0, "Not acceptable.", "")

    direct_vm.sender = direct_bob  # client confirms alone -- refund must NOT execute yet
    result = contract.confirm_milestone_refund(aid, 0)
    assert result == "DISPUTED"
    assert contract.get_milestone(aid, 0)["status"] == "DISPUTED"
    assert contract.get_agreement(aid)["escrow_balance"] == "1000"  # untouched


def test_confirm_milestone_refund_executes_once_both_confirm(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    direct_vm.sender = direct_carol
    contract.submit_milestone(aid, 0, "Incomplete work.", EVIDENCE_URL)
    mock_interpretation(direct_vm, "VIOLATES_TERMS", "Not satisfied.")
    direct_vm.sender = direct_bob
    contract.dispute_milestone(aid, 0, "Not acceptable.", "")

    direct_vm.sender = direct_bob
    contract.confirm_milestone_refund(aid, 0)
    direct_vm.sender = direct_carol
    result = contract.confirm_milestone_refund(aid, 0)
    assert result == "REFUNDED"
    assert contract.get_milestone(aid, 0)["status"] == "REFUNDED"
    assert contract.get_agreement(aid)["escrow_balance"] == "400"  # only milestone 0's amount left


def test_confirm_milestone_refund_requires_disputed_status(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    direct_vm.sender = direct_bob
    with pytest.raises(Exception):  # still PENDING, never disputed
        contract.confirm_milestone_refund(aid, 0)


def test_propose_rejects_self_as_counterparty(contract, direct_vm, direct_bob):
    with pytest.raises(Exception):
        propose(contract, direct_vm, direct_bob, direct_bob, agreement_type="GENERIC", raw_text=GENERIC_TEXT)


def test_propose_freelance_rejects_out_of_order_deadlines(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose_freelance(
            contract, direct_vm, direct_bob, direct_carol,
            deadlines=("2099-06-01", "2099-01-01"),  # second milestone due before the first
        )


def test_propose_rent_rejects_invalid_month(contract, direct_vm, direct_bob, direct_carol):
    with pytest.raises(Exception):
        propose_rent(contract, direct_vm, direct_bob, direct_carol, start="2099-13-01", end="2099-12-31")


# --- views ---

def test_list_agreements(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT")
    propose_rent(contract, direct_vm, direct_bob, direct_carol)
    propose_nda(contract, direct_vm, direct_carol, direct_bob)
    listed = contract.list_agreements(0, 10)
    assert len(listed) == 2


# --- v1.2: steward-feedback fixes -- per-term consistency + evidence-backed disputes ---

def test_consistency_fails_if_one_milestone_amount_contradicts_text(contract, direct_vm, direct_bob, direct_carol):
    """Rejection point 1: milestone amounts must be checked individually, so one contradicted
    money-moving term cannot hide behind other terms that pass."""
    mock_consistency(direct_vm, "CONSISTENT", overrides={"milestone_1_amount": "INCONSISTENT"})
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    a = contract.get_agreement(aid)
    assert a["status"] == "FLAGGED_INCONSISTENT"
    assert "milestone_1_amount=INCONSISTENT" in a["consistency_rationale"]


def test_consistency_fails_if_milestone_deadline_contradicts_text(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT", overrides={"milestone_0_deadline": "INCONSISTENT"})
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "FLAGGED_INCONSISTENT"


def test_consistency_fails_if_milestone_description_contradicts_text(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT", overrides={"milestone_0_description": "INCONSISTENT"})
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "FLAGGED_INCONSISTENT"


def test_consistency_unstated_milestone_term_cannot_pass(contract, direct_vm, direct_bob, direct_carol):
    """A money-moving term the text never states is COULD_NOT_DETERMINE -> NEEDS_REVIEW, not accept-able."""
    mock_consistency(direct_vm, "CONSISTENT", overrides={"milestone_0_amount": "COULD_NOT_DETERMINE"})
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "NEEDS_REVIEW"
    direct_vm.sender = direct_carol
    with pytest.raises(Exception):
        contract.accept_agreement(aid)


def test_consistency_missing_term_verdict_fails_closed(contract, direct_vm, direct_bob, direct_carol):
    """If the model skips a term entirely, that term counts as unchecked, never as passed."""
    mock_consistency(direct_vm, "CONSISTENT", omit=("milestone_1_deadline",))
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "NEEDS_REVIEW"


def test_consistency_missing_rent_term_fails_closed(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT", omit=("late_fee",))
    aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "NEEDS_REVIEW"


def test_consistency_inconsistent_beats_undetermined(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(
        direct_vm, "CONSISTENT",
        overrides={"milestone_0_amount": "COULD_NOT_DETERMINE", "milestone_1_amount": "INCONSISTENT"},
    )
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "FLAGGED_INCONSISTENT"


def _active_freelance(contract, direct_vm, bob, carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, bob, carol, amounts=(600, 400))
    direct_vm.sender = bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = carol
    contract.accept_agreement(aid)
    contract.submit_milestone(aid, 0, "Delivered the design mockups as agreed.", EVIDENCE_URL)
    return aid


def test_dispute_milestone_does_not_pay_when_evidence_not_fetched(contract, direct_vm, direct_bob, direct_carol):
    """Rejection point 2: party-written text alone must never release escrow."""
    aid = _active_freelance(contract, direct_vm, direct_bob, direct_carol)
    mock_interpretation(direct_vm, "CONSISTENT_WITH_TERMS", "Looks fine.", fetch=False)
    direct_vm.sender = direct_bob
    assert contract.dispute_milestone(aid, 0, "Please confirm.", "") == "DISPUTED"
    assert contract.get_agreement(aid)["escrow_balance"] == "1000"


def test_dispute_milestone_does_not_pay_when_model_relies_on_claims_only(contract, direct_vm, direct_bob, direct_carol):
    aid = _active_freelance(contract, direct_vm, direct_bob, direct_carol)
    mock_interpretation(direct_vm, "CONSISTENT_WITH_TERMS", "Contractor says it is done.", supports=False)
    direct_vm.sender = direct_bob
    assert contract.dispute_milestone(aid, 0, "Please confirm.", "") == "DISPUTED"
    assert contract.get_agreement(aid)["escrow_balance"] == "1000"


def test_dispute_milestone_accepts_client_counter_evidence_url(contract, direct_vm, direct_bob, direct_carol):
    aid = _active_freelance(contract, direct_vm, direct_bob, direct_carol)
    mock_interpretation(direct_vm, "CONSISTENT_WITH_TERMS", "Evidence shows mockups delivered.")
    direct_vm.sender = direct_bob
    assert contract.dispute_milestone(aid, 0, "Missing pages.", EVIDENCE_URL_2) == "APPROVED"


def test_submit_milestone_requires_https_evidence_url(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(600, 400))
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.fund_milestone_escrow(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_carol
    contract.accept_agreement(aid)
    for bad in ("", "http://evidence.example.com/x", "https://localhost/x", "https://192.168.0.1/x", "not a url"):
        with pytest.raises(Exception):
            contract.submit_milestone(aid, 0, "Delivered.", bad)


def test_dispute_milestone_rejects_bad_client_evidence_url(contract, direct_vm, direct_bob, direct_carol):
    aid = _active_freelance(contract, direct_vm, direct_bob, direct_carol)
    mock_interpretation(direct_vm, "CONSISTENT_WITH_TERMS")
    direct_vm.sender = direct_bob
    with pytest.raises(Exception):
        contract.dispute_milestone(aid, 0, "Reason.", "http://insecure.example.com/x")


def _active_nda(contract, direct_vm, bob, carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_nda(contract, direct_vm, bob, carol)
    direct_vm.sender = carol
    contract.accept_agreement(aid)
    return aid


def test_raise_dispute_requires_evidence_urls(contract, direct_vm, direct_bob, direct_carol):
    aid = _active_nda(contract, direct_vm, direct_bob, direct_carol)
    mock_interpretation(direct_vm, "VIOLATES_TERMS")
    direct_vm.sender = direct_bob
    with pytest.raises(Exception):
        contract.raise_dispute(aid, "They leaked it.", [])
    with pytest.raises(Exception):
        contract.raise_dispute(aid, "They leaked it.", [EVIDENCE_URL] * 4)  # over MAX_EVIDENCE_URLS
    with pytest.raises(Exception):
        contract.raise_dispute(aid, "They leaked it.", ["ftp://evidence.example.com/x"])


def test_raise_dispute_does_not_confirm_breach_without_fetched_evidence(contract, direct_vm, direct_bob, direct_carol):
    aid = _active_nda(contract, direct_vm, direct_bob, direct_carol)
    mock_interpretation(direct_vm, "VIOLATES_TERMS", "Party says it leaked.", fetch=False)
    direct_vm.sender = direct_bob
    result = contract.raise_dispute(aid, "They leaked our roadmap.", [EVIDENCE_URL])
    assert result == "OPEN"
    a = contract.get_agreement(aid)
    assert a["status"] == "ACTIVE" and a["breach_count"] == 0


def test_raise_dispute_does_not_confirm_breach_when_model_relies_on_claims_only(contract, direct_vm, direct_bob, direct_carol):
    aid = _active_nda(contract, direct_vm, direct_bob, direct_carol)
    mock_interpretation(direct_vm, "VIOLATES_TERMS", "Accuser says so.", supports=False)
    direct_vm.sender = direct_bob
    assert contract.raise_dispute(aid, "They leaked our roadmap.", [EVIDENCE_URL]) == "OPEN"
    assert contract.get_agreement(aid)["breach_count"] == 0


def test_evidence_urls_are_recorded_for_audit(contract, direct_vm, direct_bob, direct_carol):
    aid = _active_freelance(contract, direct_vm, direct_bob, direct_carol)
    mock_interpretation(direct_vm, "COULD_NOT_DETERMINE")
    direct_vm.sender = direct_bob
    contract.dispute_milestone(aid, 0, "Check pages.", EVIDENCE_URL_2)
    m = contract.get_milestone(aid, 0)
    assert m["deliverable_url"] == EVIDENCE_URL
    assert m["client_evidence_url"] == EVIDENCE_URL_2

    nda = _active_nda(contract, direct_vm, direct_bob, direct_carol)
    mock_interpretation(direct_vm, "COULD_NOT_DETERMINE")
    direct_vm.sender = direct_bob
    contract.raise_dispute(nda, "Possible leak.", [EVIDENCE_URL, EVIDENCE_URL_2])
    assert contract.get_agreement(nda)["dispute_evidence_urls"] == EVIDENCE_URL + "\n" + EVIDENCE_URL_2


# --- v1.2 (cont.): rent / NDA per-term coverage, revision re-check, view content ---

def test_consistency_rent_single_term_contradiction_flags(contract, direct_vm, direct_bob, direct_carol):
    for term in ("monthly_rent", "due_day", "lease_start", "lease_end", "late_fee"):
        mock_consistency(direct_vm, "CONSISTENT", overrides={term: "INCONSISTENT"})
        aid = propose_rent(contract, direct_vm, direct_bob, direct_carol)
        a = contract.get_agreement(aid)
        assert a["status"] == "FLAGGED_INCONSISTENT", term
        assert f"{term}=INCONSISTENT" in a["consistency_rationale"]


def test_consistency_nda_duration_contradiction_flags(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT", overrides={"confidentiality_duration": "INCONSISTENT"})
    aid = propose_nda(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "FLAGGED_INCONSISTENT"


def test_consistency_nda_missing_duration_verdict_fails_closed(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT", omit=("confidentiality_duration",))
    aid = propose_nda(contract, direct_vm, direct_bob, direct_carol)
    assert contract.get_agreement(aid)["status"] == "NEEDS_REVIEW"


def test_consistency_escrow_total_and_count_are_checked(contract, direct_vm, direct_bob, direct_carol):
    for term in ("escrow_total", "milestone_count"):
        mock_consistency(direct_vm, "CONSISTENT", overrides={term: "INCONSISTENT"})
        aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
        assert contract.get_agreement(aid)["status"] == "FLAGGED_INCONSISTENT", term


def test_consistency_rationale_lists_every_term_verdict(contract, direct_vm, direct_bob, direct_carol):
    mock_consistency(direct_vm, "CONSISTENT")
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol)
    rationale = contract.get_agreement(aid)["consistency_rationale"]
    for term in ("escrow_total", "milestone_count", "milestone_0_description", "milestone_0_amount",
                 "milestone_0_deadline", "milestone_1_description", "milestone_1_amount",
                 "milestone_1_deadline"):
        assert f"{term}=CONSISTENT" in rationale, term


def test_revise_freelance_rechecks_changed_milestone_amount(contract, direct_vm, direct_bob, direct_carol):
    warp_to(direct_vm, NOW)
    mock_consistency(direct_vm, "CONSISTENT", overrides={"milestone_0_amount": "INCONSISTENT"})
    aid = propose_freelance(contract, direct_vm, direct_bob, direct_carol, amounts=(900, 400))
    assert contract.get_agreement(aid)["status"] == "FLAGGED_INCONSISTENT"

    warp_to(direct_vm, _iso_plus(NOW, 600))
    mock_consistency(direct_vm, "CONSISTENT")
    direct_vm.sender = direct_bob
    contract.revise_agreement(
        aid, FREELANCE_TEXT, 0, 0, "", "", 0,
        ["Design mockups", "Final build"], [600, 400], ["2099-03-01", "2099-06-01"], 0,
    )
    a = contract.get_agreement(aid)
    assert a["status"] == "PROPOSED"
    assert contract.get_milestone(aid, 0)["amount_wei"] == "600"


def test_milestone_view_exposes_evidence_fields(contract, direct_vm, direct_bob, direct_carol):
    aid = _active_freelance(contract, direct_vm, direct_bob, direct_carol)
    m = contract.get_milestone(aid, 0)
    assert m["deliverable_url"] == EVIDENCE_URL
    assert m["client_evidence_url"] == ""
