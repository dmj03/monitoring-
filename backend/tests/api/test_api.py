"""
API layer tests.

The API is a thin translation over engines that are already covered by the unit
and integration suites. These tests therefore check the translation, not the
business logic: status codes, schema shape, error mapping, and that existing
engine semantics survive the HTTP round trip.

The last section repeats the read-only guarantee at HTTP level, because a shared
application-scoped engine is a new way for state to leak that the engine-level
suite could not cover.

Uses fastapi.testclient (httpx under the hood). Same plain-script style as the
other suites. Exit code 0 on success.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import app  # noqa: E402
from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def is_pycache(p: Path) -> bool:
    return "__pycache__" in p.parts or p.suffix == ".pyc"


def snapshot(root: Path) -> dict[str, tuple[str, int]]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not is_pycache(p):
            st = p.stat()
            out[str(p.relative_to(root))] = (
                hashlib.sha256(p.read_bytes()).hexdigest(), st.st_mtime_ns)
    return out


def main() -> int:
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    alerts = raw["alerts"]
    alert = alerts[0]

    print("=" * 78)
    print("API LAYER")
    print("=" * 78)

    before = snapshot(PROJECT_ROOT)

    with TestClient(app) as client:
        # --------------------------------------------------------- health
        print("\n" + "-" * 78)
        print("HEALTH")
        print("-" * 78)
        r = client.get("/health")
        check("GET /health returns 200", r.status_code == 200, str(r.status_code))
        body = r.json()
        check("health body has status ok", body.get("status") == "ok", str(body))
        check("health reports the loaded KB version",
              body.get("kb_version", "").startswith("kb-playbooks-"), str(body.get("kb_version")))
        check("health reports 10 playbooks", body.get("playbooks") == 10, str(body.get("playbooks")))
        check("health reports 4 cluster types",
              body.get("cluster_types") == 4, str(body.get("cluster_types")))
        print(f"  {body}")

        # -------------------------------------------------- investigation
        print("\n" + "-" * 78)
        print("INVESTIGATION")
        print("-" * 78)
        r = client.post("/investigate", json={"alert": alert})
        check("POST /investigate valid request returns 200", r.status_code == 200,
              f"{r.status_code} {r.text[:120]}")
        inv = r.json()
        check("response carries the submitted alert_id",
              inv["alert_id"] == alert["alert_id"], inv.get("alert_id"))
        check("response carries a category and playbook_id",
              bool(inv["category"]) and bool(inv["playbook_id"]))
        check("evidence is present", len(inv["evidence"]) > 0, str(len(inv["evidence"])))
        check("tool calls are fewer than evidence items (dedup survives HTTP)",
              len(inv["tool_calls"]) < len(inv["evidence"]),
              f"{len(inv['tool_calls'])} calls / {len(inv['evidence'])} evidence")
        check("allowed_outcomes is the playbook subset, not the global enum",
              0 < len(inv["allowed_outcomes"]) < 6, str(inv["allowed_outcomes"]))
        check("ai_boundaries reaches the client", bool(inv["ai_boundaries"]))
        print(f"  {inv['alert_id']}  {inv['category']}  "
              f"{len(inv['evidence'])} evidence / {len(inv['tool_calls'])} calls  "
              f"outcomes={len(inv['allowed_outcomes'])}")

        # semantics preserved: compare against the engine called directly
        from backend.investigation import InvestigationEngine
        from backend.tools import DataStore
        direct = InvestigationEngine(KB_DIR, DataStore(DATASET_PATH)).investigate(dict(alert))
        check("HTTP result matches the engine called directly",
              [(e.key, e.status, str(e.value)) for e in direct.evidence]
              == [(e["key"], e["status"], str(e["value"])) for e in inv["evidence"]],
              "the API altered engine output")
        check("missing_evidence preserved",
              inv["missing_evidence"] == direct.missing, str(inv["missing_evidence"]))

        # MISSING semantics over the wire
        unknown_cust = {**alert, "customer_ref": "NO-SUCH-CUSTOMER"}
        r = client.post("/investigate", json={"alert": unknown_cust})
        check("unknown customer_ref is a valid investigation, not an error",
              r.status_code == 200, str(r.status_code))
        gaps = r.json()["missing_evidence"] if r.status_code == 200 else []
        check("unknown customer_ref yields MISSING evidence", len(gaps) > 0, str(len(gaps)))
        check("MISSING items carry no fabricated value",
              all(e["value"] is None for e in r.json()["evidence"] if e["status"] == "MISSING"))
        print(f"  unknown customer -> 200 with {len(gaps)} MISSING items, no values")

        # ------------------------------------------------------- errors
        print("\n" + "-" * 78)
        print("ERROR HANDLING")
        print("-" * 78)
        r = client.post("/investigate", json={"alert": {"alert_id": "X"}})
        check("missing required fields returns 422", r.status_code == 422, str(r.status_code))

        r = client.post("/investigate", json={})
        check("missing body returns 422", r.status_code == 422, str(r.status_code))

        r = client.post("/investigate", json={"alert": {**alert, "rule_id": "NOT-A-RULE"}})
        check("unrouted rule_id returns 400, not 500", r.status_code == 400, str(r.status_code))
        check("400 explains the problem", "unknown rule_id" in r.json()["detail"],
              r.json().get("detail", ""))
        check("error body exposes no filesystem path",
              "/home" not in r.text and str(PROJECT_ROOT) not in r.text, r.text[:80])
        check("error body exposes no traceback",
              "Traceback" not in r.text and "File \"" not in r.text)

        r = client.post("/investigate", json={"alert": {**alert, "alert_id": ""}})
        check("empty alert_id returns 422", r.status_code == 422, str(r.status_code))
        print("  422 on schema violations, 400 on unroutable rule_id, no paths or tracebacks")

        # --------------------------------------------------- clustering
        print("\n" + "-" * 78)
        print("CLUSTERING")
        print("-" * 78)
        r = client.post("/cluster", json={"alerts": alerts})
        check("POST /cluster valid request returns 200", r.status_code == 200,
              f"{r.status_code} {r.text[:120]}")
        cl = r.json()
        check("four clusters form over the canonical dataset",
              len(cl["clusters"]) == 4, str(len(cl["clusters"])))
        check("membership map is returned", len(cl["membership"]) > 0, str(len(cl["membership"])))
        check("counts are consistent",
              cl["alerts_submitted"] == len(alerts)
              and cl["alerts_clustered"] == len(cl["membership"]))
        check("cluster carries thresholds and span",
              all("min_alerts" in c["thresholds"] and c["time_span_hours"] >= 0
                  for c in cl["clusters"]))
        for c in cl["clusters"]:
            print(f"  {c['cluster_ref']}  {c['cluster_type']:<28}"
                  f"{len(c['alert_ids'])} alerts / {len(c['customer_refs'])} customers / "
                  f"{c['time_span_hours']}h")

        # overrides are honoured and request-scoped
        r2 = client.post("/cluster", json={"alerts": alerts,
                                           "overrides": {"repeated_velocity": {"min_alerts": 99}}})
        check("threshold override is honoured", r2.status_code == 200
              and not any(c["cluster_type"] == "repeated_velocity" for c in r2.json()["clusters"]),
              str(r2.status_code))
        r3 = client.post("/cluster", json={"alerts": alerts})
        check("override did not persist into the next request",
              r3.json()["clusters"] == cl["clusters"],
              "a per-request override leaked into shared engine state")

        r = client.post("/cluster", json={"alerts": []})
        check("empty alert list returns 422", r.status_code == 422, str(r.status_code))
        r = client.post("/cluster", json={"alerts": [{k: v for k, v in alert.items()
                                                      if k != "created_at"}]})
        check("alert without created_at returns 400", r.status_code == 400, str(r.status_code))
        r = client.post("/cluster", json={"alerts": alerts,
                                          "overrides": {"no_such_cluster": {"min_alerts": 2}}})
        check("unknown cluster type in overrides returns 400", r.status_code == 400,
              str(r.status_code))
        print("  override honoured, request-scoped; 422 on empty list, 400 on bad input")

        # ------------------------------------------------------ read: alerts
        print("\n" + "-" * 78)
        print("READ ENDPOINTS — ALERTS")
        print("-" * 78)
        r = client.get("/alerts")
        check("GET /alerts returns 200", r.status_code == 200, str(r.status_code))
        lst = r.json()
        check("total equals the dataset alert count",
              lst["total"] == len(alerts), f"{lst['total']} vs {len(alerts)}")
        check("default page is capped at the limit",
              len(lst["alerts"]) == min(50, len(alerts)), str(len(lst["alerts"])))
        check("response carries no generator metadata",
              not any(k.startswith("_") for a in lst["alerts"] for k in a),
              "an internal _-prefixed field leaked into the response")
        check("alert IDs are stable dataset IDs",
              lst["alerts"][0]["alert_id"] == alerts[0]["alert_id"])

        r = client.get("/alerts", params={"limit": 5, "offset": 10})
        page = r.json()
        check("paging returns the requested slice",
              len(page["alerts"]) == 5 and page["offset"] == 10)
        check("paged IDs match the underlying slice",
              [a["alert_id"] for a in page["alerts"]]
              == [a["alert_id"] for a in alerts[10:15]])

        rid = alerts[0]["rule_id"]
        r = client.get("/alerts", params={"rule_id": rid})
        expected = sum(1 for a in alerts if a["rule_id"] == rid)
        check("rule_id filter matches the dataset",
              r.json()["total"] == expected, f"{r.json()['total']} vs {expected}")
        prog = alerts[0]["programme_ref"]
        r = client.get("/alerts", params={"programme_ref": prog})
        check("programme_ref filter matches the dataset",
              r.json()["total"] == sum(1 for a in alerts if a["programme_ref"] == prog))
        r = client.get("/alerts", params={"rule_id": "NOT-A-RULE"})
        check("unmatched filter returns an empty list, not 404",
              r.status_code == 200 and r.json()["total"] == 0, str(r.status_code))
        r = client.get("/alerts", params={"limit": 0})
        check("limit below range returns 422", r.status_code == 422, str(r.status_code))
        print(f"  {lst['total']} alerts, paging and three filters verified against the dataset")

        r = client.get(f"/alerts/{alerts[0]['alert_id']}")
        check("GET /alerts/{id} returns 200", r.status_code == 200, str(r.status_code))
        one = r.json()
        check("single alert matches the stored record",
              all(one[k] == alerts[0][k] for k in one if k in alerts[0]))
        check("single alert carries no generator metadata",
              not any(k.startswith("_") for k in one))
        r = client.get("/alerts/NO-SUCH-ALERT")
        check("unknown alert_id returns 404", r.status_code == 404, str(r.status_code))
        check("404 body names the missing id and no path",
              "NO-SUCH-ALERT" in r.json()["detail"] and "/home" not in r.text)
        print(f"  {alerts[0]['alert_id']} fetched; unknown id -> 404")

        # ---------------------------------------------------- read: clusters
        print("\n" + "-" * 78)
        print("READ ENDPOINTS — CLUSTERS")
        print("-" * 78)
        r = client.get("/clusters")
        check("GET /clusters returns 200", r.status_code == 200, str(r.status_code))
        cls = r.json()
        check("four clusters over the whole dataset", len(cls["clusters"]) == 4,
              str(len(cls["clusters"])))
        check("alerts_considered equals the dataset size",
              cls["alerts_considered"] == len(alerts))
        check("GET /clusters agrees with POST /cluster",
              [c["alert_ids"] for c in cls["clusters"]]
              == [c["alert_ids"] for c in cl["clusters"]],
              "the read endpoint diverged from the POST endpoint")
        check("response states that cluster_ref is not durable",
              "not a durable" in cls["note"], cls.get("note", ""))
        print(f"  {len(cls['clusters'])} clusters, {cls['alerts_clustered']} members, "
              f"identical to POST /cluster")

    # ------------------------------------------------------- read-only
    print("\n" + "-" * 78)
    print("READ-ONLY GUARANTEE OVER HTTP")
    print("-" * 78)
    after = snapshot(PROJECT_ROOT)
    ds_key = str(DATASET_PATH.relative_to(PROJECT_ROOT))
    kb_files = [k for k in before if k.startswith(KB_DIR.name)]
    content = [k for k in before if k in after and before[k][0] != after[k][0]]
    mtimes = [k for k in before if k in after and before[k][1] != after[k][1]]
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))

    check("dataset unchanged after API requests", before[ds_key] == after[ds_key])
    check("KB unchanged after API requests",
          all(before[k] == after[k] for k in kb_files),
          str([k for k in kb_files if before[k] != after[k]]))
    check("no file content changed", not content, str(content[:5]))
    check("no file mtime changed", not mtimes, str(mtimes[:5]))
    check("no files created", not created, str(created[:5]))
    check("no files deleted", not deleted, str(deleted[:5]))
    print(f"  {len(before)} tracked files: 0 content, 0 mtime, 0 created, 0 deleted")
    print(f"  dataset sha256 {before[ds_key][0][:16]}")

    print("\n" + "=" * 78)
    for p in passed:
        print(f"  PASS  {p}")
    for f in failed:
        print(f"  FAIL  {f}")
    print("=" * 78)
    print(f"{len(passed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
