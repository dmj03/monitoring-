"""
The controlled Add Alert flow: GET /alerts/form-options and the validation
POST /alerts applies before anything is written.

In-process, against a temporary copy of the dataset (UC3_DATASET_PATH) and a
temporary routing file and AI cache, so no project file is touched. The suite
asserts that at the end.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="uc3_alert_form_"))
WORK = TMP / "dataset.json"
shutil.copy(DATASET_PATH, WORK)
os.environ["UC3_DATASET_PATH"] = str(WORK)
os.environ["UC3_ROUTING_PATH"] = str(TMP / "routing.json")
os.environ["UC3_AI_CACHE_PATH"] = str(TMP / "ai_cache.json")

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api import app  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fields(r) -> dict[str, str]:
    """422 detail as {field: message}."""
    d = r.json().get("detail")
    return {e["loc"][-1]: e["msg"] for e in d} if isinstance(d, list) else {}


# The expected IDs follow from the data, not from a fixed count: alerts added
# through the UI raise the highest number.
_HIGHEST = max(int(a["alert_id"][5:]) for a in json.loads(WORK.read_text(encoding="utf-8"))["alerts"]
               if a["alert_id"][5:].isdigit() and a["alert_id"].startswith("ALRT-"))
NEXT, NEXT2, NEXT3 = (f"ALRT-{_HIGHEST + i:04d}" for i in (1, 2, 3))

VALID = {"alert_id": NEXT, "rule_id": "TM-VEL-014", "customer_ref": "CUST-0101",
         "created_at": "2026-10-03T09:00:00Z"}


def main() -> int:
    project_routing = PROJECT_ROOT / "data" / "routing.json"
    before = {"dataset": digest(DATASET_PATH),
              "routing": digest(project_routing) if project_routing.exists() else None,
              "kb": {str(f.relative_to(KB_DIR)): digest(f) for f in sorted(KB_DIR.rglob("*"))
                     if f.is_file() and "__pycache__" not in f.parts}}
    cache_existed = (PROJECT_ROOT / "data" / "ai_cache.json").exists()
    data = json.loads(WORK.read_text(encoding="utf-8"))
    cfg = yaml.safe_load((KB_DIR / "config.yaml").read_text())
    playbooks = yaml.safe_load((KB_DIR / "playbooks.yaml").read_text())["playbooks"]

    with TestClient(app) as c:
        print("[1] GET /alerts/form-options")
        r = c.get("/alerts/form-options")
        check("200, not read as an alert ID", r.status_code == 200, str(r.status_code))
        o = r.json()
        check("rules are exactly the KB routing table, in config order",
              [x["rule_id"] for x in o["rules"]] == list(cfg["routing"]))
        check("ten rules", len(o["rules"]) == 10, str(len(o["rules"])))
        check("each rule carries its playbook and that playbook's category",
              all(x["playbook_id"] == cfg["routing"][x["rule_id"]]
                  and x["category"] == playbooks[x["playbook_id"]]["category"] for x in o["rules"]))
        check("the programme-level pattern is not offered as a rule",
              all(x["category"] != "programme_level_pattern" for x in o["rules"]))
        check("customers are exactly the dataset's customers",
              sorted(x["customer_ref"] for x in o["customers"]) == sorted(data["customers"]))
        check("each customer carries its own programme",
              all(x["programme_ref"] == data["customers"][x["customer_ref"]]["programme_ref"]
                  for x in o["customers"]))
        check("programmes are the dataset's, with their stored names",
              {x["programme_ref"]: x["name"] for x in o["programmes"]}
              == {k: v.get("programme_name_placeholder") for k, v in data["programmes"].items()})
        check("next alert ID is one above the highest", o["next_alert_id"] == NEXT,
              o["next_alert_id"])
        check("alert IDs in use are listed",
              sorted(o["alert_ids"]) == sorted(a["alert_id"] for a in data["alerts"]))

        print("[2] validation before anything is written")
        n0 = len(c.get("/alerts", params={"limit": 500}).json()["alerts"])
        r = c.post("/alerts", json={**VALID, "rule_id": "FOO-999"})
        check("unknown rule is 422", r.status_code == 422, str(r.status_code))
        check("the error names rule_id", "rule_id" in fields(r), r.text[:200])
        r = c.post("/alerts", json={**VALID, "customer_ref": "CUST-NOPE"})
        check("unknown customer is 422", r.status_code == 422, str(r.status_code))
        check("the error names customer_ref", "customer_ref" in fields(r), r.text[:200])
        r = c.post("/alerts", json={**VALID, "programme_ref": "P004"})
        check("a programme that is not the customer's is 422", r.status_code == 422, str(r.status_code))
        check("the error names the customer's programme",
              "P001" in fields(r).get("programme_ref", ""), r.text[:200])
        r = c.post("/alerts", json={**VALID, "cluster_ref": "C001"})
        check("a non-null cluster_ref is 422", r.status_code == 422, str(r.status_code))
        check("the error names cluster_ref and the cluster engine",
              "cluster engine" in fields(r).get("cluster_ref", ""), r.text[:200])
        r = c.post("/alerts", json={**VALID, "rule_id": "FOO-999", "customer_ref": "CUST-NOPE"})
        check("every problem is reported at once",
              {"rule_id", "customer_ref"} <= set(fields(r)), r.text[:200])
        r = c.post("/alerts", json={**VALID, "alert_id": ""})
        check("empty alert ID is 422", r.status_code == 422, str(r.status_code))
        check("nothing was written by any refused request",
              len(c.get("/alerts", params={"limit": 500}).json()["alerts"]) == n0)
        check("no refused alert is readable", c.get(f"/alerts/{NEXT}").status_code == 404)

        print("[3] duplicates")
        r = c.post("/alerts", json={**VALID, "alert_id": "ALRT-0001"})
        check("an existing alert ID is 409", r.status_code == 409, str(r.status_code))
        r = c.post("/alerts", json={**VALID, "alert_id": "ALRT-0001", "rule_id": "FOO-999"})
        check("409 takes precedence over other problems", r.status_code == 409, str(r.status_code))

        print("[4] successful creation")
        r = c.post("/alerts", json=VALID)
        check("valid alert without programme is 201", r.status_code == 201, r.text[:200])
        a = r.json()
        check("programme derived from the customer", a["programme_ref"] == "P001", str(a))
        check("created_at stored as sent, in UTC", a["created_at"] == "2026-10-03T09:00:00Z")
        check("cluster_ref stored as null", a["cluster_ref"] is None)
        stored = json.loads(WORK.read_text(encoding="utf-8"))["alerts"][-1]
        check("stored record has exactly the business keys",
              sorted(stored) == sorted(["alert_id", "rule_id", "customer_ref", "programme_ref",
                                        "created_at", "shared_match_key", "cluster_ref"]), str(sorted(stored)))
        r = c.post("/alerts", json={**VALID, "alert_id": NEXT2, "programme_ref": "P001",
                                    "shared_match_key": "CP-SHARED-7781", "rule_id": "CP-LINK-071",
                                    "cluster_ref": None})
        check("a matching programme, a shared_match_key and a null cluster_ref are still accepted",
              r.status_code == 201, r.text[:200])
        check("next alert ID moves on", c.get("/alerts/form-options").json()["next_alert_id"] == NEXT3)

        print("[5] the new alert works with the existing engines")
        r = c.post("/investigate", json={"alert": c.get(f"/alerts/{NEXT}").json()})
        check("investigable immediately", r.status_code == 200, str(r.status_code))
        check("category from the selected rule's playbook",
              r.json()["category"] == playbooks[cfg["routing"]["TM-VEL-014"]]["category"])
        check("no cluster claimed by the investigation", r.json()["cluster_ref"] is None)
        r = c.get("/clusters")
        check("clustering still runs over the new alerts", r.status_code == 200
              and r.json()["alerts_considered"] == len(data["alerts"]) + 2)
        check("routing is independent: the new alert is not routed",
              NEXT not in c.get("/routing").json()["routing"])

    print("[6] project files")
    check("canonical dataset.json unchanged", digest(DATASET_PATH) == before["dataset"])
    check("project routing.json unchanged",
          (digest(project_routing) if project_routing.exists() else None) == before["routing"])
    check("KB unchanged", {str(f.relative_to(KB_DIR)): digest(f) for f in sorted(KB_DIR.rglob("*"))
                           if f.is_file() and "__pycache__" not in f.parts} == before["kb"])
    check("no AI cache file written into the project",
          (PROJECT_ROOT / "data" / "ai_cache.json").exists() == cache_existed)

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    for f in failed:
        print("  FAIL", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
