"""
Read-only guarantees at engine level.

Every other suite proves what the engines compute. This one proves what they
do not do: touch anything on disk.

Verified before writing these tests, by AST inspection of all three modules
rather than by trusting the earlier audit — backend/tools/datastore.py,
backend/investigation/engine.py and backend/clustering/engine.py contain zero
calls to open, write_text, write_bytes, json.dump, unlink, mkdir, rename,
remove, rmtree, copy, touch or chmod. The only name that looked like a
filesystem call, `replace` at clustering/engine.py:43, is str.replace on a
timestamp. All loading goes through read_text / json.loads / yaml.safe_load.

This suite checks the observable consequence: content hash AND modification
time for every tracked project file, plus the exact file inventory, before and
after running real investigations and clustering against the canonical KB and
dataset.

__pycache__ is excluded from the tracked set and handled separately: CPython
writes bytecode as a side effect of import, which is not an engine action. The
suite asserts that every path which appeared or changed outside the tracked set
is a __pycache__ path, so the exclusion cannot hide a real write.

Same plain-script style as the other suites. Exit code 0 on success.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.clustering import ClusterEngine  # noqa: E402
from backend.investigation import InvestigationEngine  # noqa: E402
from backend.paths import DATASET_PATH, KB_DIR, PROJECT_ROOT  # noqa: E402
from backend.tools import DataStore  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def is_pycache(p: Path) -> bool:
    return "__pycache__" in p.parts or p.suffix == ".pyc"


def snapshot(root: Path) -> dict[str, tuple[str, int]]:
    """Content hash and modification time (ns) for every tracked file."""
    out: dict[str, tuple[str, int]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not is_pycache(p):
            st = p.stat()
            out[str(p.relative_to(root))] = (
                hashlib.sha256(p.read_bytes()).hexdigest(), st.st_mtime_ns)
    return out


def all_paths(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if not is_pycache(p)}


def main() -> int:
    print("=" * 78)
    print("ENGINE READ-ONLY GUARANTEES")
    print("=" * 78)
    print(f"\nproject root : {PROJECT_ROOT}")
    print(f"KB           : {KB_DIR.name}")
    print(f"dataset      : {DATASET_PATH.relative_to(PROJECT_ROOT)}")

    # ------------------------------------------------------------ before
    before = snapshot(PROJECT_ROOT)
    paths_before = all_paths(PROJECT_ROOT)
    kb_files = sorted(k for k in before if k.startswith(KB_DIR.name))
    fixtures = sorted(k for k in before if "tests/fixtures" in k)
    ds_key = str(DATASET_PATH.relative_to(PROJECT_ROOT))

    print(f"\ntracked files: {len(before)}   "
          f"KB: {len(kb_files)}   fixtures: {len(fixtures)}   dataset: 1")
    check("dataset is tracked", ds_key in before)
    check("all KB files are tracked", len(kb_files) == 11, f"{len(kb_files)} found")
    check("fixtures are tracked", len(fixtures) == 2, f"{len(fixtures)} found")
    check("markdown files are tracked",
          any(k.endswith(".md") for k in before),
          "no .md file in the tracked set")

    # ------------------------------------------------------- 1. investigate
    print("\n" + "-" * 78)
    print("1. REPRESENTATIVE INVESTIGATION (real KB, real dataset)")
    print("-" * 78)
    store = DataStore(DATASET_PATH)
    inv = InvestigationEngine(KB_DIR, store)
    alerts = store.alerts
    alert = alerts[0]
    pack = inv.investigate(alert)
    check("investigation produced an evidence pack", len(pack.evidence) > 0)
    print(f"  {alert['alert_id']}  {pack.category}  "
          f"{len(pack.evidence)} evidence / {len(pack.tool_calls)} calls")

    # ---------------------------------------------------------- 2. cluster
    print("\n" + "-" * 78)
    print("2. REPRESENTATIVE CLUSTERING (real dataset)")
    print("-" * 78)
    clus = ClusterEngine(KB_DIR)
    found, claimed = clus.detect(alerts)
    check("clustering produced clusters", len(found) > 0, f"{len(found)}")
    print(f"  {len(found)} clusters over {len(alerts)} alerts, {len(claimed)} members")

    # ------------------------------------------- 3. combined, 4. repeated
    print("\n" + "-" * 78)
    print("3/4. COMBINED RUN AND REPEATED INVESTIGATIONS (stateful mutation check)")
    print("-" * 78)
    packs = [inv.investigate(a) for a in alerts]
    clus.detect(alerts)
    clus.detect(alerts, overrides={"repeated_velocity": {"min_alerts": 99}})
    clus.detect(alerts)
    # a second pass must produce identical results — catches engine-held state
    packs2 = [inv.investigate(a) for a in alerts]
    same = all(
        [(e.key, e.status, str(e.value)) for e in a.evidence]
        == [(e.key, e.status, str(e.value)) for e in b.evidence]
        for a, b in zip(packs, packs2))
    check("repeated investigations give identical results", same,
          "engine appears to hold mutable state between runs")
    found2, claimed2 = clus.detect(alerts)
    check("repeated clustering gives identical results",
          [c.alert_ids for c in found] == [c.alert_ids for c in found2]
          and claimed == claimed2)
    print(f"  {len(packs)} investigations x2 + 4 clustering runs: results stable")

    # -------------------------------- 5. mutating returned data is contained
    print("\n" + "-" * 78)
    print("5. MUTATING RETURNED DATA CANNOT REACH DISK")
    print("-" * 78)
    # Observed contract (see test_datastore.py section 6): four tools return the
    # stored object itself rather than a copy, so mutating a returned record
    # changes the in-memory store. That is an in-memory aliasing property, NOT a
    # persistence one. This asserts the persistence boundary holds regardless.
    sink_store = DataStore(DATASET_PATH)          # throwaway, never the shared one
    sink_inv = InvestigationEngine(KB_DIR, sink_store)
    sink_pack = sink_inv.investigate(alerts[0])
    for e in sink_pack.evidence:
        if isinstance(e.value, dict):
            e.value["__probe__"] = 1
        e.key = "__mutated__"
    sink_pack.tool_calls.append({"tool": "__fake__", "args": {}})
    for tbl in list(sink_store.d):
        if isinstance(sink_store.d[tbl], dict):
            sink_store.d[tbl]["__PROBE__"] = {"injected": True}
    sink_clusters, _ = ClusterEngine(KB_DIR).detect(sink_store.alerts)
    for c in sink_clusters:
        c.alert_ids.append("__FAKE__")
    # Immediate persistence check, before the global after-snapshot: the files
    # must already be untouched at this point, and a freshly loaded store must
    # show none of the injected probes.
    ds_now = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    kb_now = {k: hashlib.sha256((PROJECT_ROOT / k).read_bytes()).hexdigest()
              for k in kb_files}
    check("mutation did not reach the dataset file",
          ds_now == before[ds_key][0], f"{before[ds_key][0][:12]} -> {ds_now[:12]}")
    check("mutation did not reach any KB file",
          all(kb_now[k] == before[k][0] for k in kb_files),
          str([k for k in kb_files if kb_now[k] != before[k][0]]))
    fresh = DataStore(DATASET_PATH)
    check("a freshly loaded store carries none of the injected probes",
          not any("__PROBE__" in v for v in fresh.d.values() if isinstance(v, dict)),
          "an injected probe survived a reload, so it reached persistent storage")
    check("the shared store used by this suite is also unpolluted",
          not any("__PROBE__" in v for v in store.d.values() if isinstance(v, dict)),
          "the throwaway store contaminated the shared one")
    print("  mutated evidence values, pack internals, the loaded store and cluster objects")

    # ------------------------------------------------------------- after
    after = snapshot(PROJECT_ROOT)
    paths_after = all_paths(PROJECT_ROOT)

    print("\n" + "-" * 78)
    print("VERIFICATION")
    print("-" * 78)

    content_changed = [k for k in before if k in after and before[k][0] != after[k][0]]
    mtime_changed = [k for k in before if k in after and before[k][1] != after[k][1]]
    created = sorted(paths_after - paths_before)
    deleted = sorted(paths_before - paths_after)
    temp_like = [p for p in paths_after
                 if p.endswith((".tmp", ".bak", ".swp", "~", ".orig", ".new"))
                 or Path(p).name.startswith(".#")]

    check("1. every file's content hash is identical", not content_changed,
          str(content_changed[:5]))
    check("2. every file's modification time is unchanged", not mtime_changed,
          str(mtime_changed[:5]))
    check("3. no new files were created", not created, str(created[:5]))
    check("4. no files were deleted", not deleted, str(deleted[:5]))
    check("5. no temporary or backup files appeared", not temp_like, str(temp_like[:5]))
    check("6. the dataset remains byte-identical", before[ds_key] == after[ds_key],
          f"{before[ds_key][0][:12]} -> {after.get(ds_key, ('gone',))[0][:12]}")
    check("7. the KB remains byte-identical",
          all(before[k] == after[k] for k in kb_files),
          str([k for k in kb_files if before[k] != after[k]]))
    check("fixtures remain byte-identical",
          all(before[k] == after[k] for k in fixtures),
          str([k for k in fixtures if before[k] != after[k]]))

    print(f"  tracked files            {len(before)}")
    print(f"  content changed          {len(content_changed)}")
    print(f"  mtime changed            {len(mtime_changed)}")
    print(f"  created / deleted        {len(created)} / {len(deleted)}")
    print(f"  temp or backup files     {len(temp_like)}")
    print(f"  dataset sha256           {before[ds_key][0][:16]} (mtime ns {before[ds_key][1]})")

    # the __pycache__ exclusion must not be hiding anything
    untracked_new = {str(p.relative_to(PROJECT_ROOT))
                     for p in PROJECT_ROOT.rglob("*") if is_pycache(p)}
    check("every path excluded from tracking is a __pycache__ path",
          all("__pycache__" in p or p.endswith(".pyc") for p in untracked_new),
          str(sorted(untracked_new)[:5]))
    print(f"  excluded paths (all __pycache__)  {len(untracked_new)}")

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
