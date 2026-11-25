"""
Pre-generation audit. Verifies the locked decisions against the actual files
rather than against anyone's recollection of them.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "UC3_Monitoring_KB"
FIXTURES = ROOT / "backend" / "tests" / "fixtures"
ENGINE_MODULES = ["tools/datastore.py", "investigation/engine.py",
                  "clustering/engine.py"]

tools = yaml.safe_load((KB / "tools.yaml").read_text())["tools"]
pbs = yaml.safe_load((KB / "playbooks.yaml").read_text())["playbooks"]
cfg = yaml.safe_load((KB / "config.yaml").read_text())
schema = json.loads((KB / "derived_schema.json").read_text())
data = json.loads((FIXTURES / "test_dataset.json").read_text())
cov = json.loads((FIXTURES / "cluster_coverage.json").read_text())
engine_src = "".join((ROOT / "backend" / m).read_text() for m in ENGINE_MODULES)

findings: list[tuple[str, str, str]] = []  # (section, severity, text)


def f(section: str, sev: str, text: str) -> None:
    findings.append((section, sev, text))


# ---------------------------------------------------------------- A
cats_in_pbs = {p["category"] for p in pbs.values()}
f("A", "ok", f"playbooks define {len(pbs)} categories; locked decision 23 requires 10 "
             f"-> {'match' if len(pbs) == 10 else 'MISMATCH'}")

if "programme_level_pattern" in cfg["routing"].values():
    f("A", "BLOCK", "programme_level_pattern has an upstream rule_id (violates decision on cluster-produced category)")
else:
    f("A", "ok", "programme_level_pattern has no upstream rule_id (cluster-produced only)")

if len(cfg["clusters"]) == 4:
    f("A", "ok", f"exactly 4 cluster types: {sorted(cfg['clusters'])}")
else:
    f("A", "BLOCK", f"cluster count is {len(cfg['clusters'])}, decision 14 requires 4")

banned = {"same_customer", "short_window", "evidence_gap"}
for cid in cfg["clusters"]:
    if any(b in cid for b in banned):
        f("A", "BLOCK", f"cluster '{cid}' appears to be an excluded type (decision 15)")

prio_hits = []
for name, text in [("tools.yaml", (KB / "tools.yaml").read_text()),
                   ("playbooks.yaml", (KB / "playbooks.yaml").read_text()),
                   ("config.yaml", (KB / "config.yaml").read_text()),
                   ("backend engine modules", engine_src),
                   ("derived_schema.json", json.dumps(
                       {o: v["all_valid"] for o, v in schema["objects"].items()}))]:
    if "priority" in text.lower():
        prio_hits.append(name)
f("A", "ok" if not prio_hits else "BLOCK",
  "no priority anywhere in implementation (decision 19)" if not prio_hits
  else f"priority still present in: {prio_hits}")

if "risk_rating" in json.dumps({o: v["all_valid"] for o, v in schema["objects"].items()}):
    f("A", "BLOCK", "risk_rating in derived schema but no playbook requires it (decision 21)")
else:
    f("A", "ok", "risk_rating excluded from derived schema (decision 21)")

for key in ("customer_transaction_baseline", "programme_transaction_baseline"):
    present = any(key in v.get("all_valid", []) for v in schema["objects"].values())
    f("A", "ok" if present else "BLOCK",
      f"{key} {'present' if present else 'ABSENT'} in derived schema (decision 18)")

# ---------------------------------------------------------------- B
missing_fields = []
for pid, pb in pbs.items():
    for item in pb["required_evidence"]:
        spec = tools.get(item["tool"])
        if spec is None:
            missing_fields.append(f"{pid}/{item['key']}: tool {item['tool']} undefined")
        elif item["field"] not in spec["returns"]:
            missing_fields.append(f"{pid}/{item['key']}: {item['tool']} lacks {item['field']}")
f("B", "ok" if not missing_fields else "BLOCK",
  "every evidence item resolves to a declared tool field" if not missing_fields
  else "; ".join(missing_fields))

# ---------------------------------------------------------------- C
TOOL_SOURCE = {
    "get_customer": "Customer", "get_programme": "Programme",
    "get_transaction_summary": "TransactionSummary", "get_transactions": "Transaction",
    "get_topup_summary": "TopUpSummary", "get_topups": "TopUp",
    "get_screening": "Screening", "get_kyb_history": "KybHistory",
    "get_counterparty_summary": "CounterpartySummary", "get_prior_alerts": "PriorAlerts",
}
unused = {}
for tool, spec in tools.items():
    obj = TOOL_SOURCE[tool]
    never = set(spec["returns"]) - set(schema["objects"].get(obj, {}).get("all_valid", []))
    never -= set(spec["args"]) | {"customer_ref", "programme_ref", "window_hours", "lookback_days"}
    if never:
        unused[tool] = sorted(never)
f("C", "info", f"{sum(len(v) for v in unused.values())} declared-but-unrequested fields across "
               f"{len(unused)} tools (expected: drill-down payloads)")
for tool, flds in unused.items():
    f("C", "info", f"  {tool}: {', '.join(flds)}")

# ---------------------------------------------------------------- D
ALERT_LEVEL = set(data["alerts"][0].keys())
for cid, c in cfg["clusters"].items():
    gb = c["match_on"].get("group_by", [])
    non_alert = [g for g in gb if g not in ALERT_LEVEL]
    th = c["thresholds"]
    rules = c["match_on"].get("rule_ids", [])
    unrouted = [r for r in rules if r not in cfg["routing"]]
    ok = not non_alert and not unrouted and {"min_alerts", "time_window_hours"} <= set(th)
    f("D", "ok" if ok else "BLOCK",
      f"{cid}: group_by={gb} rules={rules} thresholds={th} "
      f"{'' if ok else ' PROBLEM: ' + str(non_alert or unrouted)}")

# can each cluster type actually form in the current test data?
alerts = data["alerts"] + cov["alerts"]
for cid, c in cfg["clusters"].items():
    rules = set(c["match_on"].get("rule_ids", []))
    eligible = [a for a in alerts if a["rule_id"] in rules]
    keys_ok = [a for a in eligible
               if all(a.get(g) is not None for g in c["match_on"].get("group_by", []))]
    demonstrable = len(keys_ok) >= c["thresholds"]["min_alerts"]
    f("D", "info" if demonstrable else "GAP",
      f"{cid}: {len(eligible)} eligible alerts in test data, {len(keys_ok)} with usable group key "
      f"-> {'demonstrable' if demonstrable else 'NOT demonstrable in test data'}")

# ---------------------------------------------------------------- E
f("E", "ok" if "def build_llm_input" in engine_src and "permitted_outcomes" in engine_src else "BLOCK",
  "model input is a closed object with a constrained outcome enum")
f("E", "ok" if "plan.setdefault(sig, args)" in engine_src else "BLOCK",
  "tool calls deduplicated by (tool, resolved args)")
f("E", "ok" if 'status = MISSING if raw in (None, [], "", {})' in engine_src else "BLOCK",
  "absent values yield MISSING rather than an exception")
selects = any(w in engine_src for w in ["choose_tool", "tool_choice", "function_call"])
f("E", "ok" if not selects else "BLOCK", "no model-driven tool selection present")

# ---------------------------------------------------------------- F
INPUT_OBJECTS = set(TOOL_SOURCE.values()) | {"Alert"}
OUTPUT_OBJECTS = set(schema["system_output_objects"])
derived = set(schema["objects"])
f("F", "ok", f"classified schema covers {len(derived)} input objects, {sum(len(v['all_valid']) for v in schema['objects'].values())} valid fields")
absent_outputs = OUTPUT_OBJECTS - derived
f("F", "info", f"output-side objects not in derived schema (by design, not playbook-derived): "
               f"{sorted(absent_outputs)}")
for obj in ["Customer", "Programme", "TransactionSummary", "TopUpSummary", "Screening"]:
    f("F", "ok" if obj in derived else "BLOCK", f"{obj}: {len(schema['objects'].get(obj, {}).get('all_valid', []))} valid fields")

# ---------------------------------------------------------------- G
blockers = [t for s, sev, t in findings if sev == "BLOCK"]
gaps = [t for s, sev, t in findings if sev == "GAP"]

print("=" * 78)
for sec in "ABCDEF":
    rows = [(sev, t) for s, sev, t in findings if s == sec]
    if not rows:
        continue
    print(f"\n[{sec}]")
    for sev, t in rows:
        tag = {"ok": "  ok  ", "info": " info ", "GAP": " GAP  ", "BLOCK": "BLOCK "}[sev]
        print(f"  {tag} {t}")
print("\n" + "=" * 78)
print(f"blockers: {len(blockers)}   gaps: {len(gaps)}")
for b in blockers:
    print(f"  BLOCK  {b}")
for g in gaps:
    print(f"  GAP    {g}")
