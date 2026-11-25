"""
Strict read-only validation of the bulk dataset.

Nothing is modified. Clusters are recomputed from alert-level data and the
configured thresholds are recalculated independently rather than trusting any
stored cluster_ref or label.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from backend.clustering import ClusterEngine  # noqa: E402
from backend.investigation import InvestigationEngine  # noqa: E402
from backend.tools import MISSING, DataStore  # noqa: E402

KB = Path(__file__).resolve().parents[1] / "UC3_Monitoring_KB"
DS = Path(__file__).resolve().parents[1] / "data" / "dataset.json"

TABLE_FOR_TOOL = {
    "get_customer": "customers", "get_programme": "programmes",
    "get_transaction_summary": "transaction_summaries", "get_transactions": "transactions",
    "get_topup_summary": "topup_summaries", "get_topups": "topups",
    "get_screening": "screenings", "get_kyb_history": "kyb_history",
    "get_counterparty_summary": "counterparty_summaries", "get_prior_alerts": "prior_alerts",
}

FAILURES: list[str] = []
WARNINGS: list[str] = []
RESULT: dict[str, str] = {}


def fail(msg: str) -> None:
    FAILURES.append(msg)


def warn(msg: str) -> None:
    WARNINGS.append(msg)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main() -> int:
    raw = json.loads(DS.read_text())
    store = DataStore(DS)
    inv = InvestigationEngine(KB, store)
    clus = ClusterEngine(KB)
    cfg = yaml.safe_load((KB / "config.yaml").read_text())
    pbs = yaml.safe_load((KB / "playbooks.yaml").read_text())["playbooks"]
    schema = json.loads((KB / "derived_schema.json").read_text())

    alerts = raw["alerts"]
    customers = raw["customers"]
    programmes = raw["programmes"]

    print("=" * 78)
    print("TASK 1 — DATASET-LEVEL INTEGRITY")
    print("=" * 78)

    ids = [a["alert_id"] for a in alerts]
    dupes = [k for k, v in Counter(ids).items() if v > 1]
    if dupes:
        fail(f"duplicate alert_id: {dupes}")
    print(f"  unique alert_id                 {len(set(ids))}/{len(ids)}"
          f"{'' if not dupes else '  DUPLICATES: ' + str(dupes)}")

    bad_cust = [a["alert_id"] for a in alerts if a["customer_ref"] not in customers]
    bad_prog = [a["alert_id"] for a in alerts if a["programme_ref"] not in programmes]
    if bad_cust:
        fail(f"alerts referencing unknown customer: {bad_cust}")
    if bad_prog:
        fail(f"alerts referencing unknown programme: {bad_prog}")
    print(f"  customer refs resolve           {len(alerts) - len(bad_cust)}/{len(alerts)}")
    print(f"  programme refs resolve          {len(alerts) - len(bad_prog)}/{len(alerts)}")

    mismatch = [a["alert_id"] for a in alerts
                if a["customer_ref"] in customers
                and customers[a["customer_ref"]]["programme_ref"] != a["programme_ref"]]
    if mismatch:
        fail(f"alert programme differs from its customer's programme: {mismatch}")
    print(f"  alert/customer programme agree  {len(alerts) - len(mismatch)}/{len(alerts)}")

    orphans = []
    for table in ["transaction_summaries", "transactions", "topup_summaries", "topups",
                  "screenings", "kyb_history", "counterparty_summaries", "prior_alerts"]:
        for ref in raw.get(table, {}):
            if ref not in customers:
                orphans.append(f"{table}/{ref}")
    if orphans:
        fail(f"orphan records: {orphans[:10]}")
    print(f"  orphan records                  {len(orphans)}")

    bad_ts = [a["alert_id"] for a in alerts if ts(a["created_at"]).year != 2026]
    future = [a["alert_id"] for a in alerts
              if ts(a["created_at"]) > datetime(2026, 9, 24, 9, 0, tzinfo=ts(alerts[0]["created_at"]).tzinfo)]
    if bad_ts or future:
        fail(f"invalid timestamps: {bad_ts[:5]} future: {future[:5]}")
    print(f"  timestamps valid / not future   {len(alerts) - len(bad_ts) - len(future)}/{len(alerts)}")

    blob = json.dumps(raw)
    prio = '"priority"' in blob.lower()
    rr = '"risk_rating"' in blob
    if prio:
        fail("priority field present in dataset")
    if rr:
        fail("risk_rating present in dataset")
    print(f"  no priority field               {'yes' if not prio else 'NO'}")
    print(f"  no risk_rating field            {'yes' if not rr else 'NO'}")

    # enum conformance for the values the engine actually reads
    enum_errs = []
    for ref, c in customers.items():
        if c["kyb_status"] not in {"complete", "pending_beneficial_owner_verification",
                                   "pending_documents", "expired_refresh_due", "failed", "not_started"}:
            enum_errs.append(f"{ref}.kyb_status={c['kyb_status']}")
        if c["beneficial_owner_status"] not in {"verified", "partially_verified", "pending", "unverified", None}:
            enum_errs.append(f"{ref}.beneficial_owner_status={c['beneficial_owner_status']}")
    for ref, s in raw["screenings"].items():
        if s["match_status"] not in {"no_match", "possible_match", "review_required",
                                     "confirmed_by_analyst", "dismissed_by_analyst"}:
            enum_errs.append(f"{ref}.match_status={s['match_status']}")
        if s["match_confidence_band"] not in {"none", "low", "medium", "high"}:
            enum_errs.append(f"{ref}.match_confidence_band={s['match_confidence_band']}")
    if enum_errs:
        fail(f"enum violations: {enum_errs[:10]}")
    print(f"  enum conformance                {'ok' if not enum_errs else str(enum_errs[:3])}")
    RESULT["reference_integrity"] = "PASS" if not (dupes or bad_cust or bad_prog or mismatch or orphans or enum_errs) else "FAIL"

    # ---------------------------------------------------------------- 2 & 3
    print("\n" + "=" * 78)
    print("TASK 2 & 3 — PER-CATEGORY VALIDATION AND EVIDENCE COMPLETENESS")
    print("=" * 78)

    packs = {a["alert_id"]: inv.investigate(a) for a in alerts}
    found, claimed = clus.detect(alerts)

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        by_cat[pbs[cfg["routing"][a["rule_id"]]]["category"]].append(a)

    def coherent(cat: str, ev: dict) -> tuple[bool, str]:
        if cat == "unusual_transaction_velocity":
            t, b = ev.get("transaction_activity"), ev.get("customer_transaction_baseline")
            if t and b:
                return (t > b * 3, f"{t} vs baseline {b} (x{t//b})")
            return (t is not None, "baseline intentionally MISSING")
        if cat == "unusual_topup_pattern":
            t, b = ev.get("topup_activity"), ev.get("customer_topup_baseline")
            return (bool(t and b and t > b), f"{t} vs baseline {b}")
        if cat == "high_risk_geography_exposure":
            return (bool(ev.get("high_risk_country_volume")),
                    f"{ev.get('high_risk_country_volume')} high-risk txns, top={ev.get('country_exposure')}")
        if cat == "high_risk_merchant_category":
            return (bool(ev.get("monitored_mcc_volume")),
                    f"{ev.get('monitored_mcc_volume')} monitored-MCC txns, top={ev.get('top_merchant_category')}")
        if cat == "repeated_failed_kyb_kyc_checks":
            return (bool(ev.get("failed_check_count")),
                    f"{ev.get('failed_check_count')} failed checks, BO={ev.get('beneficial_owner_status')}")
        if cat == "sanctions_pep_screening_review_required":
            return (ev.get("screening_status") in ("possible_match", "review_required"),
                    f"{ev.get('screening_status')} / {ev.get('match_confidence_band')}")
        if cat == "linked_party_or_counterparty_review":
            return (bool(ev.get("top_counterparty_share")),
                    f"top share {ev.get('top_counterparty_share')}, new {ev.get('new_counterparty_count')}")
        if cat == "possible_account_misuse":
            t, b = ev.get("transaction_activity"), ev.get("customer_transaction_baseline")
            return (bool(t and b and t > b), f"{t} vs baseline {b}, counterparties {ev.get('counterparty_spread')}")
        if cat == "possible_card_misuse":
            return (ev.get("declined_transaction_count", 0) >= 10,
                    f"{ev.get('declined_transaction_count')} declines, CNP {ev.get('card_not_present_volume')}")
        if cat == "repeated_failed_transaction_or_topup_pattern":
            return (ev.get("failed_transaction_count", 0) >= 10 or ev.get("failed_topup_count", 0) >= 5,
                    f"{ev.get('failed_transaction_count')} txn fails / {ev.get('failed_topup_count')} topup fails")
        return (True, "")

    cat_status: dict[str, str] = {}
    for cat in sorted(by_cat):
        group = by_cat[cat]
        pb_id = next(p for p in pbs if pbs[p]["category"] == cat)
        rule = pbs[pb_id]["rule_ids"][0]
        req = [i["key"] for i in pbs[pb_id]["required_evidence"]]
        aids = [a["alert_id"] for a in group]

        missing_by_alert = {aid: packs[aid].missing for aid in aids if packs[aid].missing}
        ref_bad = []
        for a in group:
            for item in pbs[pb_id]["required_evidence"]:
                tbl = TABLE_FOR_TOOL[item["tool"]]
                key = a["programme_ref"] if tbl == "programmes" else a["customer_ref"]
                if key not in raw.get(tbl, {}):
                    ref_bad.append(f"{a['alert_id']}->{tbl}/{key}")

        incoherent = []
        samples = []
        for a in group:
            ev = {e.key: e.value for e in packs[a["alert_id"]].evidence if e.status != MISSING}
            ok, detail = coherent(cat, ev)
            if not ok:
                incoherent.append(f"{a['alert_id']}: {detail}")
            elif len(samples) < 2:
                samples.append(f"{a['alert_id']} {detail}")

        status = "PASS" if (group and not ref_bad and not incoherent) else "FAIL"
        cat_status[cat] = status
        if ref_bad:
            fail(f"{cat}: broken references {ref_bad[:5]}")
        if incoherent:
            fail(f"{cat}: evidence incoherent with alert {incoherent[:5]}")

        print(f"\nRule ID:             {rule}")
        print(f"Category:            {cat}")
        print(f"Alert count:         {len(group)}")
        print(f"Alert IDs:           {', '.join(aids[:8])}{' …' if len(aids) > 8 else ''}")
        print(f"Required evidence:   {len(req)} items -> {len(packs[aids[0]].tool_calls)} tool calls")
        print(f"Evidence available:  {len(packs[aids[0]].found)}/{len(req)} on first alert")
        print(f"Missing evidence:    {missing_by_alert if missing_by_alert else 'none'}")
        print(f"Reference integrity: {'ok' if not ref_bad else ref_bad[:3]}")
        print(f"Scenario consistency:{' ' + '; '.join(samples) if samples else ' n/a'}")
        print(f"PASS/FAIL:           {status}")

    # fabricated-value check: every FOUND value must equal the source record
    fabricated = []
    for aid, p in packs.items():
        for e in p.evidence:
            if e.status != "FOUND":
                continue
            tbl = TABLE_FOR_TOOL[e.tool]
            key = e.args.get("programme_ref") or e.args.get("customer_ref")
            rec = raw.get(tbl, {}).get(key)
            if isinstance(rec, list):
                src = [r.get(e.field) for r in rec[:e.args.get("limit", 25)]]
            elif isinstance(rec, dict):
                src = rec.get(e.field)
            else:
                src = None
            if src != e.value and e.field not in ("window_hours", "lookback_days"):
                fabricated.append(f"{aid}/{e.key}: pack={e.value!r} source={src!r}")
    if fabricated:
        fail(f"fabricated evidence values: {fabricated[:5]}")
    print(f"\n  fabricated values (pack vs source record): {len(fabricated)}")
    RESULT["evidence_completeness"] = "PASS" if not fabricated and all(
        v == "PASS" for v in cat_status.values()) else "FAIL"

    # ---------------------------------------------------------------- 4 & 5
    print("\n" + "=" * 78)
    print("TASK 4 & 5 — CLUSTER VALIDATION (recalculated, not read from labels)")
    print("=" * 78)

    by_id = {a["alert_id"]: a for a in alerts}
    cluster_status: dict[str, str] = {}
    for ctype in cfg["cluster_evaluation_order"]:
        cdef = cfg["clusters"][ctype]
        th = cdef["thresholds"]
        hits = [c for c in found if c.cluster_type == ctype]
        if not hits:
            cluster_status[ctype] = "FAIL"
            fail(f"cluster type {ctype} did not form")
            print(f"\nCluster type:         {ctype}\nPASS/FAIL:            FAIL (no cluster formed)")
            continue
        c = hits[0]
        members = [by_id[i] for i in c.alert_ids]
        # recalculate independently from the raw alerts
        calc_alerts = len(members)
        calc_custs = len({m["customer_ref"] for m in members})
        calc_progs = sorted({m["programme_ref"] for m in members})
        calc_span = round((max(ts(m["created_at"]) for m in members)
                           - min(ts(m["created_at"]) for m in members)).total_seconds() / 3600, 1)
        keys = {m["shared_match_key"] for m in members}
        rules_ok = all(m["rule_id"] in cdef["match_on"]["rule_ids"] for m in members)
        qualifies = (calc_alerts >= th["min_alerts"] and calc_custs >= th["min_customers"]
                     and calc_span <= th["time_window_hours"] and rules_ok)
        same_prog_req = cdef["match_on"].get("same_programme", False)
        prog_ok = (len(calc_progs) == 1) if same_prog_req else True
        gaps = [i for i in c.alert_ids if packs[i].missing]

        status = "PASS" if (qualifies and prog_ok and not gaps) else "FAIL"
        cluster_status[ctype] = status
        if not qualifies:
            fail(f"{ctype}: recalculated values do not satisfy thresholds")
        if not prog_ok:
            fail(f"{ctype}: same_programme required but spans {calc_progs}")
        if gaps:
            fail(f"{ctype}: member alerts with missing evidence {gaps}")

        print(f"\nCluster type:         {ctype}")
        print(f"Cluster ID:           {c.cluster_ref}")
        print(f"Member alerts:        {c.alert_ids}")
        print(f"Alert count:          {calc_alerts}")
        print(f"Distinct customers:   {calc_custs}  {sorted({m['customer_ref'] for m in members})}")
        print(f"Distinct programmes:  {len(calc_progs)}  {calc_progs}")
        print(f"Time span:            {calc_span}h")
        print(f"Matching key:         {cdef['match_on'].get('group_by')} -> "
              f"{sorted(k for k in keys if k) or 'programme-scoped'}")
        print(f"Configured thresholds:min_alerts={th['min_alerts']} min_customers={th['min_customers']} "
              f"window={th['time_window_hours']}h same_programme={same_prog_req}")
        print(f"Calculated thresholds:alerts={calc_alerts} customers={calc_custs} span={calc_span}h "
              f"programmes={len(calc_progs)}")
        print(f"Member evidence gaps: {gaps or 'none'}")
        print(f"Qualifies:            {qualifies and prog_ok}")
        print(f"PASS/FAIL:            {status}")

    # near-miss
    print("\n" + "-" * 78)
    print("NEAR-MISS ANALYSIS (why each fails)")
    print("-" * 78)
    nm = defaultdict(list)
    for a in alerts:
        if a.get("_tag", "").startswith("nearmiss"):
            nm[a["_tag"]].append(a)
    nm_status: dict[str, str] = {}
    tag_to_type = {"nearmiss:velocity:count": "repeated_velocity",
                   "nearmiss:topup:count": "repeated_topup",
                   "nearmiss:counterparty:customers": "shared_counterparty_mcc",
                   "nearmiss:screening:window": "repeated_screening_review"}
    for tag, group in sorted(nm.items()):
        ctype = tag_to_type[tag]
        th = cfg["clusters"][ctype]["thresholds"]
        n = len(group)
        custs = len({g["customer_ref"] for g in group})
        span = round((max(ts(g["created_at"]) for g in group)
                      - min(ts(g["created_at"]) for g in group)).total_seconds() / 3600, 1)
        clustered = [g["alert_id"] for g in group if g["alert_id"] in claimed]
        refs_ok = all(g["customer_ref"] in customers for g in group)
        reasons = []
        if n < th["min_alerts"]:
            reasons.append(f"alert count {n} < {th['min_alerts']}")
        if custs < th["min_customers"]:
            reasons.append(f"customers {custs} < {th['min_customers']}")
        if span > th["time_window_hours"]:
            reasons.append(f"span {span}h > {th['time_window_hours']}h")
        status = "PASS" if (not clustered and reasons and refs_ok) else "FAIL"
        nm_status[ctype] = status
        if clustered:
            fail(f"near-miss {tag} clustered: {clustered}")
        if not refs_ok:
            fail(f"near-miss {tag} fails on broken references, not threshold")
        print(f"\n  {tag}")
        print(f"    alerts {[g['alert_id'] for g in group]}")
        print(f"    calculated: {n} alerts / {custs} customers / {span}h span")
        print(f"    fails on:   {'; '.join(reasons) or 'NOTHING — unexpected'}")
        print(f"    references intact (not a broken-reference artefact): {refs_ok}")
        print(f"    clustered:  {clustered or 'no'}    PASS/FAIL: {status}")

    # shared_match_key specifics
    print("\n" + "-" * 78)
    print("SHARED COUNTERPARTY / MCC SPECIFICS")
    print("-" * 78)
    cp = [c for c in found if c.cluster_type == "shared_counterparty_mcc"]
    keyed = [a for a in alerts if a["shared_match_key"]]
    null_key = [a for a in alerts if not a["shared_match_key"]]
    key_groups = Counter(a["shared_match_key"] for a in keyed)
    cp_rules = set(cfg["clusters"]["shared_counterparty_mcc"]["match_on"]["rule_ids"])
    wrong_rule = [a["alert_id"] for a in keyed if a["rule_id"] not in cp_rules]
    null_clustered_together = [c.cluster_ref for c in found
                               if c.cluster_type == "shared_counterparty_mcc"
                               and any(not by_id[i]["shared_match_key"] for i in c.alert_ids)]
    print(f"  alerts with a key:          {len(keyed)}   distribution {dict(key_groups)}")
    print(f"  alerts with null key:       {len(null_key)}")
    print(f"  keys on non-matching rules: {wrong_rule or 'none'}")
    print(f"  null keys collapsed:        {null_clustered_together or 'no'}")
    if cp:
        print(f"  cluster spans programmes:   {cp[0].programme_refs}")
        print(f"  mixed rule IDs in cluster:  {cp[0].rule_ids}")
    if wrong_rule:
        fail(f"shared_match_key on non-matching rules: {wrong_rule}")
    if null_clustered_together:
        fail("null shared_match_key alerts collapsed into a cluster")

    # ---------------------------------------------------------------- 6
    print("\n" + "=" * 78)
    print("TASK 6 — THRESHOLD SENSITIVITY (recomputed each time)")
    print("=" * 78)
    sens_ok = True
    for ctype in cfg["cluster_evaluation_order"]:
        base = [c for c in found if c.cluster_type == ctype]
        if not base:
            continue
        c = base[0]
        raise_to = len(c.alert_ids) + 1
        dissolved, _ = clus.detect(alerts, overrides={ctype: {"min_alerts": raise_to}})
        gone = not any(x.cluster_type == ctype for x in dissolved)
        restored, _ = clus.detect(alerts)
        back = any(x.cluster_type == ctype for x in restored)
        narrow, _ = clus.detect(alerts, overrides={ctype: {"time_window_hours": 1}})
        gone_w = not any(x.cluster_type == ctype for x in narrow)
        ok = gone and back and gone_w
        sens_ok &= ok
        if not ok:
            fail(f"{ctype}: threshold sensitivity failed (raise={gone} restore={back} narrow={gone_w})")
        print(f"  {ctype:<28} min_alerts={raise_to} -> dissolved:{gone}   "
              f"window=1h -> dissolved:{gone_w}   restored:{back}   {'PASS' if ok else 'FAIL'}")
    RESULT["threshold_sensitivity"] = "PASS" if sens_ok else "FAIL"

    # ---------------------------------------------------------------- 7
    print("\n" + "=" * 78)
    print("TASK 7 — INDIVIDUAL / CLUSTER SEPARATION")
    print("=" * 78)
    sep_ok = True
    for c in sorted(found, key=lambda x: x.cluster_ref):
        aid = c.alert_ids[0]
        a = dict(by_id[aid])
        a["cluster_ref"] = c.cluster_ref
        p = inv.investigate(a)
        own_cat = pbs[cfg["routing"][a["rule_id"]]]["category"]
        ok = (p.category == own_cat and len(p.evidence) > 0 and p.cluster_ref == c.cluster_ref
              and not p.missing)
        sep_ok &= ok
        if not ok:
            fail(f"{aid}: individual investigation compromised by cluster membership")
        print(f"\n  {aid}  (member of {c.cluster_ref} / {c.cluster_type})")
        print(f"    individual category : {p.category}  (own rule {a['rule_id']}, not "
              f"{c.cluster_category})")
        print(f"    individual evidence : {len(p.evidence)} items via {len(p.tool_calls)} calls, "
              f"missing {p.missing or 'none'}")
        print(f"    model input built   : {len(inv.build_llm_input(p)['evidence'])} keys, "
              f"{len(inv.build_llm_input(p)['permitted_outcomes'])} permitted outcomes")
        print(f"    cluster membership  : {p.cluster_ref} ({c.label})")
        print(f"    PASS/FAIL           : {'PASS' if ok else 'FAIL'}")
    RESULT["separation"] = "PASS" if sep_ok else "FAIL"

    # ---------------------------------------------------------------- 8
    print("\n" + "=" * 78)
    print("TASK 8 — MISSING-EVIDENCE PATH")
    print("=" * 78)
    gap_alerts = {aid: p for aid, p in packs.items() if p.missing}
    me_ok = True
    for aid, p in sorted(gap_alerts.items()):
        a = by_id[aid]
        for key in p.missing:
            item = next(e for e in p.evidence if e.key == key)
            tbl = TABLE_FOR_TOOL[item.tool]
            ref = item.args.get("programme_ref") or item.args.get("customer_ref")
            rec = raw.get(tbl, {}).get(ref)
            record_exists = rec is not None
            field_absent = record_exists and rec.get(item.field) in (None, [], "", {})
            in_cluster = aid in claimed
            ok = record_exists and field_absent and not in_cluster
            me_ok &= ok
            if not ok:
                fail(f"{aid}/{key}: record_exists={record_exists} field_absent={field_absent} "
                     f"in_cluster={in_cluster}")
            print(f"  {aid}  {key:<30} {item.tool}.{item.field}")
            print(f"        record {tbl}/{ref} exists: {record_exists}   "
                  f"field absent (legitimate, not broken ref): {field_absent}   "
                  f"resolves MISSING: {item.status == MISSING}   clustered: {in_cluster}")
    print(f"\n  {len(gap_alerts)} alerts carry a gap, all unclustered: "
          f"{not any(a in claimed for a in gap_alerts)}")
    RESULT["missing_evidence"] = "PASS" if me_ok and gap_alerts else "FAIL"

    # ---------------------------------------------------------------- 9
    print("\n" + "=" * 78)
    print("TASK 9 — SCHEMA VALIDATION")
    print("=" * 78)
    OBJ_TABLE = {"Customer": "customers", "Programme": "programmes",
                 "TransactionSummary": "transaction_summaries", "Transaction": "transactions",
                 "TopUpSummary": "topup_summaries", "TopUp": "topups",
                 "Screening": "screenings", "KybHistory": "kyb_history",
                 "CounterpartySummary": "counterparty_summaries", "PriorAlerts": "prior_alerts"}
    DISPLAY_OK = {"Customer": {"business_name_placeholder"},
                  "Programme": {"programme_name_placeholder"}}
    schema_ok = True
    objs = schema["objects"]
    print(f"  schema {schema['schema_version']} — canonical source: {schema['canonical_source']}")
    print(f"  {'Object':<22}{'rows':>5}  {'evid':>5}{'join':>6}{'canon':>7}{'ui':>5}  "
          f"{'missing evidence':<20}{'unclassified'}")
    print("  " + "-" * 94)
    for obj, table in OBJ_TABLE.items():
        spec = objs.get(obj, {})
        ev = set(spec.get("evidence", []))
        valid = set(spec.get("all_valid", []))
        recs = raw.get(table, {})
        rows = []
        for v in recs.values():
            rows.extend(v if isinstance(v, list) else [v])
        if not rows:
            print(f"  {obj:<22} no records")
            continue
        present = set(rows[0])
        missing_req = ev - present
        unclassified = present - valid
        if missing_req:
            schema_ok = False
            fail(f"{obj}: evidence fields absent from the data {sorted(missing_req)}")
        if unclassified:
            # A field in the data that no class accounts for is a real finding.
            warn(f"{obj}: fields in data matching no schema class {sorted(unclassified)}")
        print(f"  {obj:<22}{len(rows):>5}  {len(ev & present):>5}{len(set(spec.get('join', [])) & present):>6}"
              f"{len(set(spec.get('canonical', [])) & present):>7}{len(set(spec.get('ui', [])) & present):>5}  "
              f"{str(sorted(missing_req) or 'none'):<20}{sorted(unclassified) or 'none'}")
    RESULT["schema"] = "PASS" if schema_ok else "FAIL"

    # ---------------------------------------------------------------- 10
    print("\n" + "=" * 78)
    print("DATASET VALIDATION")
    print("=" * 78)
    print(f"\nTotal alerts:     {len(alerts)}")
    print(f"Total customers:  {len(customers)}")
    print(f"Total programmes: {len(programmes)}")
    order = ["unusual_transaction_velocity", "unusual_topup_pattern", "high_risk_geography_exposure",
             "high_risk_merchant_category", "repeated_failed_kyb_kyc_checks",
             "sanctions_pep_screening_review_required", "linked_party_or_counterparty_review",
             "possible_account_misuse", "possible_card_misuse",
             "repeated_failed_transaction_or_topup_pattern"]
    print("\nIndividual categories:")
    for i, cat in enumerate(order, 1):
        print(f"{i:>2}. {cat:<46} {cat_status.get(cat, 'FAIL')}  ({len(by_cat.get(cat, []))} alerts)")
    print("\nClusters:")
    for i, ct in enumerate(cfg["cluster_evaluation_order"], 1):
        print(f"{i:>2}. {ct:<46} {cluster_status.get(ct, 'FAIL')}")
    print("\nNear-miss coverage:")
    for i, ct in enumerate(cfg["cluster_evaluation_order"], 1):
        print(f"{i:>2}. {ct:<46} {nm_status.get(ct, 'FAIL')}")
    print()
    print(f"Reference integrity:           {RESULT['reference_integrity']}")
    print(f"Evidence completeness:         {RESULT['evidence_completeness']}")
    print(f"Schema validation:             {RESULT['schema']}")
    print(f"Missing-evidence scenarios:    {RESULT['missing_evidence']}")
    print(f"Cluster threshold sensitivity: {RESULT['threshold_sensitivity']}")
    print(f"Individual/cluster separation: {RESULT['separation']}")
    print(f"\nTOTAL FAILURES:  {len(FAILURES)}")
    print(f"TOTAL WARNINGS:  {len(WARNINGS)}")
    for f_ in FAILURES:
        print(f"  FAIL  {f_}")
    for w in WARNINGS:
        print(f"  WARN  {w}")
    print(f"\nFINAL STATUS:\n{'BULK DATASET READY' if not FAILURES else 'BULK DATASET NOT READY'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
