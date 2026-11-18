from tender_anchor.bid_intake import intake_bids
from tender_anchor.panel_intake import preview_panel_case

from tests.panel_helpers import load, panel_store  # noqa: F401


def test_initial_review_scores_signed_ballots_and_awards_a(panel_store):
    initial = preview_panel_case(panel_store, load("case.json"))["initial"]
    assert initial["stage"] == "initial"
    assert initial["status"] == "decided"
    assert initial["winner_bidder_id"] == "supplier-a"
    assert [(row["bid_id"], row["display_mean"]) for row in initial["bids"]] == [
        ("BID-A", "84.67"), ("BID-B", "76.00"),
    ]
    # The late disclosure is recorded as late, never as known at signing.
    assert initial["known_disclosure_ids"] == []
    assert initial["late_disclosure_ids"] == ["DISC-1"]


def test_whole_round_recusal_reconstitutes_panel_and_awards_b(panel_store):
    preview = preview_panel_case(panel_store, load("case.json"))
    whole = next(
        item for item in preview["rereviews"]
        if item["contract_id"] == "CONTRACT-WHOLE-ROUND"
    )
    assert whole["status"] == "decided"
    assert whole["winner_bidder_id"] == "supplier-b"
    assert [(row["bid_id"], row["display_mean"], row["included_reviewer_ids"])
            for row in whole["bids"]] == [
        ("BID-A", "79.33", ["R2", "R3", "R4"]),
        ("BID-B", "85.67", ["R2", "R3", "R4"]),
    ]
    excluded = {
        (vote["reviewer_id"], vote["bid_id"])
        for vote in whole["votes"] if not vote["included"]
    }
    assert excluded == {("R1", "BID-A"), ("R1", "BID-B")}


def test_independent_absolute_recusal_keeps_other_ballot_valid(panel_store):
    preview = preview_panel_case(panel_store, load("case_independent.json"))
    absolute = next(
        item for item in preview["rereviews"]
        if item["contract_id"] == "CONTRACT-ABSOLUTE"
    )
    assert absolute["status"] == "decided"
    assert absolute["winner_bidder_id"] == "supplier-a"
    rows = {row["bid_id"]: row for row in absolute["bids"]}
    assert rows["BID-A"]["display_mean"] == "79.33"
    assert rows["BID-A"]["included_reviewer_ids"] == ["R2", "R3", "R4"]
    assert rows["BID-B"]["display_mean"] == "76.00"
    assert rows["BID-B"]["included_reviewer_ids"] == ["R1", "R2", "R3"]
    excluded = {
        (vote["reviewer_id"], vote["bid_id"])
        for vote in absolute["votes"] if not vote["included"]
    }
    # Only R1-A is struck; R1-B stays valid and R4-B is outside this
    # contract's scope, so it is not used to inflate BID-B.
    assert excluded == {("R1", "BID-A"), ("R4", "BID-B")}


def test_governing_contract_controls_final_award(panel_store):
    whole = preview_panel_case(panel_store, load("case.json"))["final_award"]
    absolute = preview_panel_case(panel_store, load("case_independent.json"))["final_award"]
    assert (whole["winner_bidder_id"], whole["stage"]) == ("supplier-b", "rereview")
    assert (absolute["winner_bidder_id"], absolute["stage"]) == ("supplier-a", "rereview")
    assert absolute["contract_id"] == "CONTRACT-ABSOLUTE"


def test_missing_replacement_ballot_holds_rather_than_improvises(panel_store):
    preview = preview_panel_case(panel_store, load("case_missing_alternate.json"))
    whole = next(
        item for item in preview["rereviews"]
        if item["contract_id"] == "CONTRACT-WHOLE-ROUND"
    )
    assert whole["status"] == "pending_verification"
    assert whole["winner_bidder_id"] == ""
    assert "replacement_ballot_missing" in whole["issues"]
    assert "quorum_short" in whole["issues"]
    assert whole["missing_replacements"] == [{"reviewer_id": "R4", "bid_id": "BID-B"}]
    assert preview["final_award"]["status"] == "pending_verification"


