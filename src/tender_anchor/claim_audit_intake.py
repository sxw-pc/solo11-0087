"""Prior-expected single-claim audit preview for a synthetic result notice."""
from __future__ import annotations
import argparse
import json
from decimal import Decimal
from pathlib import Path
from .mock_portal.fixtures import get_notice


def prior_audit_preview(data:dict)->dict:
    notice=get_notice(data["notice_id"])
    if notice is None or notice["kind"]!="result":
        raise ValueError("audit needs a synthetic result notice")
    scores={a["action_id"]:sum(Decimal(c["prior"])*Decimal(c["suspected_loss"])*
                                Decimal(a["detection"][c["claim_id"]]) for c in data["award_claims"])
            for a in data["audit_actions"]}
    chosen=max(scores,key=lambda action_id:(scores[action_id],action_id))
    return {"action_id":chosen,"expected_prevented":str(scores[chosen])}


def main(argv=None)->int:
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",type=Path,default=Path("fixtures/claim_audit/intake.json"))
    args=p.parse_args(argv)
    print(json.dumps(prior_audit_preview(json.loads(args.fixture.read_text())),sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
