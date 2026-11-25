"""
Validate the KB and derive the data schemas from it.

This runs before any dataset exists. It answers, mechanically:

  - does every rule_id route to exactly one playbook?
  - does every evidence key name a registered tool?
  - does that tool actually return the named field?
  - do the tool's arguments resolve from the alert record alone?
  - how many distinct tool calls does each playbook actually make after
    deduplication by (tool, args)?
  - which fields must therefore exist in the generated data?

The last question is the point: the schema is an output of the playbooks,
not something designed alongside them and checked later.

Run: python validate_kb.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).parent

# Fields the alert record must carry for tool arguments to resolve.
ALERT_FIELDS_USED: set[str] = set()

# Which data object each tool reads from. Drives schema derivation.
TOOL_SOURCE = {
    "get_customer": "Customer",
    "get_programme": "Programme",
    "get_transaction_summary": "TransactionSummary",
    "get_transactions": "Transaction",
    "get_topup_summary": "TopUpSummary",
    "get_topups": "TopUp",
    "get_screening": "Screening",
    "get_kyb_history": "KybHistory",
    "get_counterparty_summary": "CounterpartySummary",
    "get_prior_alerts": "PriorAlerts",
}


def load(name: str) -> dict:
    return yaml.safe_load((HERE / name).read_text(encoding="utf-8"))


def freeze(args: dict) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in args.items()))


def main() -> int:
    tools_doc = load("tools.yaml")
    pb_doc = load("playbooks.yaml")
    cfg = load("config.yaml")

    tools = tools_doc["tools"]
    playbooks = pb_doc["playbooks"]
    routing = cfg["routing"]
    clusters = cfg["clusters"]

    errors: list[str] = []
    warnings: list[str] = []

    # ---- routing -------------------------------------------------------
    for rule_id, pb_id in routing.items():
        if pb_id not in playbooks:
            errors.append(f"routing: {rule_id} -> unknown playbook '{pb_id}'")

    declared = defaultdict(list)
    for pb_id, pb in playbooks.items():
        for rid in pb.get("rule_ids", []):
            declared[rid].append(pb_id)
    for rid, owners in declared.items():
        if len(owners) > 1:
            errors.append(f"routing: {rid} claimed by multiple playbooks {owners}")
        if rid not in routing:
            errors.append(f"routing: {rid} declared on '{owners[0]}' but absent from routing map")
    for rid in routing:
        if rid not in declared:
            errors.append(f"routing: {rid} in routing map but no playbook declares it")

    if "programme_level_pattern" in routing.values():
        errors.append("routing: programme_level_pattern must not have an upstream rule_id")

    # ---- evidence -> tool -> field ------------------------------------
    per_playbook_calls: dict[str, set[tuple]] = {}
    schema: dict[str, set[str]] = defaultdict(set)
    evidence_keys_by_playbook: dict[str, list[str]] = {}

    for pb_id, pb in playbooks.items():
        calls: set[tuple] = set()
        keys: list[str] = []
        seen_keys: set[str] = set()

        for item in pb.get("required_evidence", []):
            key, tool, field, args = item["key"], item["tool"], item["field"], item.get("args", {})
            keys.append(key)

            if key in seen_keys:
                errors.append(f"{pb_id}: duplicate evidence key '{key}'")
            seen_keys.add(key)

            spec = tools.get(tool)
            if spec is None:
                errors.append(f"{pb_id}/{key}: unknown tool '{tool}'")
                continue

            if field not in spec["returns"]:
                errors.append(f"{pb_id}/{key}: tool '{tool}' does not return '{field}'")
                continue

            declared_args = set(spec["args"])
            given_args = set(args)
            if given_args != declared_args:
                missing = declared_args - given_args
                extra = given_args - declared_args
                errors.append(
                    f"{pb_id}/{key}: args mismatch for '{tool}' "
                    f"(missing={sorted(missing)}, unexpected={sorted(extra)})"
                )
                continue

            for arg_name, src in args.items():
                if isinstance(src, str) and src.startswith("alert."):
                    ALERT_FIELDS_USED.add(src.split(".", 1)[1])
                elif isinstance(src, str):
                    errors.append(
                        f"{pb_id}/{key}: arg '{arg_name}' = '{src}' does not resolve "
                        "from the alert record or a literal"
                    )

            calls.add((tool, freeze(args)))
            schema[TOOL_SOURCE[tool]].add(field)

        per_playbook_calls[pb_id] = calls
        evidence_keys_by_playbook[pb_id] = keys

    # ---- clusters ------------------------------------------------------
    order = cfg["cluster_evaluation_order"]
    if set(order) != set(clusters):
        errors.append(
            f"clusters: evaluation order {sorted(order)} does not match "
            f"defined clusters {sorted(clusters)}"
        )

    for cid, c in clusters.items():
        for rid in c["match_on"].get("rule_ids", []):
            if rid not in routing:
                errors.append(f"cluster '{cid}': matches unrouted rule_id {rid}")
        th = c["thresholds"]
        if th["min_alerts"] < th.get("min_customers", 0):
            errors.append(f"cluster '{cid}': min_alerts below min_customers")
        for key in c["match_on"].get("group_by", []):
            if key != "shared_match_key":
                ALERT_FIELDS_USED.add(key)
            else:
                ALERT_FIELDS_USED.add("shared_match_key")

    overlap = defaultdict(list)
    for cid, c in clusters.items():
        for rid in c["match_on"].get("rule_ids", []):
            overlap[rid].append(cid)
    for rid, cids in overlap.items():
        if len(cids) > 1:
            warnings.append(
                f"rule {rid} can match clusters {cids}; evaluation order decides "
                "(first wins, since cluster_ref is singular)"
            )

    # ---- coverage ------------------------------------------------------
    uncovered = [p for p in playbooks if not playbooks[p].get("required_evidence")]
    for p in uncovered:
        errors.append(f"{p}: no required_evidence defined")


    # ---- Phase 1 KB contract -------------------------------------------
    outcomes = set(cfg["investigation_outcomes"])
    gb = pb_doc.get("global_ai_boundaries")
    if not gb:
        errors.append("playbooks.yaml: global_ai_boundaries block missing")
    else:
        for k in ("model_may", "model_must_not", "screening_specific"):
            if not gb.get(k):
                errors.append(f"global_ai_boundaries: '{k}' missing or empty")

    for pb_id, pb in playbooks.items():
        for fld in ("playbook_id", "version", "category", "rule_ids", "description",
                    "purpose", "allowed_outcomes", "investigation_checklist",
                    "escalation_guidance", "ai_boundaries"):
            if not pb.get(fld):
                errors.append(f"{pb_id}: contract field '{fld}' missing")
        ao = set(pb.get("allowed_outcomes", []))
        if not ao <= outcomes:
            errors.append(f"{pb_id}: allowed_outcomes not a subset of the global enum: {sorted(ao - outcomes)}")
        if not ao:
            errors.append(f"{pb_id}: allowed_outcomes empty")
        for item in pb.get("required_evidence", []):
            if "required" not in item:
                errors.append(f"{pb_id}/{item.get('key')}: 'required' flag missing")
            if not item.get("missing_behaviour"):
                errors.append(f"{pb_id}/{item.get('key')}: 'missing_behaviour' missing")

    # screening must have no clearing path
    scr = playbooks.get("sanctions_pep_screening_review_required", {})
    if "no_action_required" in scr.get("allowed_outcomes", []):
        errors.append("sanctions playbook: no_action_required would let the model clear a match")

    # cluster KB interpretation files must exist, one per configured cluster,
    # and must not restate formation mechanics
    cl_dir = HERE / "clusters"
    for cid in clusters:
        f = cl_dir / f"{cid}.yaml"
        if not f.exists():
            errors.append(f"cluster KB file missing: clusters/{cid}.yaml")
            continue
        doc = yaml.safe_load(f.read_text())
        for k in ("cluster_id", "purpose", "interpretation_guidance", "analyst_guidance",
                  "relevant_categories", "boundaries"):
            if not doc.get(k):
                errors.append(f"clusters/{cid}.yaml: '{k}' missing")
        for mech in ("match_on", "thresholds", "min_alerts", "min_customers", "time_window_hours"):
            if mech in doc:
                errors.append(f"clusters/{cid}.yaml: duplicates engine mechanics '{mech}'")

    if (HERE / "manifest.yaml").exists():
        man = yaml.safe_load((HERE / "manifest.yaml").read_text())
        if set(man.get("supported_categories", [])) != {p["category"] for p in playbooks.values()}:
            errors.append("manifest: supported_categories does not match the playbooks")
        if set(man.get("supported_cluster_types", [])) != set(clusters):
            errors.append("manifest: supported_cluster_types does not match config.yaml")
    else:
        errors.append("manifest.yaml missing")

    # ---- report --------------------------------------------------------
    print("=" * 72)
    print("KB VALIDATION")
    print("=" * 72)
    print(f"playbooks        : {len(playbooks)}")
    print(f"routed rule_ids  : {len(routing)}")
    print(f"tools registered : {len(tools)}")
    print(f"cluster types    : {len(clusters)}")
    print()

    print("TOOL CALLS PER PLAYBOOK (after dedup by tool+args)")
    print("-" * 72)
    total_items = total_calls = 0
    for pb_id in playbooks:
        n_items = len(evidence_keys_by_playbook[pb_id])
        n_calls = len(per_playbook_calls[pb_id])
        total_items += n_items
        total_calls += n_calls
        saved = n_items - n_calls
        print(f"  {pb_id:<46} {n_items:>2} evidence -> {n_calls} calls  (-{saved})")
    print(f"\n  {'TOTAL':<46} {total_items:>2} evidence -> {total_calls} calls")
    print(f"  dedup removes {total_items - total_calls} redundant calls "
          f"({100 * (total_items - total_calls) / total_items:.0f}%)")
    print()

    print("DERIVED SCHEMA — fields required by at least one playbook")
    print("-" * 72)
    for obj in sorted(schema):
        fields = sorted(schema[obj])
        print(f"\n  {obj}  ({len(fields)} fields)")
        for f in fields:
            print(f"    - {f}")
    print()

    print("ALERT RECORD — fields required to resolve tool args and clustering")
    print("-" * 72)
    for f in sorted(ALERT_FIELDS_USED):
        print(f"    - {f}")
    print()

    unused = []
    for tool, spec in tools.items():
        obj = TOOL_SOURCE[tool]
        never = set(spec["returns"]) - schema.get(obj, set())
        if never:
            unused.append((tool, sorted(never)))
    if unused:
        print("DECLARED BUT NEVER REQUIRED BY ANY PLAYBOOK")
        print("-" * 72)
        for tool, fields in unused:
            print(f"  {tool}: {', '.join(fields)}")
        print()

    if warnings:
        print("WARNINGS")
        print("-" * 72)
        for w in warnings:
            print(f"  ! {w}")
        print()

    if errors:
        print("ERRORS")
        print("-" * 72)
        for e in errors:
            print(f"  x {e}")
        print(f"\nFAILED with {len(errors)} error(s).")
        return 1

    # ---- derived_schema.json is VALIDATED here, never written ------------
    # build_schema.py is the sole writer. This module reads the committed
    # artifact and checks it against what the current KB would produce. A
    # difference is reported as staleness; it is never silently corrected.
    schema_path = HERE / "derived_schema.json"
    if not schema_path.exists():
        errors.append("derived_schema.json missing — run build_schema.py to generate it")
    else:
        import build_schema
        on_disk = json.loads(schema_path.read_text(encoding="utf-8"))
        expected = build_schema.build()
        if on_disk != expected:
            drift = []
            if on_disk.get("schema_version") != expected.get("schema_version"):
                drift.append(f"schema_version {on_disk.get('schema_version')} "
                             f"!= {expected.get('schema_version')}")
            objs_on, objs_exp = set(on_disk.get("objects", {})), set(expected["objects"])
            if objs_on != objs_exp:
                drift.append(f"objects differ: only-on-disk={sorted(objs_on - objs_exp)} "
                             f"only-expected={sorted(objs_exp - objs_on)}")
            for obj in sorted(objs_on & objs_exp):
                for cls in ("canonical", "evidence", "join", "ui", "all_valid"):
                    a = set(on_disk["objects"][obj].get(cls, []))
                    b = set(expected["objects"][obj].get(cls, []))
                    if a != b:
                        drift.append(f"{obj}.{cls}: only-on-disk={sorted(a - b)} "
                                     f"only-expected={sorted(b - a)}")
            errors.append(
                "derived_schema.json is stale relative to the KB — "
                "run build_schema.py to regenerate it. "
                + ("; ".join(drift[:5]) if drift else "content differs"))

    if errors:
        print("ERRORS")
        print("-" * 72)
        for e in errors:
            print(f"  x {e}")
        print(f"\nFAILED with {len(errors)} error(s).")
        return 1

    n = sum(len(v["all_valid"]) for v in on_disk["objects"].values())
    print("PASS — every evidence key resolves to a tool, a field and resolvable args.")
    print(f"Classified schema validated, not written "
          f"({len(on_disk['objects'])} objects, {n} valid fields, "
          f"{on_disk['schema_version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
