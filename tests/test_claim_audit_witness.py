import json
from fractions import Fraction as F
from pathlib import Path


def test_small_zero_sum_witness_has_primal_and_dual_bounds():
    root=Path(__file__).parents[1]/"fixtures/claim_audit"
    d=json.loads((root/"intake.json").read_text())
    w=json.loads((root/"witness.json").read_text())
    p={k:F(v) for k,v in w["lottery"].items()}
    q={k:F(v) for k,v in w["adversary"].items()}
    target=F(w["optimal_worst_loss"])
    assert sum(p.values())==sum(q.values())==1
    by_action={a["action_id"]:a for a in d["audit_actions"]}
    by_claim={c["claim_id"]:c for c in d["award_claims"]}
    def residual(action_id,claim_id):
        a=by_action[action_id]; c=by_claim[claim_id]
        return F(c["suspected_loss"])*(1-F(a["detection"][claim_id]))
    assert all(sum(prob*residual(a,claim_id) for a,prob in p.items())<=target
               for claim_id in by_claim)
    assert all(sum(prob*residual(action_id,c) for c,prob in q.items())>=target
               for action_id in by_action)
    assert target==F(504,115)<F(5)
