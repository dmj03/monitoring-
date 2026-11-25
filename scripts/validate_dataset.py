"""
Validate the bulk dataset by running the real engine over every alert.

Nothing here trusts the generator's intent. Clusters are recomputed from
configuration, every alert is investigated through the actual investigation
engine, and each invariant is asserted against the results.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from backend.clustering import ClusterEngine  # noqa: E402
from backend.investigation import InvestigationEngine  # noqa: E402
from backend.tools import MISSING, DataStore  # noqa: E402

KB = Path(__file__).resolve().parents[1] / "UC3_Monitoring_KB"
DS = Path(__file__).resolve().parents[1] / "data" / "dataset.json"

TABLES_FOR_TOOL = {
    "get_customer": "customers",
    "get_programme": "programmes",
    "get_transaction_summary": "transaction_summaries",
    "get_transactions": "transactions",
    "get_topup_summary": "topup_summaries",
    "get_topups": "topups",
    "get_screening": "screenings",
    "get_kyb_history": "kyb_history",
    "get_counterparty_summary": "counterparty_summaries",
    "get_prior_alerts": "prior_alerts",
}

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def main() -> int:
    raw = json.loads(DS.read_text())
    store = DataStore(DS)
    inv = InvestigationEngine(KB, store)
    clus = ClusterEngine(KB)
    cfg = yaml.safe_load((KB / "config.yaml").read_text())
    pbs = yaml.safe_load((KB / "playbooks.yaml").read_text())["playbooks"]

    alerts = raw["alerts"]
    meta = raw["_meta"]

    print("=" * 78)
    print("BULK DATASET VALIDATION")
    print("=" * 78)
    print(f"\n{len(alerts)} alerts · {len(raw['customers'])} customers · "
          f"{len(raw['programmes'])} programmes · seed {meta['seed']}\n")

    # ---- 1. routing ---------------------------------------------------
    unrouted = [a["alert_id"] for a in alerts if a["rule_id"] not in cfg["routing"]]
    check("every alert routes to a playbook", not unrouted, str(unrouted[:5]))

    # ---- 5. category coverage ----------------------------------------
    cats = Counter(pbs[cfg["routing"][a["rule_id"]]]["category"] for a in alerts)
    print("CATEGORY COVERAGE")
    for c in sorted(pbs, key=lambda p: pbs[p]["category"]):
        cat = pbs[c]["category"]
        print(f"  {cat:<48} {cats.get(cat, 0):>3}")
    check("all ten categories populated", len(cats) == 10, f"{len(cats)} present")
    check("no category below 5 alerts", all(v >= 5 for v in cats.values()),
          str({k: v for k, v in cats.items() if v < 5}))

    # ---- 6. clusters recomputed --------------------------------------
    found, claimed = clus.detect(alerts)
    print("\nCLUSTERS (recomputed from configuration)")
    for c in sorted(found, key=lambda x: x.cluster_ref):
        print(f"  {c.cluster_ref}  {c.label:<46} {len(c.alert_ids)} alerts / "
              f"{len(c.customer_refs)} customers / {c.time_span_hours}h / {c.programme_refs}")
    types_found = {c.cluster_type for c in found}
    for t in cfg["clusters"]:
        check(f"cluster type forms: {t}", t in types_found)
    check("no alert in two clusters",
          len([i for c in found for i in c.alert_ids]) == len(claimed))

    # near-miss groups must not have clustered
    nm_tags = defaultdict(list)
    for a in alerts:
        if a.get("_tag", "").startswith("nearmiss"):
            nm_tags[a["_tag"]].append(a["alert_id"])
    print("\nNEAR-MISS GROUPS")
    for tag, ids in sorted(nm_tags.items()):
        hit = [i for i in ids if i in claimed]
        print(f"  {tag:<40} {len(ids)} alerts, clustered: {len(hit)}")
        check(f"near-miss stays unclustered: {tag}", not hit, str(hit))
    check("four near-miss groups present", len(nm_tags) == 4, f"{len(nm_tags)}")

    # ---- 7. shared_match_key -----------------------------------------
    cp_rules = set(cfg["clusters"]["shared_counterparty_mcc"]["match_on"]["rule_ids"])
    bad_key = [a["alert_id"] for a in alerts
               if a["shared_match_key"] and a["rule_id"] not in cp_rules]
    check("shared_match_key only on counterparty-matched rules", not bad_key, str(bad_key[:5]))

    # ---- 2. every alerted customer has the records its playbooks read --
    missing_tables = []
    for a in alerts:
        pb = pbs[cfg["routing"][a["rule_id"]]]
        for item in pb["required_evidence"]:
            table = TABLES_FOR_TOOL[item["tool"]]
            key = a["programme_ref"] if table == "programmes" else a["customer_ref"]
            if key not in raw.get(table, {}):
                missing_tables.append(f"{a['alert_id']}: {table}/{key}")
    check("every alert's required tables exist", not missing_tables,
          f"{len(missing_tables)} missing, e.g. {missing_tables[:3]}")

    # ---- investigate every alert --------------------------------------
    packs = {}
    for a in alerts:
        packs[a["alert_id"]] = inv.investigate(a)

    total_ev = sum(len(p.evidence) for p in packs.values())
    total_calls = sum(len(p.tool_calls) for p in packs.values())
    with_gaps = [aid for aid, p in packs.items() if p.missing]
    print(f"\nINVESTIGATIONS\n  {len(packs)} alerts · {total_ev} evidence items · "
          f"{total_calls} tool calls · dedup saved {total_ev - total_calls} "
          f"({100*(total_ev-total_calls)//total_ev}%)")
    print(f"  alerts with at least one MISSING: {len(with_gaps)}")

    check("every alert produced an evidence pack", len(packs) == len(alerts))
    check("dedup active across the whole set", total_calls < total_ev)

    # ---- 3 & 4. gaps only outside clusters ----------------------------
    clustered_gaps = [aid for aid in with_gaps if aid in claimed]
    check("cluster members fully evidenced", not clustered_gaps,
          f"{len(clustered_gaps)} clustered alerts have gaps: {clustered_gaps[:5]}")
    check("evidence gaps exist on unclustered alerts", len(with_gaps) >= 10,
          f"{len(with_gaps)} alerts carry a gap")

    gap_fields = Counter(k for p in packs.values() for k in p.missing)
    print(f"  gap kinds: {dict(gap_fields.most_common())}")

    # ---- 8. directional consistency -----------------------------------
    inconsistent = []
    for a in alerts:
        p = packs[a["alert_id"]]
        ev = {e.key: e.value for e in p.evidence if e.status != MISSING}
        cat = p.category
        if cat == "unusual_transaction_velocity":
            if ev.get("transaction_activity") and ev.get("customer_transaction_baseline"):
                if ev["transaction_activity"] <= ev["customer_transaction_baseline"]:
                    inconsistent.append(f"{a['alert_id']}: velocity not above baseline")
        elif cat == "unusual_topup_pattern":
            if ev.get("topup_activity") and ev.get("customer_topup_baseline"):
                if ev["topup_activity"] <= ev["customer_topup_baseline"]:
                    inconsistent.append(f"{a['alert_id']}: top-ups not above baseline")
        elif cat == "sanctions_pep_screening_review_required":
            if ev.get("screening_status") not in ("possible_match", "review_required"):
                inconsistent.append(f"{a['alert_id']}: screening status {ev.get('screening_status')}")
        elif cat == "repeated_failed_kyb_kyc_checks":
            if not ev.get("failed_check_count"):
                inconsistent.append(f"{a['alert_id']}: no failed KYB checks")
        elif cat == "high_risk_geography_exposure":
            if not ev.get("high_risk_country_volume"):
                inconsistent.append(f"{a['alert_id']}: no high-risk country volume")
        elif cat == "possible_card_misuse":
            if ev.get("declined_transaction_count", 0) < 10:
                inconsistent.append(f"{a['alert_id']}: declines not elevated")
        elif cat == "repeated_failed_transaction_or_topup_pattern":
            if ev.get("failed_transaction_count", 0) < 10 and ev.get("failed_topup_count", 0) < 5:
                inconsistent.append(f"{a['alert_id']}: failures not elevated")
    check("evidence is directionally consistent with the alert", not inconsistent,
          f"{len(inconsistent)}: {inconsistent[:4]}")

    # ---- scope guards --------------------------------------------------
    blob = json.dumps(raw)
    check("no risk_rating generated", '"risk_rating"' not in blob)
    check("no priority generated", '"priority"' not in blob.lower())
    check("no PAN-shaped values", not any(
        len(str(v)) >= 13 and str(v).isdigit() for v in [t.get("amount") for t in
        [r for rows in raw["transactions"].values() for r in rows]]))

    # ---- customer impact ----------------------------------------------
    impacted = [c for c, r in raw["topup_summaries"].items() if r.get("customer_impact")]
    check("customer impact present on top-up alerts", len(impacted) >= 3,
          f"{len(impacted)} impacted customers")

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
