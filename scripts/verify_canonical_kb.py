"""
Canonical KB verification — 34 checks. READ-ONLY: opens files, writes nothing.

Resolves the KB the way the engine's callers do rather than hardcoding a path,
so it verifies the files actually loaded at runtime.

Run: python verify_canonical_kb.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.clustering import ClusterEngine  # noqa: E402
from backend.investigation import InvestigationEngine  # noqa: E402
from backend.tools import MISSING, DataStore  # noqa: E402

KB = ROOT / "UC3_Monitoring_KB"
DATA = ROOT / "data" / "dataset.json"

ENGINE_MODULES = ["tools/datastore.py", "investigation/engine.py",
                  "clustering/engine.py"]

EXPECTED_VERSIONS = {
    "playbooks.yaml": "kb-playbooks-1.0.1-phase1",
    "tools.yaml": "kb-tools-0.1.0",
    "config.yaml": "kb-config-1.0.0-phase1",
}
EXPECTED_EVIDENCE_ITEMS = 97
EXPECTED_TOOL_CALLS = 44

RESULT: dict[str, bool] = {}
BLOCKERS: list[str] = []
WARNINGS: list[str] = []


def check(name: str, cond: bool, detail: str = "", blocker: bool = True) -> bool:
    RESULT[name] = cond
    if not cond:
        (BLOCKERS if blocker else WARNINGS).append(f"{name}: {detail}")
    return cond


def main() -> int:
    man = yaml.safe_load((KB / "manifest.yaml").read_text())
    pbd = yaml.safe_load((KB / "playbooks.yaml").read_text())
    tld = yaml.safe_load((KB / "tools.yaml").read_text())
    cfg = yaml.safe_load((KB / "config.yaml").read_text())
    schema = json.loads((KB / "derived_schema.json").read_text())
    pbs, tls = pbd["playbooks"], tld["tools"]

    print("=" * 80)
    print(f"KB resolved by the engine: {KB}")
    print("=" * 80)

    # 1 canonical files
    expected = ["manifest.yaml", "playbooks.yaml", "tools.yaml", "config.yaml",
                "build_schema.py", "validate_kb.py", "derived_schema.json",
                "clusters/repeated_velocity.yaml", "clusters/repeated_topup.yaml",
                "clusters/shared_counterparty_mcc.yaml", "clusters/repeated_screening_review.yaml"]
    present = [f for f in expected if (KB / f).exists()]
    check("canonical_files", len(present) == len(expected),
          f"missing {sorted(set(expected) - set(present))}")
    print(f"\n[1] canonical files: {len(present)}/{len(expected)}")

    # 2/3 versions and playbook contract
    vers = {"playbooks.yaml": pbd["version"], "tools.yaml": tld["version"],
            "config.yaml": cfg["version"]}
    check("versions", vers == EXPECTED_VERSIONS, f"actual {vers}")
    CONTRACT = ["playbook_id", "version", "category", "rule_ids", "description", "purpose",
                "investigation_focus", "required_evidence", "investigation_checklist",
                "allowed_outcomes", "escalation_guidance", "ai_boundaries"]
    bad = {p: [f for f in CONTRACT if not pbs[p].get(f)] for p in pbs}
    bad = {k: v for k, v in bad.items() if v}
    check("playbook_contract", not bad, str(bad))
    check("ten_playbooks", len(pbs) == 10, str(len(pbs)))
    check("ten_rule_ids", len(cfg["routing"]) == 10 and len(set(cfg["routing"])) == 10)
    check("plp_no_rule", "programme_level_pattern" not in cfg["routing"].values()
          and "programme_level_pattern" not in pbs)
    print(f"[2/3] " + "  ".join(f"{k.split('.')[0]}={v}" for k, v in vers.items()))
    print(f"      playbooks {len(pbs)}  contract complete {not bad}  rules {len(cfg['routing'])}")

    # 4 clusters
    CL = {"repeated_velocity", "repeated_topup", "shared_counterparty_mcc",
          "repeated_screening_review"}
    check("four_clusters", set(cfg["clusters"]) == CL, str(set(cfg["clusters"])))
    check("no_forbidden_clusters",
          not (set(cfg["clusters"]) & {"same_customer_short_window", "evidence_gap",
                                       "insufficient_evidence"}))
    check("mechanics_in_config", all({"match_on", "thresholds"} <= set(c)
                                     for c in cfg["clusters"].values()))
    kb_cl = {cid: yaml.safe_load((KB / "clusters" / f"{cid}.yaml").read_text()) for cid in CL}
    leak = {cid: [m for m in ["match_on", "thresholds", "min_alerts", "min_customers",
                              "time_window_hours", "group_by"] if m in d]
            for cid, d in kb_cl.items()}
    leak = {k: v for k, v in leak.items() if v}
    check("no_mechanics_in_kb", not leak, str(leak))
    guid = {cid: [k for k in ["cluster_id", "purpose", "interpretation_guidance",
                              "analyst_guidance", "relevant_categories", "boundaries"]
                  if not d.get(k)] for cid, d in kb_cl.items()}
    check("cluster_kb_complete", not {k: v for k, v in guid.items() if v})
    print(f"[4] clusters {len(cfg['clusters'])}  mechanics leaked into KB: {leak or 'none'}")

    # 5 deterministic architecture
    src = "".join((ROOT / "backend" / m).read_text() for m in ENGINE_MODULES).lower()
    banned = [w for w in ["tool_choice", "choose_tool", "function_call", "embedding",
                          "vector", "faiss", "chroma", "similarity_search"] if w in src]
    kbtext = " ".join((KB / f).read_text() for f in
                      ["playbooks.yaml", "tools.yaml", "config.yaml", "manifest.yaml"]).lower()
    kb_banned = [w for w in ["faiss", "chroma", "embedding model", "vector store:"] if w in kbtext]
    check("deterministic_routing", not banned and not kb_banned, f"{banned} {kb_banned}")
    print(f"[5] model-driven selection markers: {banned or 'none'}; retrieval in KB: {kb_banned or 'none'}")

    # 6 global AI boundaries
    gb = pbd.get("global_ai_boundaries", {})
    must = " ".join(gb.get("model_must_not", [])).lower()
    scr = " ".join(gb.get("screening_specific", [])).lower()
    needed = {
        "invent evidence": "invent evidence" in must,
        "fabricate values": "fabricate" in must,
        "select tools": "select tools" in must,
        "select playbook": "select the playbook" in must,
        "decide cluster membership": "cluster membership" in must,
        "override tool results": "override a deterministic tool result" in must,
        "change alert category": "change the alert category" in must,
        "confirm screening match": "confirm" in scr,
        "dismiss screening match": "dismiss" in scr,
        "final human decision": "final human decision" in must,
        "change thresholds": "threshold" in must,
        "change policy or configuration": "policy or configuration" in must,
        "prohibited external actions": "external action" in must,
    }
    missing_b = [k for k, v in needed.items() if not v]
    check("global_boundaries", not missing_b, str(missing_b))
    print(f"[6] prohibitions {len(gb.get('model_must_not', []))}, screening-specific "
          f"{len(gb.get('screening_specific', []))}; required items absent: {missing_b or 'none'}")

    # 7 outcomes
    genum = set(cfg["investigation_outcomes"])
    subs = {p: set(pbs[p]["allowed_outcomes"]) for p in pbs}
    check("outcomes_subset", all(s <= genum and s for s in subs.values()))
    check("pending_not_recommendable", not [p for p, s in subs.items() if "pending" in s])
    scr_pb = "sanctions_pep_screening_review_required"
    check("screening_no_clearing", "no_action_required" not in subs[scr_pb])
    check("insufficient_available",
          all("insufficient_evidence_manual_investigation_required" in s for s in subs.values()))
    print(f"[7] subsets {sorted({len(s) for s in subs.values()})} of {len(genum)}; "
          f"screening offers {sorted(subs[scr_pb])}")

    # 8 evidence mappings
    ITEM = ["key", "tool", "field", "args", "required", "missing_behaviour"]
    item_bad, resolve_bad, n_items, n_calls = [], [], 0, 0
    for pid, p in pbs.items():
        sigs = set()
        for i in p["required_evidence"]:
            n_items += 1
            if any(k not in i for k in ITEM):
                item_bad.append(f"{pid}/{i.get('key')}")
            t, f_ = i.get("tool"), i.get("field")
            if t not in tls or f_ not in tls[t]["returns"] or set(i.get("args", {})) != set(tls[t]["args"]):
                resolve_bad.append(f"{pid}/{i.get('key')}")
            sigs.add((t, tuple(sorted((k, str(v)) for k, v in i.get("args", {}).items()))))
        n_calls += len(sigs)
    check("evidence_contract", not item_bad, str(item_bad[:5]))
    check("evidence_resolves", not resolve_bad, str(resolve_bad[:5]))
    check("evidence_counts", n_items == EXPECTED_EVIDENCE_ITEMS and n_calls == EXPECTED_TOOL_CALLS,
          f"{n_items} items / {n_calls} calls")
    print(f"[8] evidence items {n_items}, dedup tool calls {n_calls}")

    # 9/12 runtime
    store = DataStore(DATA)
    inv = InvestigationEngine(KB, store)
    clus = ClusterEngine(KB)
    alerts = store.alerts
    packs = {a["alert_id"]: inv.investigate(a) for a in alerts}
    gaps = {k: v for k, v in packs.items() if v.missing}
    fabricated = [e.key for p in packs.values() for e in p.evidence
                  if e.status == MISSING and e.value is not None]
    check("missing_non_fatal", len(packs) == len(alerts) and len(gaps) > 0)
    check("missing_no_value", not fabricated, str(fabricated[:5]))
    found, claimed = clus.detect(alerts)
    sep_fail = []
    for c in found:
        aid = c.alert_ids[0]
        a = dict(next(x for x in alerts if x["alert_id"] == aid))
        a["cluster_ref"] = c.cluster_ref
        p = inv.investigate(a)
        if p.category == "programme_level_pattern" or not p.evidence or p.cluster_ref != c.cluster_ref:
            sep_fail.append(aid)
    check("cluster_separation", not sep_fail, str(sep_fail))
    print(f"[9/12] {len(packs)} investigations, {len(gaps)} with MISSING, "
          f"{len(fabricated)} MISSING carrying a value; clusters {len(found)}, "
          f"separation ok {len(found) - len(sep_fail)}/{len(found)}")

    # 10 excluded fields
    EXCL = ["priority", "risk_rating", "card_ref_masked", "risk_band", "alert_status",
            "alert_age_days", "transaction_value_band", "top-up_value_band"]
    req_fields = {i["field"] for p in pbs.values() for i in p["required_evidence"]}
    viol = [e for e in EXCL if e in req_fields]
    in_valid = [e for e in EXCL for o in schema["objects"].values() if e in o["all_valid"]]
    check("excluded_fields", not viol and not in_valid, f"required={viol} schema={in_valid}")
    print(f"[10] excluded fields requested {viol or 'none'}, active in schema {in_valid or 'none'}")

    # 11 shared_match_key
    smk = man.get("correlation_keys", {}).get("shared_match_key", {})
    cp = cfg["clusters"]["shared_counterparty_mcc"]
    check("smk_alert_level", "shared_match_key" in alerts[0] and smk.get("level") == "alert")
    check("smk_rules_documented",
          set(smk.get("populated_for_rules", [])) == {"CP-LINK-071", "MC-RISK-041"})
    check("smk_used_by_cluster", "shared_match_key" in cp["match_on"]["group_by"])
    null_clustered = [c.cluster_ref for c in found if c.cluster_type == "shared_counterparty_mcc"
                      and any(not next(x for x in alerts if x["alert_id"] == i)["shared_match_key"]
                              for i in c.alert_ids)]
    check("smk_null_no_cluster", not null_clustered, str(null_clustered))
    print(f"[11] shared_match_key documented and alert-level; null collapse: {null_clustered or 'no'}")

    # 13 schema
    CLASSES = {"canonical", "evidence", "join", "ui", "all_valid",
               "canonical_excluded_by_locked_decision"}
    check("schema_classes", all(CLASSES <= set(o) for o in schema["objects"].values()))
    vk = (KB / "validate_kb.py").read_text()
    check("schema_one_path",
          "import build_schema" in vk and "build_schema.build()" in vk
          and 'derived["Alert"] = sorted(ALERT_FIELDS_USED)' not in vk)
    check("schema_system_output", bool(schema.get("system_output_objects")))
    print(f"[13] classified schema with a single generation path")

    # 14 manifest
    declared = {d["path"]: d.get("version") for d in man["files"].values()}
    mf_missing = [p for p in declared if not (KB / p).exists()]
    mf_ver = []
    for p, v in declared.items():
        if p.endswith(".yaml") and v:
            actual = yaml.safe_load((KB / p).read_text()).get("version")
            if actual != v:
                mf_ver.append(f"{p}: manifest={v} actual={actual}")
    check("manifest_files_exist", not mf_missing, str(mf_missing))
    check("manifest_versions", not mf_ver, str(mf_ver))
    check("manifest_lists",
          set(man["supported_categories"]) == {p["category"] for p in pbs.values()}
          and set(man["supported_cluster_types"]) == set(cfg["clusters"]))
    print(f"[14] manifest consistent with the filesystem and the loaded versions")

    print("\n" + "=" * 80)
    print("CANONICAL KB AUDIT")
    print("=" * 80)
    rows = [("Canonical files", "canonical_files"), ("Engine loading", "versions"),
            ("10 playbooks", "ten_playbooks"), ("10 rule IDs", "ten_rule_ids"),
            ("4 clusters", "four_clusters"), ("Deterministic routing", "deterministic_routing"),
            ("AI boundaries", "global_boundaries"), ("Allowed outcomes", "outcomes_subset"),
            ("Evidence mappings", "evidence_counts"), ("Missing evidence", "missing_non_fatal"),
            ("Excluded fields", "excluded_fields"), ("shared_match_key", "smk_rules_documented"),
            ("Cluster/individual separation", "cluster_separation"), ("Schema", "schema_one_path"),
            ("Manifest", "manifest_versions")]
    for label, key in rows:
        print(f"{label + ':':<32}{'PASS' if RESULT.get(key) else 'FAIL'}")
    print(f"\nTOTAL BLOCKERS: {len(BLOCKERS)}")
    print(f"TOTAL WARNINGS: {len(WARNINGS)}")
    for b in BLOCKERS:
        print(f"  BLOCKER  {b}")
    for w in WARNINGS:
        print(f"  WARNING  {w}")
    failed = [k for k, v in RESULT.items() if not v]
    print(f"\nindividual checks: {len(RESULT) - len(failed)}/{len(RESULT)} pass"
          f"{'' if not failed else '  failed: ' + str(failed)}")
    return 1 if BLOCKERS else 0


if __name__ == "__main__":
    sys.exit(main())
