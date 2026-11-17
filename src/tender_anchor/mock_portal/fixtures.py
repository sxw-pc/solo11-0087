"""Loading and validating bundled synthetic portal fixtures."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
ATTACHMENT_DIR = DATA_DIR / "attachments"


@lru_cache(maxsize=1)
def load_fixtures() -> dict[str, Any]:
    notices_doc = json.loads((DATA_DIR / "notices.json").read_text(encoding="utf-8"))
    corrections_doc = json.loads(
        (DATA_DIR / "corrections.json").read_text(encoding="utf-8")
    )
    notices = {item["notice_id"]: dict(item) for item in notices_doc["notices"]}
    corrections = corrections_doc.get("corrections", {})
    for notice_id, correction in corrections.items():
        if notice_id not in notices:
            raise ValueError(f"更正公告引用了不存在的公告：{notice_id}")
        notices[notice_id]["correction"] = correction
    return {
        "portal_name": notices_doc["portal_name"],
        "disclaimer": notices_doc["disclaimer"],
        "notices": notices,
        "order": sorted(notices),
        "attachments": {path.name for path in ATTACHMENT_DIR.iterdir() if path.is_file()},
    }


def get_notice(notice_id: str) -> dict[str, Any] | None:
    return load_fixtures()["notices"].get(notice_id)
