"""
PHASE 1-4 schema audit. Read-only: opens files, writes nothing.

Canonical source: UC3 scope brief section 8 (the only field-level canonical
data model available). Objects named in the audit request that the brief does
not define at field level are reported as such rather than invented.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "UC3_Monitoring_KB"
DS = ROOT / "data" / "dataset.json"

# --- Canonical, transcribed from brief section 8 -------------------------
CANON = {
    "AlertCase": ["alert_id", "alert_created_at", "alert_source", "alert_type", "rule_triggered",
                  "customer_ref", "business_ref", "programme_ref", "account_ref", "card_ref_masked",
                  "jurisdiction", "risk_band", "alert_status", "alert_age_days", "priority",
                  "AI_triage_category", "AI_summary", "AI_confidence", "evidence_refs",
                  "missing_evidence", "assigned_analyst", "human_decision", "final_outcome",
                  "escalation_status", "audit_trail"],
    "CustomerBusinessSummary": ["customer_ref", "business_name_placeholder", "entity_type",
                                "onboarding_date", "country", "industry_segment", "KYB_status",
                                "KYC_status", "beneficial_owner_status", "risk_rating",
                                "previous_alert_count", "open_case_count", "last_review_date"],
    "TransactionPatternSummary": ["customer_ref", "period_start", "period_end", "transaction_count",
                                  "transaction_value_band", "average_transaction_size",
                                  "top_merchant_category", "top_country_exposure", "top-up_count",
                                  "top-up_value_band", "failed_transaction_count",
                                  "unusual_velocity_flag", "new_counterparty_flag",
                                  "historical_baseline_ref"],
    "ScreeningSummary": ["screening_ref", "customer_ref", "screening_type", "match_status",
                         "match_confidence_band", "list_type", "review_required", "last_screened_at",
                         "analyst_action", "evidence_refs"],
    "CaseOutcomeRecord": ["case_id", "alert_id", "analyst_ref", "decision_date", "outcome_type",
                          "escalation_level", "final_rationale", "SAR_STR_considered_flag",
                          "SAR_STR_final_decision_owner", "restriction_applied_flag",
                          "customer_contact_allowed_flag", "audit_refs"],
}

# Canonical name -> our name, where the same concept is carried differently.
ALIAS = {
    "alert_created_at": "created_at", "rule_triggered": "rule_id",
    "KYB_status": "kyb_status", "KYC_status": "kyc_status",
    "transaction_count": "transaction_count", "top-up_count": "topup_count",
    "failed_transaction_count": "failed_transaction_count",
}

# v4 locked decisions that explicitly exclude canonical fields.
EXCLUDED_BY_DECISION = {
    "priority": "locked: priority out of Phase 1 (deferred to Phase 2)",
    "jurisdiction": "locked: jurisdiction / legal entity out of scope",
    "card_ref_masked": "locked: masked card reference / PCI card handling out of scope",
    "risk_band": "locked: risk band not a required core POC field",
    "alert_age_days": "locked: alert ageing not a required core POC field (derivable from created_at)",
    "alert_status": "locked: alert status not a required core POC field",
    "transaction_value_band": "locked: amount band not a required core POC field",
    "top-up_value_band": "locked: amount band not a required core POC field",
    "average_transaction_size": "locked: amount band not a required core POC field",
    "risk_rating": "locked: risk_rating not generated unless a playbook requires it",
}

SYSTEM_OUTPUT = {
    "AI_triage_category": "produced by routing + cluster engine, not supplied as input",
    "AI_summary": "produced by the LLM at investigation time",
    "AI_confidence": "dropped: category is deterministic, nothing to be confident about",
    "evidence_refs": "produced in the evidence pack at investigation time",
    "missing_evidence": "computed by the engine from retrieval results",
    "human_decision": "HumanReview object, produced at review time",
    "final_outcome": "HumanReview object, produced at review time",
    "escalation_status": "HumanReview object, produced at review time",
    "audit_trail": "AuditEvent object, produced by the system",
    "assigned_analyst": "assigned at review time, not an input field",
}

OBJ_TO_TABLE = {
    "AlertCase": "alerts", "CustomerBusinessSummary": "customers",
    "TransactionPatternSummary": "transaction_summaries", "ScreeningSummary": "screenings",
    "CaseOutcomeRecord": None,
}
DERIVED_OBJ = {
    "AlertCase": "Alert", "CustomerBusinessSummary": "Customer",
    "TransactionPatternSummary": "TransactionSummary", "ScreeningSummary": "Screening",
}


def main() -> None:
    schema = json.loads((KB / "derived_schema.json").read_text())
    pbs = yaml.safe_load((KB / "playbooks.yaml").read_text())["playbooks"]
    raw = json.loads(DS.read_text())

    used_by_playbook: set[str] = set()
    for pb in pbs.values():
        for item in pb["required_evidence"]:
            used_by_playbook.add(item["field"])

    def in_dataset(obj: str, fld: str) -> bool:
        table = OBJ_TO_TABLE.get(obj)
        if not table:
            return False
        recs = raw.get(table)
        if table == "alerts":
            return fld in (recs[0] if recs else {})
        if not recs:
            return False
        first = next(iter(recs.values()))
        rows = first if isinstance(first, list) else [first]
        return fld in (rows[0] if rows else {})

    def classify(obj: str, fld: str, impl: str | None) -> str:
        if fld in SYSTEM_OUTPUT:
            return "E. SYSTEM OUTPUT"
        if impl and impl in used_by_playbook:
            return "B. EVIDENCE"
        joins = {"alert_id", "customer_ref", "programme_ref", "screening_ref", "business_ref",
                 "account_ref", "case_id", "analyst_ref", "transaction_id", "topup_id",
                 "historical_baseline_ref", "audit_refs", "rule_triggered"}
        if fld in joins:
            return "C. JOIN / REFERENCE"
        if fld in EXCLUDED_BY_DECISION:
            return "A. CORE CANONICAL (excluded by locked decision)"
        if impl and in_dataset(obj, impl):
            return "D. UI / DRILL-DOWN"
        return "A. CORE CANONICAL"

    print("=" * 112)
    print("PHASE 1-3 — CANONICAL (brief s8) vs IMPLEMENTATION")
    print("=" * 112)
    header = f"{'Object':<26}{'Canonical field':<30}{'Doc':<5}{'Schema':<8}{'Dataset':<9}{'Playbook':<10}{'Action':<24}"
    counts = {"KEEP": 0, "ADD TO SCHEMA": 0, "ADD TO DATASET": 0, "SYSTEM OUTPUT ONLY": 0,
              "NOT NEEDED FOR PHASE 1": 0}
    classes: dict[str, int] = {}

    for obj, fields in CANON.items():
        print(f"\n{obj}")
        print("-" * 112)
        print(header)
        for fld in fields:
            impl = ALIAS.get(fld, fld)
            dobj = DERIVED_OBJ.get(obj)
            in_schema = bool(dobj) and impl in schema.get("objects", {}).get(dobj, {}).get("all_valid", [])
            in_data = in_dataset(obj, impl)
            in_pb = impl in used_by_playbook

            if fld in SYSTEM_OUTPUT:
                action = "SYSTEM OUTPUT ONLY"
            elif fld in EXCLUDED_BY_DECISION:
                action = "NOT NEEDED FOR PHASE 1"
            elif in_data and not in_schema:
                action = "ADD TO SCHEMA"
            elif in_data:
                action = "KEEP"
            else:
                action = "NOT NEEDED FOR PHASE 1"
            counts[action] = counts.get(action, 0) + 1
            cls = classify(obj, fld, impl)
            classes[cls] = classes.get(cls, 0) + 1
            print(f"{'':<26}{fld:<30}{'yes':<5}{('yes' if in_schema else 'no'):<8}"
                  f"{('yes' if in_data else 'no'):<9}{('yes' if in_pb else 'no'):<10}{action:<24}")

    print("\n" + "=" * 112)
    print("ACTION TOTALS")
    for k, v in counts.items():
        print(f"  {k:<26} {v}")
    print("\nCLASSIFICATION TOTALS")
    for k, v in sorted(classes.items()):
        print(f"  {k:<48} {v}")

    print("\n" + "=" * 112)
    print("PHASE 4 — THE EIGHT WARNINGS")
    print("=" * 112)
    warned = [("Customer", "customer_ref"), ("Customer", "programme_ref"),
              ("Programme", "programme_ref"), ("TransactionSummary", "customer_ref"),
              ("TopUpSummary", "customer_ref"), ("Screening", "customer_ref"),
              ("Screening", "screening_ref"), ("KybHistory", "customer_ref"),
              ("CounterpartySummary", "customer_ref"), ("PriorAlerts", "customer_ref"),
              ("PriorAlerts", "last_prior_alert_at"), ("PriorAlerts", "prior_alert_categories"),
              ("PriorAlerts", "prior_alert_refs"), ("PriorAlerts", "prior_outcome_summary")]
    canon_flat = {f for fs in CANON.values() for f in fs}
    print(f"{'Object':<22}{'Field':<26}{'In brief s8?':<14}{'Class':<24}{'Action'}")
    print("-" * 112)
    for obj, fld in warned:
        in_canon = fld in canon_flat
        if fld in {"customer_ref", "programme_ref", "screening_ref"}:
            cls, act = "C. JOIN / REFERENCE", "ADD TO SCHEMA (as join key)"
        elif obj == "PriorAlerts":
            cls, act = "D. UI / DRILL-DOWN", "KEEP (not evidence; shown in pack)"
        else:
            cls, act = "?", "?"
        print(f"{obj:<22}{fld:<26}{('yes' if in_canon else 'no'):<14}{cls:<24}{act}")

    print("\n" + "=" * 112)
    print("OBJECTS NAMED IN THE AUDIT REQUEST THAT BRIEF s8 DOES NOT DEFINE AT FIELD LEVEL")
    print("=" * 112)
    for o in ["Programme", "Cluster", "Cluster Configuration", "Evidence", "KB Reference",
              "Investigation Outcome", "Human Review", "Audit Trail"]:
        print(f"  {o}")
    print("\n  brief s8 defines field lists for exactly five objects: Alert Case,")
    print("  Customer/Business Summary, Transaction Pattern Summary, Screening Summary,")
    print("  Case Outcome Record. The remaining objects appear in the brief only as prose")
    print("  (s5.7 clusters, s5.8 evidence pack, s6 KB, s9 screens) with no field list.")


if __name__ == "__main__":
    main()
