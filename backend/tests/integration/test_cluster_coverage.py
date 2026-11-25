"""
Cluster coverage test.

Proves the cluster engine is generic rather than correct only for velocity.
Each of the four configured cluster types gets:

  - one qualifying set that must form
  - one near-miss that must not

The three near-misses fail on three different threshold dimensions
(alert count, customer count, time window), so each threshold is shown to be
load-bearing rather than decorative.

The dataset carries alerts only — no customer, transaction or screening
records exist in it. Clustering still works, which is the demonstration that
cluster detection does not depend on enrichment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.clustering import ClusterEngine  # noqa: E402

KB = Path(__file__).resolve().parents[3] / "UC3_Monitoring_KB"
DATA = Path(__file__).resolve().parents[1] / "fixtures" / "cluster_coverage.json"

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def main() -> int:
    alerts = [a for a in json.loads(DATA.read_text())["alerts"]]
    clus = ClusterEngine(KB)

    print("=" * 78)
    print("CLUSTER COVERAGE — all four types")
    print("=" * 78)
    print(f"\ndataset: {len(alerts)} alerts, no enrichment records of any kind\n")

    found, claimed = clus.detect(alerts)

    by_type = {c.cluster_type: c for c in found}
    for c in sorted(found, key=lambda x: x.cluster_ref):
        th = c.thresholds
        print(f"  {c.cluster_ref}  {c.label}")
        print(f"      alerts     {len(c.alert_ids):>2}  (min {th['min_alerts']})   {c.alert_ids}")
        print(f"      customers  {len(c.customer_refs):>2}  (min {th['min_customers']})   {c.customer_refs}")
        print(f"      span     {c.time_span_hours:>5}h  (max {th['time_window_hours']}h)")
        print(f"      programmes {c.programme_refs}   rules {c.rule_ids}")
        print()

    # ---- each type forms exactly once -------------------------------
    for ctype, label in [
        ("repeated_velocity", "velocity"),
        ("repeated_topup", "top-up"),
        ("shared_counterparty_mcc", "counterparty/MCC"),
        ("repeated_screening_review", "screening"),
    ]:
        hits = [c for c in found if c.cluster_type == ctype]
        check(f"{label} cluster forms", len(hits) == 1,
              f"{len(hits)} formed" if len(hits) != 1 else f"{hits[0].cluster_ref}")

    check("exactly four clusters formed", len(found) == 4, f"{len(found)} formed")

    # ---- near-misses do not form ------------------------------------
    clustered = set(claimed)
    near_miss_groups = {
        "velocity near-miss (count)": ["V90", "V91", "V92"],
        "top-up near-miss (count)": ["U90", "U91", "U92"],
        "counterparty near-miss (customers)": ["K90", "K91", "K92", "K93"],
        "screening near-miss (time window)": ["S90", "S91", "S92"],
    }
    for name, ids in near_miss_groups.items():
        check(f"{name} does not cluster",
              not any(i in clustered for i in ids),
              f"{[i for i in ids if i in clustered]}")

    check("null shared_match_key never groups",
          "K80" not in clustered and "K81" not in clustered)

    # ---- membership integrity ---------------------------------------
    all_members = [i for c in found for i in c.alert_ids]
    check("no alert in two clusters", len(all_members) == len(set(all_members)))
    check("clustered count matches claims", len(all_members) == len(claimed))
    unclustered = [a["alert_id"] for a in alerts if a["alert_id"] not in clustered]
    print(f"  clustered: {len(claimed)}    unclustered: {len(unclustered)}  {unclustered}\n")

    # ---- counterparty specifics -------------------------------------
    k = by_type.get("shared_counterparty_mcc")
    if k:
        check("counterparty cluster spans programmes", len(k.programme_refs) > 1,
              str(k.programme_refs))
        check("counterparty cluster mixes rule_ids", len(k.rule_ids) > 1, str(k.rule_ids))

    # ---- threshold sensitivity, per type ----------------------------
    print("-" * 78)
    print("THRESHOLD SENSITIVITY (recomputed each run, never a stored label)")
    print("-" * 78)
    cases = [
        ("repeated_velocity", {"min_alerts": 6}, 0, "raise count above actual"),
        ("repeated_topup", {"min_customers": 4}, 0, "raise customer minimum"),
        ("shared_counterparty_mcc", {"time_window_hours": 24}, 0, "shrink window"),
        # Lowering to 2 qualifies the P006 near-miss as well: its widest
        # 168h window holds 2 of its 3 alerts. Two clusters is correct.
        ("repeated_screening_review", {"min_alerts": 2}, 2, "lower count"),
    ]
    for ctype, override, expect, why in cases:
        res, _ = clus.detect(alerts, overrides={ctype: override})
        got = len([c for c in res if c.cluster_type == ctype])
        key, val = next(iter(override.items()))
        print(f"  {ctype:<28} {key}={val:<4} -> {got} cluster(s)   ({why})")
        check(f"{ctype} responds to {key}", got == expect, f"expected {expect}, got {got}")

    # screening near-miss forms once its window is widened past 200h
    res, _ = clus.detect(alerts, overrides={"repeated_screening_review": {"time_window_hours": 240}})
    screening = [c for c in res if c.cluster_type == "repeated_screening_review"]
    print(f"  {'repeated_screening_review':<28} window=240  -> {len(screening)} cluster(s)   "
          "(near-miss now qualifies)")
    check("screening near-miss forms when window widened", len(screening) == 2,
          f"{len(screening)} formed")

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
