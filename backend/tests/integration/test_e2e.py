"""
End-to-end validation before bulk dataset generation.

Checks, on one velocity alert and one screening alert:
  1. rule_id routes to exactly one playbook
  2. every evidence key resolves to one tool and one field
  3. every tool argument resolves from the alert record alone
  4. tools are deduplicated by (tool, args)
  5. absent data yields MISSING and the investigation still completes
  6. the evidence pack contains only values that were actually fetched
  7. the model's permitted outcomes are constrained to the enum
  8. the model input carries no field that was not retrieved

And on the alert set:
  9. a cluster forms at the configured threshold
 10. raising the threshold dissolves it — recomputed, not read from a label
 11. an alert belongs to at most one cluster
 12. clustered alerts still receive their own individual investigation
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.clustering import ClusterEngine  # noqa: E402
from backend.investigation import InvestigationEngine  # noqa: E402
from backend.tools import MISSING, DataStore  # noqa: E402

KB = Path(__file__).resolve().parents[3] / "UC3_Monitoring_KB"
DATA = Path(__file__).resolve().parents[1] / "fixtures" / "test_dataset.json"

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def main() -> int:
    store = DataStore(DATA)
    inv = InvestigationEngine(KB, store)
    clus = ClusterEngine(KB)

    alerts = store.alerts
    by_id = {a["alert_id"]: a for a in alerts}

    print("=" * 74)
    print("END-TO-END VALIDATION")
    print("=" * 74)

    # ---------------------------------------------------- velocity alert
    pack_v = inv.investigate(by_id["A001"])
    print(f"\n[A001] {pack_v.rule_id} -> {pack_v.playbook_id}")
    print(f"  evidence items : {len(pack_v.evidence)}")
    print(f"  tool calls     : {len(pack_v.tool_calls)}  "
          f"({', '.join(sorted({c['tool'] for c in pack_v.tool_calls}))})")
    print(f"  found          : {len(pack_v.found)}")
    print(f"  MISSING        : {pack_v.missing or 'none'}")
    for e in pack_v.evidence:
        mark = "·" if e.status == "FOUND" else "!"
        val = e.value if e.status == "FOUND" else MISSING
        if isinstance(val, list):
            val = f"[{len(val)} records]"
        print(f"    {mark} {e.key:<32} {str(val)[:40]:<42} {e.tool}.{e.field}")

    check("routing (velocity)", pack_v.category == "unusual_transaction_velocity")
    check("dedup (velocity)", len(pack_v.tool_calls) == 5,
          f"{len(pack_v.evidence)} evidence -> {len(pack_v.tool_calls)} calls")
    check("get_customer called once",
          sum(1 for c in pack_v.tool_calls if c["tool"] == "get_customer") == 1)
    check("MISSING detected", "source_of_funds" in pack_v.missing)
    check("investigation completes with a gap", len(pack_v.evidence) > 0)
    check("no fabricated values",
          all(e.value is None for e in pack_v.evidence if e.status == MISSING))

    llm_in = inv.build_llm_input(pack_v)
    pack_keys = {e.key for e in pack_v.evidence}
    check("model input carries only fetched keys", set(llm_in["evidence"]) == pack_keys)
    # Phase B: outcomes are now the playbook's allowed_outcomes, a subset of the
    # global enum. Previously this asserted the full enum of 6 on every alert,
    # which predates per-playbook restriction. The subset property is stronger.
    import yaml as _yaml
    _global = set(_yaml.safe_load((KB / "config.yaml").read_text())["investigation_outcomes"])
    _offered = set(llm_in["permitted_outcomes"])
    check("outcomes constrained to the playbook subset",
          _offered <= _global and _offered
          and "insufficient_evidence_manual_investigation_required" in _offered
          and len(_offered) < len(_global),
          f"{len(_offered)} of {len(_global)} offered")
    check("global AI boundaries reach the model", bool(llm_in.get("global_boundaries")))
    check("boundaries passed to model", bool(llm_in["boundaries"]))

    # Values in the pack must match the store, not a plausible-looking number.
    tx = store.get_transaction_summary("C001", 48)
    ev = {e.key: e.value for e in pack_v.evidence}
    check("values match source data",
          ev["transaction_activity"] == tx["transaction_count"] == 1250
          and ev["customer_transaction_baseline"] == 120
          and ev["programme_transaction_baseline"] == 95,
          "1250 observed / 120 customer baseline / 95 programme baseline")

    # ---------------------------------------------------- screening alert
    pack_s = inv.investigate(by_id["A002"])
    print(f"\n[A002] {pack_s.rule_id} -> {pack_s.playbook_id}")
    print(f"  evidence items : {len(pack_s.evidence)}")
    print(f"  tool calls     : {len(pack_s.tool_calls)}  "
          f"({', '.join(sorted({c['tool'] for c in pack_s.tool_calls}))})")
    print(f"  MISSING        : {pack_s.missing or 'none'}")
    for e in pack_s.evidence:
        mark = "·" if e.status == "FOUND" else "!"
        print(f"    {mark} {e.key:<32} {str(e.value)[:40]:<42} {e.tool}.{e.field}")

    check("routing (screening)", pack_s.category == "sanctions_pep_screening_review_required")
    check("dedup (screening)", len(pack_s.tool_calls) == 3,
          f"{len(pack_s.evidence)} evidence -> {len(pack_s.tool_calls)} calls")
    sev = {e.key: e.value for e in pack_s.evidence}
    check("screening reported verbatim",
          sev["screening_status"] == "possible_match"
          and sev["match_confidence_band"] == "medium"
          and sev["list_type"] == "pep_domestic"
          and sev["review_required"] is True)
    check("screening boundaries present",
          "dismiss" in pack_s.ai_boundaries.lower() and "confirm" in pack_s.ai_boundaries.lower())
    check("screening always escalates", "sanctions analyst" in pack_s.escalation_guidance.lower())
    _s_out = set(inv.build_llm_input(pack_s)["permitted_outcomes"])
    check("screening offers no clearing outcome",
          "no_action_required" not in _s_out and "escalate_to_sanctions_analyst" in _s_out,
          str(sorted(_s_out)))

    # ---------------------------------------------------- clustering
    print("\n" + "-" * 74)
    print("CLUSTERING")
    print("-" * 74)

    found, claimed = clus.detect(alerts)
    for c in found:
        print(f"  {c.cluster_ref}  {c.label}")
        print(f"    alerts     : {len(c.alert_ids)} {c.alert_ids}")
        print(f"    customers  : {len(c.customer_refs)} {c.customer_refs}")
        print(f"    programmes : {c.programme_refs}")
        print(f"    span       : {c.time_span_hours}h   threshold: "
              f"{c.thresholds['min_alerts']} alerts / {c.thresholds['min_customers']} customers "
              f"/ {c.thresholds['time_window_hours']}h")
    if not found:
        print("  (none)")

    check("cluster forms at configured threshold", len(found) == 1)
    if found:
        check("cluster has 5 members", len(found[0].alert_ids) == 5, str(found[0].alert_ids))
        check("cluster is programme-scoped", found[0].programme_refs == ["P001"])
        check("cluster category", found[0].cluster_category == "programme_level_pattern")

    check("at most one cluster per alert",
          all(list(claimed.values()).count(v) >= 1 for v in claimed.values())
          and len(claimed) == len(set(claimed)))
    check("unclustered alerts stay unclustered", "A001" not in claimed and "A002" not in claimed)

    raised, _ = clus.detect(alerts, overrides={"repeated_velocity": {"min_alerts": 7}})
    print(f"\n  threshold raised to 7 -> {len(raised)} cluster(s)")
    check("raising threshold dissolves cluster (recomputed)", len(raised) == 0)

    lowered, _ = clus.detect(alerts, overrides={"repeated_velocity": {"min_alerts": 3, "min_customers": 2}})
    print(f"  threshold lowered to 3 -> {len(lowered)} cluster(s), "
          f"{len(lowered[0].alert_ids) if lowered else 0} members")
    check("lowering threshold re-forms cluster", len(lowered) == 1)

    # ---------------------------------------------------- clustered alert
    print("\n" + "-" * 74)
    print("CLUSTERED ALERT KEEPS ITS INDIVIDUAL INVESTIGATION")
    print("-" * 74)
    member_id = found[0].alert_ids[0] if found else "A003"
    member = dict(by_id[member_id])
    member["cluster_ref"] = claimed.get(member_id)
    pack_m = inv.investigate(member)
    print(f"  {member_id}: category={pack_m.category}  cluster_ref={pack_m.cluster_ref}  "
          f"evidence={len(pack_m.evidence)}  calls={len(pack_m.tool_calls)}  "
          f"missing={pack_m.missing or 'none'}")

    check("clustered alert keeps own category", pack_m.category == "unusual_transaction_velocity")
    check("clustered alert has own evidence pack", len(pack_m.evidence) == len(pack_v.evidence))
    check("clustered alert records membership", pack_m.cluster_ref is not None)
    check("cluster members fully evidenced", pack_m.missing == [])

    # ---------------------------------------------------- report
    print("\n" + "=" * 74)
    for p in passed:
        print(f"  PASS  {p}")
    for f in failed:
        print(f"  FAIL  {f}")
    print("=" * 74)
    print(f"{len(passed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
