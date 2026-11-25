"""
Build the classified implementation schema.

The previous derived_schema.json was a flat list of fields each object must
carry, derived solely from playbook evidence requirements. That is why
canonical join keys such as customer_ref were reported as "extra": the model
had no category for a field that is valid without being evidence.

This builds five classes instead, each derived rather than hand-listed:

  canonical  - fields the UC3 scope brief section 8 defines for this object,
               restricted to those in scope after the locked decisions
  evidence   - fields a playbook requests as an evidence item
  join       - identifiers that connect records or resolve tool arguments,
               taken from the tools' declared args plus canonical identifiers
  ui         - fields a tool returns for analyst display, requested by no
               playbook; the drill-down payload
  system     - objects the POC produces rather than consumes

A field may appear in more than one class (customer_ref is both canonical and
join). Validation accepts the union; the classes exist so a validator can say
*why* a field is legitimate.

Nothing here reads or writes the dataset.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

KB = Path(__file__).parent

# --- Canonical, transcribed from UC3 scope brief section 8 ---------------
# Mapped to implementation field names where the brief uses a different one.
CANONICAL = {
    "Alert": ["alert_id", "alert_created_at->created_at", "alert_source", "alert_type",
              "rule_triggered->rule_id", "customer_ref", "business_ref", "programme_ref",
              "account_ref", "card_ref_masked", "jurisdiction", "risk_band", "alert_status",
              "alert_age_days", "priority"],
    "Customer": ["customer_ref", "business_name_placeholder", "entity_type", "onboarding_date",
                 "country", "industry_segment", "KYB_status->kyb_status", "KYC_status->kyc_status",
                 "beneficial_owner_status", "risk_rating", "previous_alert_count",
                 "open_case_count", "last_review_date"],
    "TransactionSummary": ["customer_ref", "period_start", "period_end", "transaction_count",
                           "transaction_value_band", "average_transaction_size",
                           "top_merchant_category", "top_country_exposure",
                           "top-up_count->topup_count", "top-up_value_band",
                           "failed_transaction_count", "unusual_velocity_flag",
                           "new_counterparty_flag", "historical_baseline_ref"],
    "Screening": ["screening_ref", "customer_ref", "screening_type", "match_status",
                  "match_confidence_band", "list_type", "review_required", "last_screened_at",
                  "analyst_action"],
}

# Canonical fields excluded from Phase 1 by a recorded locked decision.
# Listed so the exclusion is visible in the schema rather than silent.
EXCLUDED_BY_DECISION = {
    "priority": "priority deferred to Phase 2",
    "jurisdiction": "jurisdiction / legal entity out of scope",
    "card_ref_masked": "masked card reference / PCI card handling out of scope",
    "risk_band": "not a required core POC field",
    "alert_status": "not a required core POC field",
    "alert_age_days": "not a required core POC field (derivable from created_at)",
    "transaction_value_band": "amount band not a required core POC field",
    "top-up_value_band": "amount band not a required core POC field",
    "average_transaction_size": "amount band not a required core POC field",
    "risk_rating": "not generated unless a playbook requires it",
    "alert_source": "rule_id carries the routing signal in this POC",
    "alert_type": "category is derived from rule_id, not supplied upstream",
    "business_ref": "not used by any playbook or cluster rule",
    "account_ref": "not used by any playbook or cluster rule",
    "previous_alert_count": "superseded by the get_prior_alerts tool",
    "last_review_date": "not used by any playbook",
    "period_start": "window is a tool argument, not a stored field",
    "period_end": "window is a tool argument, not a stored field",
    "unusual_velocity_flag": "playbooks compare counts against two baselines instead",
    "new_counterparty_flag": "superseded by get_counterparty_summary.new_counterparty_count",
    "historical_baseline_ref": "baselines are carried as values, not references",
    "analyst_action": "set at review time, not supplied as input",
}

# Objects the POC produces. Not part of the input schema and never validated
# against a generated dataset.
SYSTEM_OUTPUT = {
    "TriageResult": ["category (deterministic: rule_id or cluster)", "rationale", "checklist",
                     "recommended_outcome", "model_ref", "prompt_version", "playbook_pack_version"],
    "EvidencePack": ["evidence[] (key, status FOUND|MISSING, value, source)", "missing_evidence",
                     "tool_calls", "playbook_refs", "investigation_focus"],
    "Cluster": ["cluster_ref", "cluster_type", "label", "cluster_category", "alert_ids",
                "customer_refs", "programme_refs", "rule_ids", "time_span_hours", "thresholds"],
    "HumanReview": ["decision", "corrected_category", "approved_rationale", "final_outcome",
                    "escalated_to"],
    "AuditEvent": ["occurred_at", "actor_type", "actor_ref", "action", "object_id"],
}

TOOL_SOURCE = {
    "get_customer": "Customer", "get_programme": "Programme",
    "get_transaction_summary": "TransactionSummary", "get_transactions": "Transaction",
    "get_topup_summary": "TopUpSummary", "get_topups": "TopUp",
    "get_screening": "Screening", "get_kyb_history": "KybHistory",
    "get_counterparty_summary": "CounterpartySummary", "get_prior_alerts": "PriorAlerts",
}

# Arguments that are call parameters rather than stored record fields.
CALL_PARAMS = {"window_hours", "lookback_days", "limit"}


def canon_impl_names(obj: str) -> tuple[set[str], set[str]]:
    """Return (in-scope canonical impl names, excluded canonical doc names)."""
    in_scope, excluded = set(), set()
    for entry in CANONICAL.get(obj, []):
        doc, _, impl = entry.partition("->")
        impl = impl or doc
        if doc in EXCLUDED_BY_DECISION or impl in EXCLUDED_BY_DECISION:
            excluded.add(doc)
        else:
            in_scope.add(impl)
    return in_scope, excluded


def build() -> dict:
    tools = yaml.safe_load((KB / "tools.yaml").read_text())["tools"]
    pbs = yaml.safe_load((KB / "playbooks.yaml").read_text())["playbooks"]
    cfg = yaml.safe_load((KB / "config.yaml").read_text())

    # evidence fields, per object
    evidence: dict[str, set[str]] = {}
    alert_args: set[str] = set()
    for pb in pbs.values():
        for item in pb["required_evidence"]:
            obj = TOOL_SOURCE[item["tool"]]
            evidence.setdefault(obj, set()).add(item["field"])
            for src in item.get("args", {}).values():
                if isinstance(src, str) and src.startswith("alert."):
                    alert_args.add(src.split(".", 1)[1])

    # join keys: tool arguments that are record fields, plus canonical identifiers
    join: dict[str, set[str]] = {}
    for tool, spec in tools.items():
        obj = TOOL_SOURCE[tool]
        keys = {a for a in spec["args"] if a not in CALL_PARAMS}
        # a tool's return also carries the key it was looked up by
        keys |= {f for f in spec["returns"] if f.endswith("_ref") and f not in evidence.get(obj, set())}
        join.setdefault(obj, set()).update(keys)

    # cluster matching keys live on the alert and are join/reference fields
    cluster_keys: set[str] = set()
    for c in cfg["clusters"].values():
        cluster_keys.update(c["match_on"].get("group_by", []))

    schema: dict[str, dict] = {}
    for tool, spec in tools.items():
        obj = TOOL_SOURCE[tool]
        ev = evidence.get(obj, set())
        jn = join.get(obj, set())
        canon, excl = canon_impl_names(obj)
        # A field excluded by a locked decision must not re-enter through the
        # ui class just because a tool happens to return it.
        ui = set(spec["returns"]) - ev - jn - CALL_PARAMS - set(EXCLUDED_BY_DECISION)
        schema[obj] = {
            "canonical": sorted(canon),
            "evidence": sorted(ev),
            "join": sorted(jn),
            "ui": sorted(ui),
            "canonical_excluded_by_locked_decision": sorted(excl),
            "all_valid": sorted(ev | jn | canon | ui),
        }

    canon_alert, excl_alert = canon_impl_names("Alert")
    schema["Alert"] = {
        "canonical": sorted(canon_alert),
        "evidence": [],
        "join": sorted(alert_args | cluster_keys | {"alert_id", "cluster_ref"}),
        "ui": [],
        "canonical_excluded_by_locked_decision": sorted(excl_alert),
        "all_valid": sorted(canon_alert | alert_args | cluster_keys | {"alert_id", "cluster_ref"}),
    }

    return {
        "schema_version": "uc3-classified-0.2.0",
        "canonical_source": "UC3 scope brief section 8",
        "note": ("Five classes. A field is valid if it appears in any of them. "
                 "Join keys are canonical and are not evidence: no playbook requests "
                 "an identifier as an evidence item because tools take them as arguments."),
        "objects": schema,
        "system_output_objects": SYSTEM_OUTPUT,
        "excluded_by_locked_decision": EXCLUDED_BY_DECISION,
    }


if __name__ == "__main__":
    s = build()
    (KB / "derived_schema.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    tot = {k: 0 for k in ["canonical", "evidence", "join", "ui"]}
    for obj, d in s["objects"].items():
        for k in tot:
            tot[k] += len(d[k])
        print(f"  {obj:<22} canonical {len(d['canonical']):>2}  evidence {len(d['evidence']):>2}  "
              f"join {len(d['join']):>2}  ui {len(d['ui']):>2}  valid {len(d['all_valid']):>2}")
    print(f"\n  totals: canonical {tot['canonical']}  evidence {tot['evidence']}  "
          f"join {tot['join']}  ui {tot['ui']}  "
          f"system-output objects {len(s['system_output_objects'])}")
    print(f"  canonical fields excluded by locked decision: {len(s['excluded_by_locked_decision'])}")
