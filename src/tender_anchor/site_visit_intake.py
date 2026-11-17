"""Solo site-visit travel quotes for a synthetic tender notice."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .mock_portal.fixtures import get_notice


def solo_visit_quotes(data:dict)->list[dict]:
    notice=get_notice(data["notice_id"])
    if notice is None or notice["kind"]!="tender":
        raise ValueError("site visits require a known tender notice")
    depot=data["depot"]
    return [{"sponsor_id":s["sponsor_id"],
             "solo_cost":data["distance"][depot][s["stop"]]+data["distance"][s["stop"]][depot]}
            for s in data["sponsors"]]


def main(argv=None)->int:
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",type=Path,default=Path("fixtures/site_visits/intake.json"))
    args=p.parse_args(argv)
    data=json.loads(args.fixture.read_text(encoding="utf-8"))
    print(json.dumps(solo_visit_quotes(data),sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
