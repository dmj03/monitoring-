"""
Routing tests: PUT /alerts/{alert_id}/routing and GET /routing.

Routing is the analyst's operational decision, stored in its own file. These
tests prove it persists (across a restarted app), that an outcome is allowed
only where the alert's playbook permits it, that bad input is rejected, and that
the alert dataset, the investigation and the AI flow are untouched.

Runs in-process against the canonical dataset (read-only) with the routing
store pointed at a temporary file via UC3_ROUTING_PATH, so no project file is
written. Plain-script style like the other suites. Exit code 0 on success.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

TMP = Path(tempfile.mkdtemp(prefix="uc3-routing-"))
ROUTING = TMP / "routing.json"
os.environ["UC3_ROUTING_PATH"] = str(ROUTING)

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api import app  # noqa: E402
from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT  # noqa: E402
from backend.routing import DESTINATIONS, OUTCOMES  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def main() -> int:
    dataset_md5 = hashlib.md5(DATASET_PATH.read_bytes()).hexdigest()
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cfg = yaml.safe_load((KB_DIR / "config.yaml").read_text())
    pbs = yaml.safe_load((KB_DIR / "playbooks.yaml").read_text())["playbooks"]

    def permits(alert: dict, outcome: str) -> bool:
        return outcome in pbs[cfg["routing"][alert["rule_id"]]].get(
            "allowed_outcomes", cfg["investigation_outcomes"])

    nfa = next(iter(OUTCOMES))
    open_alert = next(a for a in raw["alerts"] if permits(a, nfa))
    closed_alert = next(a for a in raw["alerts"] if not permits(a, nfa))
    other = next(a for a in raw["alerts"] if a["alert_id"] not in (open_alert["alert_id"],
                                                                   closed_alert["alert_id"]))
    body = lambda a: {"alert": {k: v for k, v in a.items() if not k.startswith("_")}}  # noqa: E731
    project_before = {p: p.stat().st_mtime_ns for p in (PROJECT_ROOT / "data").glob("*")}

    print("=" * 78)
    print("ROUTING")
    print("=" * 78)

    with TestClient(app) as c:
        inv_before = c.post("/investigate", json=body(open_alert)).json()

        print("\n[1] options and empty state")
        r = c.get("/routing")
        d = r.json()
        check("GET /routing 200", r.status_code == 200)
        check("five destinations with label and description",
              [o["id"] for o in d["destinations"]] == list(DESTINATIONS)
              and all(o["label"] and o["description"] for o in d["destinations"]))
        check("outcome is the KB's own outcome name",
              [o["id"] for o in d["outcomes"]] == [nfa] and nfa in cfg["investigation_outcomes"])
        check("destinations and outcomes are separate lists",
              not ({o["id"] for o in d["destinations"]} & {o["id"] for o in d["outcomes"]}))
        check("nothing routed yet; no file created by reading", d["routing"] == {} and not ROUTING.exists())

        print("[2] route to a destination")
        r = c.put(f"/alerts/{open_alert['alert_id']}/routing", json={"destination": "mlro"})
        rec = r.json()
        check("PUT 200", r.status_code == 200, str(rec))
        check("record holds destination, no outcome, server timestamp",
              rec["destination"] == "mlro" and rec["outcome"] is None
              and rec["alert_id"] == open_alert["alert_id"] and rec["routed_at"].endswith("Z"))
        stored = json.loads(ROUTING.read_text())["alerts"][open_alert["alert_id"]]
        check("written to the routing file",
              {k: stored[k] for k in ("alert_id", "destination", "outcome", "routed_at")}
              == {k: rec[k] for k in ("alert_id", "destination", "outcome", "routed_at")})
        check("visible in GET /routing", c.get("/routing").json()["routing"][open_alert["alert_id"]] == rec)
        check("no temporary file left behind", sorted(p.name for p in TMP.iterdir()) == ["routing.json"])

        print("[3] re-routing replaces the decision")
        r = c.put(f"/alerts/{open_alert['alert_id']}/routing", json={"destination": "kyb_cdd"})
        check("re-route 200 and replaced",
              r.status_code == 200 and c.get("/routing").json()["routing"][open_alert["alert_id"]]["destination"] == "kyb_cdd")
        check("one record per alert", list(json.loads(ROUTING.read_text())["alerts"]) == [open_alert["alert_id"]])

        print("[4] the outcome follows the playbook's allowed_outcomes")
        r = c.put(f"/alerts/{open_alert['alert_id']}/routing", json={"outcome": nfa})
        rec = r.json()
        check("permitted playbook: outcome stored, no destination",
              r.status_code == 200 and rec["outcome"] == nfa and rec["destination"] is None, str(rec))
        r = c.put(f"/alerts/{closed_alert['alert_id']}/routing", json={"outcome": nfa})
        check("playbook without a no-action path: 409 naming its allowed outcomes",
              r.status_code == 409 and "not permitted" in r.json()["detail"]
              and all(o in r.json()["detail"] for o in
                      pbs[cfg["routing"][closed_alert["rule_id"]]]["allowed_outcomes"]), r.text)
        check("nothing stored for the refused alert",
              closed_alert["alert_id"] not in c.get("/routing").json()["routing"])
        r = c.put(f"/alerts/{closed_alert['alert_id']}/routing", json={"destination": "sanctions_analyst"})
        check("that alert can still be routed to a destination", r.status_code == 200)

        print("[5] invalid input")
        for label, payload in [("neither", {}), ("both", {"destination": "mlro", "outcome": nfa}),
                               ("unknown destination", {"destination": "compliance_bot"}),
                               ("unknown outcome", {"outcome": "escalate_to_mlro"}),
                               ("extra field", {"destination": "mlro", "decided_by": "ai"})]:
            r = c.put(f"/alerts/{other['alert_id']}/routing", json=payload)
            check(f"{label}: 422", r.status_code == 422, str(r.status_code))
        r = c.put("/alerts/ALRT-NOPE/routing", json={"destination": "mlro"})
        check("unknown alert: 404", r.status_code == 404)

        print("[6] investigation, alerts and AI are untouched")
        check("POST /investigate unchanged after routing",
              c.post("/investigate", json=body(open_alert)).json() == inv_before)
        check("investigation response carries no routing field",
              not any("rout" in k for k in inv_before if k != "rule_id"))
        a = c.get(f"/alerts/{open_alert['alert_id']}").json()
        check("alert record carries no routing field", set(a) == {
            "alert_id", "rule_id", "customer_ref", "programme_ref", "created_at",
            "shared_match_key", "cluster_ref"}, str(sorted(a)))

    print("[7] persists across a restarted app")
    with TestClient(app) as c2:
        m = c2.get("/routing").json()["routing"]
        check("decisions survive a restart",
              m[open_alert["alert_id"]]["outcome"] == nfa
              and m[closed_alert["alert_id"]]["destination"] == "sanctions_analyst", str(m))

    print("[8] the alert dataset is never written")
    check("dataset.json unchanged", hashlib.md5(DATASET_PATH.read_bytes()).hexdigest() == dataset_md5)
    check("no file added or changed under data/",
          {p: p.stat().st_mtime_ns for p in (PROJECT_ROOT / "data").glob("*")} == project_before)

    print()
    for f in failed:
        print("  FAIL", f)
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