def test_unavailable_alternate_holds_and_never_mixes_two_vote_tally(panel_store):
    preview = preview_panel_case(panel_store, load("case_unavailable_alternate.json"))
    whole = next(
        item for item in preview["rereviews"]
        if item["contract_id"] == "CONTRACT-WHOLE-ROUND"
    )
    # Whole-round route loses R1 entirely and has no R4, so every bid is short.
    assert whole["status"] == "pending_verification"
    assert all(row["mean_score"] is None for row in whole["bids"])
    absolute = next(
        item for item in preview["rereviews"]
        if item["contract_id"] == "CONTRACT-ABSOLUTE"
    )
    assert absolute["status"] == "pending_verification"
    assert absolute["winner_bidder_id"] == ""
    assert preview["final_award"]["status"] == "pending_verification"


def test_tie_breaks_on_lower_price(panel_store):
    initial = preview_panel_case(panel_store, load("case_tie.json"))["initial"]
    assert initial["bids"][0]["display_mean"] == initial["bids"][1]["display_mean"]
    assert initial["winner_bidder_id"] == "supplier-b"
    assert initial["winner_amount"] == "1200000.00"


def test_notice_budget_change_moves_eligibility_and_award(panel_store):
    case = load("case.json")
    assert preview_panel_case(panel_store, case)["final_award"]["winner_bidder_id"] == "supplier-b"
    with panel_store.transaction():
        panel_store.connection.execute(
            "UPDATE notices SET budget_amount='1100000.00', source_hash='hash-cut' "
            "WHERE notice_id='TA-2026-0001'"
        )
    preview = preview_panel_case(panel_store, case)
    assert [row["status"] for row in preview["bids"]] == ["rejected", "rejected"]
    assert preview["final_award"]["status"] == "pending_verification"
    assert preview["final_award"]["reason"] == "no_eligible_bids"


def test_notice_deadline_change_rejects_late_bid(panel_store):
    case = load("case.json")
    with panel_store.transaction():
        panel_store.connection.execute(
            "UPDATE notices SET deadline_at='2026-11-29T09:30:00+08:00', "
            "source_hash='hash-dl' WHERE notice_id='TA-2026-0001'"
        )
    preview = preview_panel_case(panel_store, case)
    reasons = {row["bid_id"]: row["reason"] for row in preview["bids"]}
    assert reasons == {"BID-A": "after_notice_deadline", "BID-B": "after_notice_deadline"}


def test_bid_intake_eligibility_feeds_panel(panel_store):
    bids = [
        {"bid_id": "BID-A", "notice_id": "TA-2026-0001", "bidder_id": "supplier-a",
         "received_at": "2026-11-30T09:00:00+08:00", "amount": "1250000.00"},
        {"bid_id": "BID-B", "notice_id": "TA-2026-0001", "bidder_id": "supplier-b",
         "received_at": "2026-11-30T09:05:00+08:00", "amount": "1200000.00"},
    ]
    assert [row["status"] for row in intake_bids(panel_store, bids)] == [
        "eligible", "eligible",
    ]
    preview = preview_panel_case(panel_store, load("case.json"))
    assert {row["bid_id"]: row["status"] for row in preview["bids"]} == {
        "BID-A": "eligible", "BID-B": "eligible",
    }
    with panel_store.transaction():
        panel_store.connection.execute(
            "UPDATE notices SET budget_amount='1100000.00' WHERE notice_id='TA-2026-0001'"
        )
    changed = preview_panel_case(panel_store, load("case.json"))
    assert [row["status"] for row in changed["bids"]] == ["rejected", "rejected"]


def test_untouched_source_case_still_reads():
    case = load("case.json")
    assert len(case["ballots"]) == 8
    assert case["disclosures"][0]["reviewer_id"] == "R1"
    assert case["contracts"][0]["kind"] == "whole_round_recusal"
