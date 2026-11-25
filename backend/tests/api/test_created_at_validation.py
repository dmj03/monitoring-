"""
created_at validation on POST /alerts.

Regression for a defect where POST /alerts accepted any non-empty created_at
("yesterday", or a timestamp without a UTC offset), persisted it, and every
later GET /clusters then failed with 500 because detection parses created_at
for every alert. The value lived in the source-of-truth file, so a restart did
not recover.

The fix is validation in the API schema. This suite proves:

  1. a valid created_at is accepted (201) and stored exactly as submitted
  2. an invalid created_at is rejected with 422
  3. a rejected alert is never written to the dataset
  4. duplicate handling (409, no overwrite) is unchanged
  5. existing alerts and every other table are unchanged
  6. GET /clusters keeps working after rejected requests

Runs in-process against a TEMPORARY COPY of the dataset (UC3_DATASET_PATH) and
asserts at the end that the canonical dataset and the KB were never touched.

Run directly: python test_created_at_validation.py
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

from fastapi.testclient import TestClient  # noqa: E402

from backend.clustering import ClusterEngine  # noqa: E402
from backend.paths import DATASET_PATH, KB_DIR  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


BASE_ALERT = {
    "rule_id": "TM-VEL-014",
    "customer_ref": "CUST-0101",
    "programme_ref": "P001",
    "shared_match_key": None,
    "cluster_ref": None,
}

# Accepted today and must stay accepted. The first is the dataset's own format.
VALID = {
    "ALRT-TS-OK-Z": "2026-09-24T07:00:00Z",
    "ALRT-TS-OK-OFFSET": "2026-09-24T08:00:00+01:00",
    "ALRT-TS-OK-FRACTION": "2026-09-24T07:00:00.250Z",
}

# Each of these was accepted before the fix and broke GET /clusters once stored.
INVALID = {
    "ALRT-TS-BAD-WORD": "yesterday",
    "ALRT-TS-BAD-TEXT": "not-a-date",
    "ALRT-TS-BAD-MONTH": "2026-13-01T00:00:00Z",
    "ALRT-TS-BAD-HOUR": "2026-09-22T25:00:00Z",
    "ALRT-TS-BAD-SLASHES": "22/09/2026 17:00",
    "ALRT-TS-NAIVE": "2026-09-22T17:00:00",
    "ALRT-TS-DATE-ONLY": "2026-09-22",
    "ALRT-TS-BLANK": " ",
}


def main() -> int:
    canonical_before = digest(DATASET_PATH)
    kb_before = {str(f.relative_to(KB_DIR)): digest(f) for f in sorted(KB_DIR.rglob("*"))
                 if f.is_file() and "__pycache__" not in f.parts}
    tmpdir = Path(tempfile.mkdtemp(prefix="uc3_created_at_"))
    work = tmpdir / "dataset.json"
    shutil.copy2(DATASET_PATH, work)

    original = json.loads(work.read_text(encoding="utf-8"))
    n_before = len(original["alerts"])
    tables_before = {k: json.dumps(v, sort_keys=True)
                     for k, v in original.items() if k != "alerts"}
    alerts_before = {a["alert_id"]: json.dumps(a, sort_keys=True) for a in original["alerts"]}

    print("=" * 78)
    print("CREATED_AT VALIDATION ON POST /alerts")
    print("=" * 78)
    print(f"\nworking copy : {work}")
    print(f"canonical    : {DATASET_PATH} (must not change)")

    os.environ["UC3_DATASET_PATH"] = str(work)
    try:
        from backend.api import app

        # raise_server_exceptions=False: a regression should surface as the 500 a
        # real client receives, reported as a FAIL, not abort the suite.
        with TestClient(app, raise_server_exceptions=False) as client:
            check("app is using the working copy",
                  str(app.state.dataset_path) == str(work), str(app.state.dataset_path))

            r = client.get("/clusters")
            check("GET /clusters works before any request", r.status_code == 200,
                  str(r.status_code))

            # ----------------------------------------- 2/3. invalid -> 422
            print("\n" + "-" * 78)
            print("INVALID created_at IS REJECTED AND NOT PERSISTED")
            print("-" * 78)
            for alert_id, ts in INVALID.items():
                file_before = digest(work)
                r = client.post("/alerts", json={**BASE_ALERT, "alert_id": alert_id,
                                                  "created_at": ts})
                check(f"{ts!r} returns 422", r.status_code == 422,
                      f"{r.status_code} {r.text[:120]}")
                detail = r.json().get("detail") if r.status_code == 422 else None
                check(f"{ts!r} 422 names the created_at field",
                      isinstance(detail, list)
                      and any(e.get("loc", [None])[-1] == "created_at" for e in detail),
                      str(detail)[:160])
                check(f"{ts!r} left the dataset file byte-identical",
                      digest(work) == file_before)
                check(f"{ts!r} is not readable afterwards",
                      client.get(f"/alerts/{alert_id}").status_code == 404)
                print(f"  {r.status_code}  {ts!r}")

            on_disk = json.loads(work.read_text(encoding="utf-8"))
            check("no rejected alert reached the dataset",
                  not any(a["alert_id"] in INVALID for a in on_disk["alerts"]))
            check("alert count unchanged after all rejections",
                  len(on_disk["alerts"]) == n_before, str(len(on_disk["alerts"])))

            # ------------------------------ 6. clusters still work after
            r = client.get("/clusters")
            check("GET /clusters returns 200 after rejected requests", r.status_code == 200,
                  f"{r.status_code} {r.text[:120]}")
            check("GET /clusters considers only the original alerts",
                  r.status_code == 200 and r.json()["alerts_considered"] == n_before)

            # -------------------------------------------- 1. valid -> 201
            print("\n" + "-" * 78)
            print("VALID created_at IS ACCEPTED UNCHANGED")
            print("-" * 78)
            for alert_id, ts in VALID.items():
                r = client.post("/alerts", json={**BASE_ALERT, "alert_id": alert_id,
                                                  "created_at": ts})
                check(f"{ts!r} returns 201", r.status_code == 201,
                      f"{r.status_code} {r.text[:120]}")
                check(f"{ts!r} is returned exactly as submitted",
                      r.status_code == 201 and r.json()["created_at"] == ts,
                      r.json().get("created_at", "") if r.status_code == 201 else "")
                print(f"  {r.status_code}  {ts!r}")

            stored = {a["alert_id"]: a["created_at"]
                      for a in json.loads(work.read_text(encoding="utf-8"))["alerts"]}
            check("valid timestamps are stored exactly as submitted",
                  all(stored.get(k) == v for k, v in VALID.items()),
                  str({k: stored.get(k) for k in VALID}))

            r = client.get("/clusters")
            check("GET /clusters returns 200 with the new valid alerts", r.status_code == 200,
                  f"{r.status_code} {r.text[:120]}")
            check("GET /clusters considers the new valid alerts",
                  r.status_code == 200 and r.json()["alerts_considered"] == n_before + len(VALID))
            try:
                direct, membership = ClusterEngine(KB_DIR).detect(
                    json.loads(work.read_text(encoding="utf-8"))["alerts"])
                check("GET /clusters matches the engine run directly over the stored file",
                      r.status_code == 200 and r.json()["membership"] == membership
                      and len(r.json()["clusters"]) == len(direct))
            except (ValueError, TypeError) as e:
                check("GET /clusters matches the engine run directly over the stored file",
                      False, f"engine cannot read the stored file: {e}")

            # ----------------------------------------- 4. duplicate path
            print("\n" + "-" * 78)
            print("DUPLICATE HANDLING UNCHANGED")
            print("-" * 78)
            dup_id = "ALRT-TS-OK-Z"
            r = client.post("/alerts", json={**BASE_ALERT, "alert_id": dup_id,
                                              "programme_ref": "P004",
                                              "created_at": "2026-09-25T00:00:00Z"})
            check("duplicate alert_id with a valid created_at still returns 409",
                  r.status_code == 409, str(r.status_code))
            check("409 detail unchanged",
                  r.status_code == 409 and "already exists" in r.json()["detail"])
            r = client.post("/alerts", json={**BASE_ALERT, "alert_id": dup_id,
                                              "created_at": "yesterday"})
            check("duplicate alert_id with an invalid created_at is rejected (422)",
                  r.status_code == 422, str(r.status_code))
            r = client.get(f"/alerts/{dup_id}")
            check("the original alert was not overwritten",
                  r.json()["programme_ref"] == BASE_ALERT["programme_ref"]
                  and r.json()["created_at"] == VALID[dup_id], str(r.json()))
            r = client.post("/alerts", json={**BASE_ALERT, "alert_id": "ALRT-0001",
                                              "created_at": "2026-09-25T00:00:00Z"})
            check("an existing dataset alert_id still returns 409", r.status_code == 409,
                  str(r.status_code))
            print("  409 on duplicate; 422 on duplicate with invalid created_at; "
                  "original intact")

            # ------------------------------ unchanged neighbouring rules
            r = client.post("/alerts", json={**BASE_ALERT, "alert_id": "ALRT-TS-NO-FIELD"})
            check("missing created_at is still 422", r.status_code == 422, str(r.status_code))
            r = client.post("/alerts", json={**BASE_ALERT, "alert_id": "ALRT-TS-EMPTY",
                                              "created_at": ""})
            check("empty created_at is still 422", r.status_code == 422, str(r.status_code))
            r = client.post("/alerts", json={**BASE_ALERT, "alert_id": "ALRT-TS-NULL",
                                              "created_at": None})
            check("null created_at is still 422", r.status_code == 422, str(r.status_code))

        # ----------------------------------------- 5. integrity of file
        print("\n" + "-" * 78)
        print("DATASET INTEGRITY")
        print("-" * 78)
        after = json.loads(work.read_text(encoding="utf-8"))
        tables_after = {k: json.dumps(v, sort_keys=True)
                        for k, v in after.items() if k != "alerts"}
        check("no other table changed", tables_after == tables_before,
              str([k for k in tables_before if tables_before[k] != tables_after.get(k)]))
        alerts_after = {a["alert_id"]: json.dumps(a, sort_keys=True) for a in after["alerts"]}
        modified = [k for k in alerts_before if alerts_before[k] != alerts_after.get(k)]
        check("no existing alert was modified", not modified, str(modified[:5]))
        added = sorted(set(alerts_after) - set(alerts_before))
        check("only the valid alerts were added", added == sorted(VALID), str(added))
        unparseable = []
        for a in after["alerts"]:
            try:
                if ClusterEngine._ts(a).tzinfo is None:
                    unparseable.append(a["created_at"])
            except ValueError:
                unparseable.append(a["created_at"])
        check("every stored created_at is usable by the cluster engine", not unparseable,
              str(unparseable))
        leftovers = [p.name for p in work.parent.iterdir() if p.name != work.name]
        check("no temporary file left beside the dataset", not leftovers, str(leftovers))
        print(f"  {n_before} -> {len(after['alerts'])} alerts; "
              f"{len(modified)} existing alerts modified")

    finally:
        os.environ.pop("UC3_DATASET_PATH", None)
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ----------------------------------- canonical dataset untouched
    canonical_after = digest(DATASET_PATH)
    check("the canonical dataset was never touched", canonical_before == canonical_after,
          f"{canonical_before[:12]} -> {canonical_after[:12]}")
    kb_after = {str(f.relative_to(KB_DIR)): digest(f) for f in sorted(KB_DIR.rglob("*"))
                if f.is_file() and "__pycache__" not in f.parts}
    check("the KB was never touched", kb_before == kb_after)

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
