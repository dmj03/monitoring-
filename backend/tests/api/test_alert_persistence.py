"""
Alert persistence tests.

Everything here runs against a TEMPORARY COPY of the dataset, never the
canonical file. The copy is made in a tmp directory, the app is pointed at it
through UC3_DATASET_PATH, and the canonical dataset's hash is asserted
unchanged at the end. A persistence test that mutated the real dataset would
invalidate every other suite in the project.

Covers: creation, visibility through the read endpoints, duplicate rejection,
investigation of a newly created alert, survival across a fresh DataStore and
across a real server restart, and dataset integrity — that exactly one alert was
added and no other record or table changed.

Run directly: python test_alert_persistence.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT  # noqa: E402
from backend.tools import DataStore  # noqa: E402
from backend.tools.datastore import DuplicateAlertError  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


NEW_ALERT = {
    "alert_id": "ALRT-TEST-9001",
    "rule_id": "TM-VEL-014",
    "customer_ref": "CUST-0101",
    "programme_ref": "P001",
    "created_at": "2026-09-24T07:00:00Z",
    "shared_match_key": None,
    "cluster_ref": None,
}


def main() -> int:
    canonical_before = digest(DATASET_PATH)
    kb_before = {str(f.relative_to(KB_DIR)): digest(f) for f in sorted(KB_DIR.rglob("*"))
                 if f.is_file() and "__pycache__" not in f.parts}
    tmpdir = Path(tempfile.mkdtemp(prefix="uc3_persist_"))
    work = tmpdir / "dataset.json"
    shutil.copy2(DATASET_PATH, work)

    original = json.loads(work.read_text(encoding="utf-8"))
    n_before = len(original["alerts"])
    tables_before = {k: json.dumps(v, sort_keys=True)
                     for k, v in original.items() if k != "alerts"}
    alerts_before = {a["alert_id"]: json.dumps(a, sort_keys=True) for a in original["alerts"]}

    print("=" * 78)
    print("ALERT PERSISTENCE")
    print("=" * 78)
    print(f"\nworking copy : {work}")
    print(f"canonical    : {DATASET_PATH} (must not change)")
    print(f"alerts before: {n_before}")

    os.environ["UC3_DATASET_PATH"] = str(work)
    try:
        from backend.api import app

        # ------------------------------------------------- 1. create
        print("\n" + "-" * 78)
        print("1. CREATE")
        print("-" * 78)
        with TestClient(app) as client:
            check("app is using the working copy",
                  str(app.state.dataset_path) == str(work), str(app.state.dataset_path))

            r = client.post("/alerts", json=NEW_ALERT)
            check("POST /alerts returns 201", r.status_code == 201,
                  f"{r.status_code} {r.text[:120]}")
            created = r.json()
            check("returned alert matches what was submitted",
                  all(created[k] == v for k, v in NEW_ALERT.items()),
                  f"returned {created}")
            check("response carries no generator metadata",
                  not any(k.startswith("_") for k in created))
            print(f"  201  {created['alert_id']}  {created['rule_id']}")

            # --------------------------------------------- 2. visible
            print("\n" + "-" * 78)
            print("2. VISIBLE THROUGH THE READ ENDPOINTS")
            print("-" * 78)
            r = client.get("/alerts")
            check("GET /alerts total increased by exactly one",
                  r.json()["total"] == n_before + 1, f"{r.json()['total']} vs {n_before + 1}")
            r = client.get("/alerts", params={"limit": 500})
            ids = [a["alert_id"] for a in r.json()["alerts"]]
            check("new alert appears in the list", NEW_ALERT["alert_id"] in ids)

            r = client.get(f"/alerts/{NEW_ALERT['alert_id']}")
            check("GET /alerts/{new_id} returns 200", r.status_code == 200, str(r.status_code))
            check("fetched alert matches the submitted alert",
                  all(r.json()[k] == v for k, v in NEW_ALERT.items()))
            print(f"  total {n_before} -> {n_before + 1}; "
                  f"GET /alerts/{NEW_ALERT['alert_id']} -> 200")

            # -------------------------------------------- 3. duplicate
            print("\n" + "-" * 78)
            print("3. DUPLICATE")
            print("-" * 78)
            tampered = {**NEW_ALERT, "programme_ref": "P004", "rule_id": "SAN-PEP-061"}
            r = client.post("/alerts", json=tampered)
            check("duplicate alert_id returns 409", r.status_code == 409, str(r.status_code))
            check("409 explains the conflict",
                  "already exists" in r.json()["detail"], r.json().get("detail", ""))
            check("409 leaks no path or traceback",
                  "/home" not in r.text and "Traceback" not in r.text)
            r = client.get(f"/alerts/{NEW_ALERT['alert_id']}")
            check("the original alert was NOT overwritten",
                  r.json()["programme_ref"] == NEW_ALERT["programme_ref"]
                  and r.json()["rule_id"] == NEW_ALERT["rule_id"],
                  f"became {r.json()['programme_ref']}/{r.json()['rule_id']}")
            check("duplicate attempt did not add a second record",
                  client.get("/alerts").json()["total"] == n_before + 1)
            print(f"  second POST -> 409; original intact; total still {n_before + 1}")

            # ------------------------------------------ 4. investigate
            print("\n" + "-" * 78)
            print("4. INVESTIGATE THE NEW ALERT")
            print("-" * 78)
            r = client.post("/investigate", json={"alert": NEW_ALERT})
            check("POST /investigate on the new alert returns 200", r.status_code == 200,
                  f"{r.status_code} {r.text[:120]}")
            inv = r.json()
            check("investigation routed the new alert to a playbook",
                  inv["category"] == "unusual_transaction_velocity", inv.get("category", ""))
            check("investigation produced evidence", len(inv["evidence"]) > 0)
            check("investigation used the new alert's references",
                  all(c["args"].get("customer_ref", NEW_ALERT["customer_ref"])
                      == NEW_ALERT["customer_ref"] for c in inv["tool_calls"]))
            print(f"  {inv['category']}, {len(inv['evidence'])} evidence / "
                  f"{len(inv['tool_calls'])} calls")

            # -------------------------------------- 5. clusters see it
            r = client.get("/clusters")
            check("GET /clusters considers the new alert",
                  r.json()["alerts_considered"] == n_before + 1,
                  f"{r.json()['alerts_considered']} vs {n_before + 1}")
            print(f"  GET /clusters considers {r.json()['alerts_considered']} alerts")

        # ------------------------------- 6. survives a fresh DataStore
        print("\n" + "-" * 78)
        print("5. SURVIVES A FRESH DATASTORE AND A REAL SERVER RESTART")
        print("-" * 78)
        fresh = DataStore(work)
        found = [a for a in fresh.alerts if a["alert_id"] == NEW_ALERT["alert_id"]]
        check("a fresh DataStore sees the persisted alert", len(found) == 1, str(len(found)))
        check("the persisted alert is readable and complete",
              found and all(found[0][k] == v for k, v in NEW_ALERT.items()))
        check("fresh DataStore alert count increased by one",
              len(fresh.alerts) == n_before + 1)

        # ---------------------------- real server restart over a socket
        port = free_port()
        env = {**os.environ, "UC3_DATASET_PATH": str(work)}
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.api:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=PROJECT_ROOT, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True)
        base = f"http://127.0.0.1:{port}"
        up = False
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                    up = True
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        try:
            check("a newly started server comes up against the same file", up)
            if up:
                r = httpx.get(f"{base}/alerts/{NEW_ALERT['alert_id']}", timeout=10)
                check("the alert survives a real server restart", r.status_code == 200,
                      str(r.status_code))
                check("restarted server returns the same alert",
                      r.status_code == 200
                      and all(r.json()[k] == v for k, v in NEW_ALERT.items()))
                r = httpx.get(f"{base}/alerts", timeout=10)
                check("restarted server counts the new alert",
                      r.json()["total"] == n_before + 1)
        finally:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=10)
        print(f"  fresh DataStore: found; restarted server on {base}: found")

        # ----------------------------------------- 6. dataset integrity
        print("\n" + "-" * 78)
        print("6. DATASET INTEGRITY")
        print("-" * 78)
        after = json.loads(work.read_text(encoding="utf-8"))
        check("exactly one alert was added",
              len(after["alerts"]) == n_before + 1, str(len(after["alerts"])))
        tables_after = {k: json.dumps(v, sort_keys=True)
                        for k, v in after.items() if k != "alerts"}
        check("no other table changed", tables_after == tables_before,
              str([k for k in tables_before if tables_before[k] != tables_after.get(k)]))
        check("top-level keys unchanged", list(after) == list(original))
        alerts_after = {a["alert_id"]: json.dumps(a, sort_keys=True) for a in after["alerts"]}
        modified = [k for k in alerts_before if alerts_before[k] != alerts_after.get(k)]
        check("no existing alert was modified", not modified, str(modified[:5]))
        added = sorted(set(alerts_after) - set(alerts_before))
        check("the only new alert is the one submitted", added == [NEW_ALERT["alert_id"]],
              str(added))
        check("the new alert carries no generator metadata",
              not any(k.startswith("_") for k in after["alerts"][-1]))
        check("the new alert is appended, not inserted",
              after["alerts"][-1]["alert_id"] == NEW_ALERT["alert_id"])
        print(f"  {n_before} -> {len(after['alerts'])} alerts; "
              f"0 tables changed; 0 existing alerts modified")

        # --------------------------------------- duplicate at engine level
        try:
            fresh.add_alert(NEW_ALERT)
            check("DataStore.add_alert raises on duplicate", False, "no exception raised")
        except DuplicateAlertError:
            check("DataStore.add_alert raises DuplicateAlertError on duplicate", True)

        # ------------------------------------------- no temp files left
        leftovers = [p.name for p in work.parent.iterdir() if p.name != work.name]
        check("no temporary or backup file left beside the dataset", not leftovers,
              str(leftovers))

    finally:
        os.environ.pop("UC3_DATASET_PATH", None)
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ----------------------------------- canonical dataset untouched
    print("\n" + "-" * 78)
    print("CANONICAL DATASET")
    print("-" * 78)
    canonical_after = digest(DATASET_PATH)
    check("the canonical dataset was never touched", canonical_before == canonical_after,
          f"{canonical_before[:12]} -> {canonical_after[:12]}")
    kb_after = {str(f.relative_to(KB_DIR)): digest(f) for f in sorted(KB_DIR.rglob("*"))
                if f.is_file() and "__pycache__" not in f.parts}
    kb_changed = [k for k in kb_before if kb_before[k] != kb_after.get(k)]
    check("the KB was never touched",
          not kb_changed and set(kb_before) == set(kb_after),
          f"changed={kb_changed} added/removed={sorted(set(kb_before) ^ set(kb_after))}")
    print(f"  canonical sha256 {canonical_before[:16]} unchanged")

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
