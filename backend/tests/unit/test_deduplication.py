"""
Direct unit tests for tool-call deduplication.

The integration suite asserts deduplicated call counts for two real playbooks
(5 and 3). That proves deduplication happens; it does not prove the rule by
which calls are merged or kept apart. These tests pin the contract itself:

    plan key = (tool, tuple(sorted(resolved_args.items())))

so identical resolved arguments merge and any difference does not.

Setup injects synthetic playbooks into a throwaway InvestigationEngine
instance. Nothing on disk is touched: the canonical KB is loaded read-only and
the synthetic entries exist only on that one object.

Same plain-script style as the other suites. Exit code 0 on success.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.investigation import InvestigationEngine  # noqa: E402
from backend.paths import DATASET_PATH, KB_DIR  # noqa: E402
from backend.tools import DataStore  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(f"{name}{(' — ' + detail) if detail else ''}")


def synthetic_engine(store: DataStore) -> InvestigationEngine:
    """A real engine whose in-memory playbook/routing tables gain test entries."""
    return InvestigationEngine(KB_DIR, store)


def install(inv: InvestigationEngine, rule_id: str, pb_id: str, evidence: list[dict]) -> None:
    inv.playbooks[pb_id] = {
        "playbook_id": pb_id,
        "category": "unusual_transaction_velocity",  # any real category; unused here
        "rule_ids": [rule_id],
        "required_evidence": evidence,
        "investigation_checklist": [],
        "allowed_outcomes": ["no_action_required"],
        "escalation_guidance": "",
        "ai_boundaries": "",
        "investigation_focus": "",
    }
    inv.routing[rule_id] = pb_id


def main() -> int:
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    digest_before = hashlib.md5(DATASET_PATH.read_bytes()).hexdigest()
    store = DataStore(DATASET_PATH)

    customers = sorted(raw["customers"])
    c1, c2 = customers[0], customers[1]
    programme = raw["customers"][c1]["programme_ref"]

    print("=" * 78)
    print("TOOL-CALL DEDUPLICATION")
    print("=" * 78)
    print(f"\nusing customers {c1} and {c2}, programme {programme}")

    # ------------------------------------------------------------ Case 1
    print("\n" + "-" * 78)
    print("CASE 1 — same tool, identical arguments -> ONE call")
    print("-" * 78)
    inv = synthetic_engine(store)
    install(inv, "TEST-DEDUP-1", "pb_identical", [
        {"key": "kyb", "tool": "get_customer", "field": "kyb_status",
         "args": {"customer_ref": "alert.customer_ref"}},
        {"key": "bo", "tool": "get_customer", "field": "beneficial_owner_status",
         "args": {"customer_ref": "alert.customer_ref"}},
        {"key": "sof", "tool": "get_customer", "field": "source_of_funds",
         "args": {"customer_ref": "alert.customer_ref"}},
    ])
    alert = {"alert_id": "T-1", "rule_id": "TEST-DEDUP-1", "customer_ref": c1,
             "programme_ref": programme}
    pack = inv.investigate(alert)
    check("case 1: three evidence items", len(pack.evidence) == 3, str(len(pack.evidence)))
    check("case 1: exactly one tool call", len(pack.tool_calls) == 1,
          f"{len(pack.tool_calls)} calls: {pack.tool_calls}")
    if pack.tool_calls:
        call = pack.tool_calls[0]
        check("case 1: the call is get_customer", call["tool"] == "get_customer", call["tool"])
        check("case 1: the call carries the expected args",
              call["args"] == {"customer_ref": c1}, str(call["args"]))
    src = raw["customers"][c1]
    ev = {e.key: e.value for e in pack.evidence}
    check("case 1: all three evidence items read from the one fetched record",
          ev["kyb"] == src["kyb_status"] and ev["bo"] == src["beneficial_owner_status"]
          and ev["sof"] == src["source_of_funds"])
    print(f"  3 evidence items -> {len(pack.tool_calls)} call {pack.tool_calls}")

    # ------------------------------------------------------------ Case 2
    print("\n" + "-" * 78)
    print("CASE 2 — same tool, different arguments -> TWO calls")
    print("-" * 78)
    inv = synthetic_engine(store)
    install(inv, "TEST-DEDUP-2", "pb_diff_args", [
        {"key": "tx_24", "tool": "get_transaction_summary", "field": "transaction_count",
         "args": {"customer_ref": "alert.customer_ref", "window_hours": 24}},
        {"key": "tx_168", "tool": "get_transaction_summary", "field": "transaction_count",
         "args": {"customer_ref": "alert.customer_ref", "window_hours": 168}},
    ])
    alert = {"alert_id": "T-2", "rule_id": "TEST-DEDUP-2", "customer_ref": c1,
             "programme_ref": programme}
    pack = inv.investigate(alert)
    check("case 2: exactly two tool calls", len(pack.tool_calls) == 2,
          f"{len(pack.tool_calls)} calls")
    windows = sorted(c["args"]["window_hours"] for c in pack.tool_calls)
    check("case 2: both argument sets preserved", windows == [24, 168], str(windows))
    check("case 2: both calls are the same tool",
          {c["tool"] for c in pack.tool_calls} == {"get_transaction_summary"})
    check("case 2: both calls target the same customer",
          {c["args"]["customer_ref"] for c in pack.tool_calls} == {c1})
    print(f"  2 evidence items -> {len(pack.tool_calls)} calls, windows {windows}")

    # ------------------------------------------------------------ Case 3
    print("\n" + "-" * 78)
    print("CASE 3 — different customers -> never merged")
    print("-" * 78)
    inv = synthetic_engine(store)
    install(inv, "TEST-DEDUP-3", "pb_two_customers", [
        # business_name_placeholder is unique per customer, so a wrongly merged
        # call would produce identical values and fail the assertion below.
        # kyb_status would not: many customers share the same value.
        {"key": "name_a", "tool": "get_customer", "field": "business_name_placeholder",
         "args": {"customer_ref": "alert.customer_ref"}},
        {"key": "name_b", "tool": "get_customer", "field": "business_name_placeholder",
         "args": {"customer_ref": "alert.other_customer_ref"}},
        {"key": "kyb_a", "tool": "get_customer", "field": "kyb_status",
         "args": {"customer_ref": "alert.customer_ref"}},
    ])
    alert = {"alert_id": "T-3", "rule_id": "TEST-DEDUP-3", "customer_ref": c1,
             "other_customer_ref": c2, "programme_ref": programme}
    pack = inv.investigate(alert)
    refs = sorted(c["args"]["customer_ref"] for c in pack.tool_calls)
    check("case 3: exactly two tool calls", len(pack.tool_calls) == 2,
          f"{len(pack.tool_calls)} calls")
    check("case 3: one call per distinct customer", refs == sorted([c1, c2]), str(refs))
    ev = {e.key: e.value for e in pack.evidence}
    check("case 3: evidence resolves to the correct customer each time",
          ev["name_a"] == raw["customers"][c1]["business_name_placeholder"]
          and ev["name_b"] == raw["customers"][c2]["business_name_placeholder"],
          f"{ev['name_a']} / {ev['name_b']}")
    check("case 3: the two customers' values are actually distinguishable",
          raw["customers"][c1]["business_name_placeholder"]
          != raw["customers"][c2]["business_name_placeholder"],
          "field chosen for this assertion does not differ between the two customers")
    check("case 3: the repeated first customer still merges",
          sum(1 for c in pack.tool_calls if c["args"]["customer_ref"] == c1) == 1,
          "two items for customer A produced more than one call")
    print(f"  3 evidence items across 2 customers -> {len(pack.tool_calls)} calls, refs {refs}")

    # ------------------------------------------------------------ Case 4
    print("\n" + "-" * 78)
    print("CASE 4 — different programmes -> never merged")
    print("-" * 78)
    progs = sorted(raw["programmes"])
    inv = synthetic_engine(store)
    install(inv, "TEST-DEDUP-4", "pb_two_programmes", [
        {"key": "base_a", "tool": "get_programme", "field": "programme_transaction_baseline",
         "args": {"programme_ref": "alert.programme_ref"}},
        {"key": "base_b", "tool": "get_programme", "field": "programme_transaction_baseline",
         "args": {"programme_ref": "alert.other_programme_ref"}},
    ])
    alert = {"alert_id": "T-4", "rule_id": "TEST-DEDUP-4", "customer_ref": c1,
             "programme_ref": progs[0], "other_programme_ref": progs[1]}
    pack = inv.investigate(alert)
    check("case 4: exactly two tool calls", len(pack.tool_calls) == 2,
          f"{len(pack.tool_calls)} calls")
    ev = {e.key: e.value for e in pack.evidence}
    check("case 4: each programme resolves to its own baseline",
          ev["base_a"] == raw["programmes"][progs[0]]["programme_transaction_baseline"]
          and ev["base_b"] == raw["programmes"][progs[1]]["programme_transaction_baseline"],
          f"{ev['base_a']} / {ev['base_b']}")
    print(f"  2 programmes -> {len(pack.tool_calls)} calls")

    # ------------------------------------------------------------ Case 5
    print("\n" + "-" * 78)
    print("CASE 5 — the same contract holds on a real playbook")
    print("-" * 78)
    inv = synthetic_engine(store)
    real_alert = next(a for a in store.alerts if a["rule_id"] == "TM-VEL-014")
    pack = inv.investigate(real_alert)
    pb = inv.playbooks[inv.routing["TM-VEL-014"]]
    from collections import Counter
    want = len({(i["tool"], tuple(sorted(
        (k, str(v)) for k, v in i["args"].items()))) for i in pb["required_evidence"]})
    per_tool = Counter(c["tool"] for c in pack.tool_calls)
    check("case 5: call count equals the number of distinct (tool, args) signatures",
          len(pack.tool_calls) == want, f"{len(pack.tool_calls)} vs {want}")
    check("case 5: get_customer called once despite several items reading it",
          per_tool["get_customer"] == 1, str(per_tool["get_customer"]))
    reading_customer = sum(1 for i in pb["required_evidence"] if i["tool"] == "get_customer")
    check("case 5: that playbook really does read get_customer more than once",
          reading_customer > 1, f"{reading_customer} evidence items")
    print(f"  {len(pack.evidence)} evidence items -> {len(pack.tool_calls)} calls; "
          f"get_customer read by {reading_customer} items, called {per_tool['get_customer']}x")

    # ------------------------------------------------------------ isolation
    digest_after = hashlib.md5(DATASET_PATH.read_bytes()).hexdigest()
    check("dataset file untouched", digest_before == digest_after)
    clean = InvestigationEngine(KB_DIR, DataStore(DATASET_PATH))
    check("synthetic playbooks never reached the KB",
          not any(k.startswith("pb_") for k in clean.playbooks)
          and not any(r.startswith("TEST-DEDUP") for r in clean.routing),
          "a fresh engine still sees test entries")

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
