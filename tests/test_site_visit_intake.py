import json
from pathlib import Path
from tender_anchor.site_visit_intake import solo_visit_quotes
from tender_anchor.mock_portal.fixtures import get_notice


def test_solo_quotes_link_real_synthetic_tender():
    root=Path(__file__).parents[1]
    data=json.loads((root/"fixtures/site_visits/intake.json").read_text())
    assert get_notice(data["notice_id"])["kind"]=="tender"
    quotes=solo_visit_quotes(data)
    assert [q["sponsor_id"] for q in quotes]==["s-A","s-B","s-C"]
    assert [q["solo_cost"] for q in quotes]==[20,18,16]
    assert sum(q["solo_cost"] for q in quotes)==54


def test_joint_visit_route_has_real_distance_witness():
    root=Path(__file__).parents[1]
    d=json.loads((root/"fixtures/site_visits/intake.json").read_text())
    road=d["distance"]
    ab=road["D"]["A"]+road["A"]["B"]+road["B"]["D"]
    c=road["D"]["C"]+road["C"]["D"]
    assert d["vehicle_capacity"]==2
    assert (ab,c,ab+c)==(21,16,37)
