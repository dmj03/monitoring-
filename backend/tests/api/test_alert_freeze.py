"""
Alert creation freeze.

Adding alerts is paused until UC3_ALERT_CREATION is switched on. With the switch
off (the default), POST /alerts must be refused with 403 before any validation,
the dataset must be left byte-for-byte unchanged, and /health must report the
freeze so the UI can disable its "Add alert" button.

Runs against a TEMPORARY COPY of the dataset, never the canonical file.

Run directly: python backend/tests/api/test_alert_freeze.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.paths import DATASET_PATH  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="uc3_alert_freeze_"))
WORK = TMP / "dataset.json"
shutil.copy(DATASET_PATH, WORK)
os.environ.pop("UC3_ALERT_CREATION", None)
os.environ["UC3_DATASET_PATH"] = str(WORK)
os.environ["UC3_ROUTING_PATH"] = str(TMP / "routing.json")
os.environ["UC3_AI_CACHE_PATH"] = str(TMP / "ai_cache.json")

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import app  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


VALID = {"alert_id": "ALRT-9001", "rule_id": "TM-VEL-014", "customer_ref": "CUST-0101",
         "created_at": "2026-10-03T09:00:00Z"}


def main() -> int:
    before = {"canonical": digest(DATASET_PATH), "work": digest(WORK)}

    with TestClient(app) as c:
        print("[1] /health reports the freeze")
        h = c.get("/health").json()
        check("alert_creation is false by default", h.get("alert_creation") is False, str(h))

        print("[2] POST /alerts is refused")
        r = c.post("/alerts", json=VALID)
        check("a valid alert is refused with 403", r.status_code == 403, str(r.status_code))
        check("the refusal says adding is paused", "paused" in r.json().get("detail", ""), r.text)
        r = c.post("/alerts", json={**VALID, "alert_id": "ALRT-0001"})
        check("refused before the duplicate check (403, not 409)", r.status_code == 403, str(r.status_code))
        r = c.post("/alerts", json={**VALID, "rule_id": "NO-SUCH-RULE"})
        check("refused before rule validation (403, not 422)", r.status_code == 403, str(r.status_code))
        n = c.get("/alerts", params={"limit": 1}).json()["total"]
        check("no alert was added", n == 90, str(n))

        print("[3] reading still works")
        check("GET /alerts/form-options still answers", c.get("/alerts/form-options").status_code == 200)

    check("working copy unchanged", digest(WORK) == before["work"])
    check("canonical dataset unchanged", digest(DATASET_PATH) == before["canonical"])

    print("[4] switching it on is honoured")
    os.environ["UC3_ALERT_CREATION"] = "on"
    try:
        with TestClient(app) as c:
            check("/health reports alert_creation true", c.get("/health").json().get("alert_creation") is True)
            r = c.post("/alerts", json=VALID)
            check("a valid alert is accepted", r.status_code == 201, f"{r.status_code} {r.text}")
    finally:
        os.environ.pop("UC3_ALERT_CREATION", None)
    check("canonical dataset still unchanged", digest(DATASET_PATH) == before["canonical"])

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    for f in failed:
        print("  FAILED:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
