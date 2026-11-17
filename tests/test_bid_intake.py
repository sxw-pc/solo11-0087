from pathlib import Path

import pytest

from tender_anchor.bid_intake import intake_bids, read_bids
from tender_anchor.store import Store


FIXTURE = Path(__file__).parents[1] / "fixtures/bids/sample.json"


def test_bid_intake_uses_captured_notice_terms_and_keeps_first_receipt(crawled):
    _, paths, _ = crawled
    bids = read_bids(FIXTURE)
    with Store(paths.db_path) as store:
        first = intake_bids(store, bids)
        assert [(item["bid_id"], item["status"], item["reason"]) for item in first] == [
            ("BID-001", "eligible", "base_notice_terms"),
            ("BID-002", "rejected", "after_notice_deadline"),
            ("BID-003", "rejected", "above_notice_budget"),
        ]
        assert intake_bids(store, bids) == first
        changed = {**bids[0], "amount": "1000000.00"}
        with pytest.raises(ValueError, match="different terms"):
            intake_bids(store, [changed])
