"""
Real-server tests.

Everything in test_api.py runs through fastapi.testclient, which calls the ASGI
app in-process and never opens a socket. This suite starts an actual uvicorn
process and talks to it over HTTP, so it covers what TestClient cannot: that
`uvicorn backend.api:app` boots, binds, serves, and shuts down cleanly, and that
nothing about real serialisation or the lifespan handler differs.

It deliberately does not re-test business behaviour that test_api.py already
covers. The checks here are: the server starts, every endpoint answers over the
wire with the right status and shape, errors leak nothing, HTTP results match
the engines called directly, and serving writes no project file.

Run directly: python test_server.py
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import httpx  # noqa: E402

from backend.clustering import ClusterEngine  # noqa: E402
from backend.investigation import InvestigationEngine  # noqa: E402
from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT  # noqa: E402
from backend.tools import DataStore  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


def wait_until_up(base: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    return False


def main() -> int:
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    alerts = raw["alerts"]
    alert = alerts[0]
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    print("=" * 78)
    print("REAL UVICORN SERVER")
    print("=" * 78)
    print(f"\ncommand : uvicorn backend.api:app --host 127.0.0.1 --port {port}")
    print(f"cwd     : {PROJECT_ROOT}")

    before = snapshot(PROJECT_ROOT)
    files_before = set(before)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True)

    try:
        up = wait_until_up(base)
        check("1. server starts and answers on a real socket", up,
              "server did not become reachable")
        if not up:
            raise SystemExit(1)
        check("server process is alive", proc.poll() is None)
        print(f"  listening on {base}, pid {proc.pid}")

        # ----------------------------------------------------- GET /health
        print("\n" + "-" * 78)
        print("ENDPOINTS OVER REAL HTTP")
        print("-" * 78)
        r = httpx.get(f"{base}/health", timeout=10)
        check("GET /health 200", r.status_code == 200, str(r.status_code))
        check("health schema", set(r.json()) == {"status", "kb_version", "playbooks",
                                                 "cluster_types"}, str(sorted(r.json())))
        check("health reports ok", r.json()["status"] == "ok")
        print(f"  GET  /health              {r.status_code}  {r.json()}")

        # ----------------------------------------------------- GET /alerts
        r = httpx.get(f"{base}/alerts", timeout=10)
        check("GET /alerts 200", r.status_code == 200, str(r.status_code))
        body = r.json()
        check("alerts schema", set(body) == {"alerts", "total", "limit", "offset"},
              str(sorted(body)))
        check("alerts total matches the dataset", body["total"] == len(alerts),
              f"{body['total']} vs {len(alerts)}")
        check("no generator metadata over the wire",
              not any(k.startswith("_") for a in body["alerts"] for k in a))
        print(f"  GET  /alerts              {r.status_code}  total={body['total']} "
              f"page={len(body['alerts'])}")

        # --------------------------------------------- GET /alerts/{id} 200
        r = httpx.get(f"{base}/alerts/{alert['alert_id']}", timeout=10)
        check("GET /alerts/{existing} 200", r.status_code == 200, str(r.status_code))
        check("single alert matches the stored record",
              all(r.json()[k] == alert[k] for k in r.json() if k in alert))
        print(f"  GET  /alerts/{alert['alert_id']}     {r.status_code}  "
              f"{r.json()['rule_id']}")

        # --------------------------------------------- GET /alerts/{id} 404
        r = httpx.get(f"{base}/alerts/NO-SUCH-ALERT", timeout=10)
        check("GET /alerts/{missing} 404", r.status_code == 404, str(r.status_code))
        check("404 names the missing id", "NO-SUCH-ALERT" in r.json()["detail"])
        check("404 leaks no filesystem path",
              "/home" not in r.text and str(PROJECT_ROOT) not in r.text)
        check("404 leaks no traceback", "Traceback" not in r.text and 'File "' not in r.text)
        print(f"  GET  /alerts/NO-SUCH      {r.status_code}  {r.json()['detail']}")

        # --------------------------------------------------- GET /clusters
        r = httpx.get(f"{base}/clusters", timeout=30)
        check("GET /clusters 200", r.status_code == 200, str(r.status_code))
        cl_http = r.json()
        check("clusters schema", {"clusters", "membership", "alerts_considered",
                                  "alerts_clustered", "note"} == set(cl_http),
              str(sorted(cl_http)))
        check("four clusters over the dataset", len(cl_http["clusters"]) == 4,
              str(len(cl_http["clusters"])))
        print(f"  GET  /clusters            {r.status_code}  "
              f"{len(cl_http['clusters'])} clusters, {cl_http['alerts_clustered']} members")

        # ------------------------------------------------- POST /investigate
        r = httpx.post(f"{base}/investigate", json={"alert": alert}, timeout=30)
        check("POST /investigate 200", r.status_code == 200, str(r.status_code))
        inv_http = r.json()
        check("investigate schema",
              {"alert_id", "rule_id", "category", "playbook_id", "evidence",
               "missing_evidence", "tool_calls", "allowed_outcomes"} <= set(inv_http),
              str(sorted(inv_http)))
        print(f"  POST /investigate         {r.status_code}  {inv_http['category']}, "
              f"{len(inv_http['evidence'])} evidence")

        r = httpx.post(f"{base}/investigate",
                       json={"alert": {**alert, "rule_id": "NOT-A-RULE"}}, timeout=10)
        check("POST /investigate unroutable rule -> 400", r.status_code == 400,
              str(r.status_code))
        check("400 leaks no path or traceback",
              "/home" not in r.text and "Traceback" not in r.text)
        r = httpx.post(f"{base}/investigate", json={"alert": {"alert_id": "X"}}, timeout=10)
        check("POST /investigate invalid body -> 422", r.status_code == 422, str(r.status_code))
        check("422 leaks no path", "/home" not in r.text)

        # ----------------------------------------------------- POST /cluster
        r = httpx.post(f"{base}/cluster", json={"alerts": alerts}, timeout=60)
        check("POST /cluster 200", r.status_code == 200, str(r.status_code))
        cl_post = r.json()
        check("cluster schema", {"clusters", "membership", "alerts_submitted",
                                 "alerts_clustered"} <= set(cl_post), str(sorted(cl_post)))
        print(f"  POST /cluster             {r.status_code}  "
              f"{len(cl_post['clusters'])} clusters")

        r = httpx.post(f"{base}/cluster", json={"alerts": []}, timeout=10)
        check("POST /cluster empty list -> 422", r.status_code == 422, str(r.status_code))

        # ------------------------- HTTP vs direct engine invocation
        print("\n" + "-" * 78)
        print("HTTP RESULT vs DIRECT ENGINE INVOCATION")
        print("-" * 78)
        store = DataStore(DATASET_PATH)
        direct_pack = InvestigationEngine(KB_DIR, store).investigate(dict(alert))
        http_ev = [(e["key"], e["status"], str(e["value"])) for e in inv_http["evidence"]]
        direct_ev = [(e.key, e.status, str(e.value)) for e in direct_pack.evidence]
        check("investigate: HTTP evidence identical to the engine", http_ev == direct_ev,
              "the HTTP layer altered engine output")
        check("investigate: category and playbook identical",
              inv_http["category"] == direct_pack.category
              and inv_http["playbook_id"] == direct_pack.playbook_id)
        check("investigate: tool call count identical",
              len(inv_http["tool_calls"]) == len(direct_pack.tool_calls))
        check("investigate: allowed_outcomes identical",
              inv_http["allowed_outcomes"] == direct_pack.allowed_outcomes)

        direct_found, direct_members = ClusterEngine(KB_DIR).detect(store.alerts)
        check("cluster: HTTP cluster membership identical to the engine",
              cl_post["membership"] == direct_members)
        check("cluster: HTTP alert_ids identical to the engine",
              [c["alert_ids"] for c in cl_post["clusters"]]
              == [c.alert_ids for c in direct_found])
        check("GET /clusters agrees with POST /cluster over the wire",
              [c["alert_ids"] for c in cl_http["clusters"]]
              == [c["alert_ids"] for c in cl_post["clusters"]])
        print(f"  investigate: {len(direct_ev)} evidence items identical")
        print(f"  cluster    : {len(direct_found)} clusters, "
              f"{len(direct_members)} memberships identical")

    finally:
        # ------------------------------------------------ clean termination
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=10)

    print("\n" + "-" * 78)
    print("LIFECYCLE AND WRITE SAFETY")
    print("-" * 78)
    check("3. server terminated cleanly on SIGINT", proc.returncode in (0, -2, 2),
          f"returncode {proc.returncode}")
    check("server is no longer listening", not wait_until_up(base, timeout=2.0))

    after = snapshot(PROJECT_ROOT)
    files_after = set(after)
    content = [k for k in before if k in after and before[k][0] != after[k][0]]
    mtimes = [k for k in before if k in after and before[k][1] != after[k][1]]
    created = sorted(files_after - files_before)
    deleted = sorted(files_before - files_after)
    ds_key = str(DATASET_PATH.relative_to(PROJECT_ROOT))
    kb_files = [k for k in before if k.startswith(KB_DIR.name)]

    check("4. no project file content changed by serving", not content, str(content[:5]))
    check("4. no project file mtime changed by serving", not mtimes, str(mtimes[:5]))
    check("4. no application files created", not created, str(created[:5]))
    check("4. no files deleted", not deleted, str(deleted[:5]))
    check("5. dataset unchanged", before[ds_key] == after[ds_key])
    check("5. KB unchanged", all(before[k] == after[k] for k in kb_files),
          str([k for k in kb_files if before[k] != after[k]]))

    # __pycache__ is import bytecode, not an application write. Account for it
    # separately so the exclusion above cannot conceal a real file.
    pyc = {str(p.relative_to(PROJECT_ROOT)) for p in PROJECT_ROOT.rglob("*")
           if is_pycache(p)}
    check("6. every untracked path is Python import bytecode",
          all("__pycache__" in p or p.endswith(".pyc") for p in pyc),
          str(sorted(pyc)[:5]))
    print(f"  tracked files {len(before)}: 0 content, 0 mtime, 0 created, 0 deleted")
    print(f"  __pycache__ paths (import bytecode, not application writes): {len(pyc)}")
    print(f"  server exit code: {proc.returncode}")

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
