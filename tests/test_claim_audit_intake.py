import json
from decimal import Decimal
from pathlib import Path
from tender_anchor.claim_audit_intake import prior_audit_preview
from tender_anchor.mock_portal.fixtures import get_notice


def test_claims_link_to_synthetic_award_result():
    root=Path(__file__).parents[1]
    data=json.loads((root/"fixtures/claim_audit/intake.json").read_text())
    assert get_notice(data["notice_id"])["kind"]=="result"
    assert sum(Decimal(c["prior"]) for c in data["award_claims"])==1
    assert prior_audit_preview(data)=={"claim_id":"claim-A","expected_prevented":"1.80"}


def test_deterministic_preview_has_large_worst_case_exposure():
    root=Path(__file__).parents[1]
    d=json.loads((root/"fixtures/claim_audit/intake.json").read_text())
    chosen=prior_audit_preview(d)["claim_id"]
    worst=max(Decimal(c["suspected_loss"]) for c in d["award_claims"] if c["claim_id"]!=chosen)
    assert worst==8>Decimal(d["authorized_contracts"]["deterrence_cover"]["max_worst_loss"])
    assert d["sampling_contract"]["one_fraud_claim_at_a_time"]
