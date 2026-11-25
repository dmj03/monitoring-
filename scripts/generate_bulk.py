"""
Bulk synthetic dataset generator.

Works backwards from the alert, never forwards from raw data: a category and
rule_id are chosen first, then the supporting records are synthesised so the
evidence is consistent with the alert. No monitoring rule is ever evaluated
and nothing here decides whether an alert should have fired.

Fields generated are those the playbooks require (the derived schema), plus a
small named display allowlist the UI needs. risk_rating is deliberately not
generated: no playbook asks for it.

Invariants the generator enforces, each asserted afterwards by
validate_dataset.py:

  1. every alert's rule_id routes to a playbook
  2. every alerted customer has a record in each table its playbooks' tools read
  3. cluster member alerts have no missing evidence
  4. evidence gaps occur only on unclustered alerts
  5. all ten individual categories are populated
  6. all four cluster types form, each with a near-miss that does not
  7. shared_match_key is non-null only on alerts the counterparty cluster matches
  8. alert evidence is directionally consistent with the alert category
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260930
NOW = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)

OUT = Path(__file__).resolve().parents[1] / "data"

# Display fields the UI needs that no playbook requires. Named explicitly so
# the exception to "derive everything" is visible rather than accidental.
DISPLAY_ALLOWLIST = {
    "Customer": ["business_name_placeholder"],
    "Programme": ["programme_name_placeholder"],
}

RULE_BY_CATEGORY = {
    "unusual_transaction_velocity": "TM-VEL-014",
    "unusual_topup_pattern": "TM-TOP-021",
    "high_risk_geography_exposure": "RG-GEO-031",
    "high_risk_merchant_category": "MC-RISK-041",
    "repeated_failed_kyb_kyc_checks": "KYB-FAIL-051",
    "sanctions_pep_screening_review_required": "SAN-PEP-061",
    "linked_party_or_counterparty_review": "CP-LINK-071",
    "possible_account_misuse": "AC-MISUSE-081",
    "possible_card_misuse": "CARD-MISUSE-091",
    "repeated_failed_transaction_or_topup_pattern": "TXN-FAIL-101",
}

# Which tables each category's playbook reads. Drives per-customer generation
# so only what a playbook needs is created (alert-first).
TABLES_BY_CATEGORY = {
    "unusual_transaction_velocity": ["customers", "programmes", "transaction_summaries", "transactions", "prior_alerts"],
    "unusual_topup_pattern": ["customers", "programmes", "topup_summaries", "topups", "prior_alerts"],
    "high_risk_geography_exposure": ["customers", "programmes", "transaction_summaries", "transactions", "prior_alerts"],
    "high_risk_merchant_category": ["customers", "programmes", "transaction_summaries", "transactions", "prior_alerts"],
    "repeated_failed_kyb_kyc_checks": ["customers", "kyb_history", "prior_alerts"],
    "sanctions_pep_screening_review_required": ["customers", "screenings", "prior_alerts"],
    "linked_party_or_counterparty_review": ["customers", "counterparty_summaries", "transactions", "prior_alerts"],
    "possible_account_misuse": ["customers", "transaction_summaries", "transactions", "counterparty_summaries", "prior_alerts"],
    "possible_card_misuse": ["customers", "transaction_summaries", "transactions", "prior_alerts"],
    "repeated_failed_transaction_or_topup_pattern": ["customers", "transaction_summaries", "transactions", "topup_summaries", "topups", "prior_alerts"],
}

INDUSTRIES = ["media_buying", "influencer_marketing", "ecommerce_marketplace", "gig_platform",
              "logistics", "travel_services", "saas", "wholesale_trade"]
MCCS = ["advertising_services", "digital_goods", "travel_agencies", "money_transfer",
        "electronics_retail", "fuel", "software_subscriptions", "precious_metals"]
ENTITY_TYPES = ["private_limited_company", "sole_trader", "partnership", "public_limited_company"]

# Gap kinds applied only to unclustered alerts.
GAP_KINDS = [
    ("customers", "source_of_funds"),
    ("customers", "beneficial_owner_status"),
    ("kyb_history", "outstanding_documents"),
    ("counterparty_summaries", "shared_counterparty_refs"),
    ("transaction_summaries", "customer_transaction_baseline"),
    ("prior_alerts", "prior_alert_count"),
]


class Generator:
    def __init__(self, seed: int = SEED) -> None:
        self.rng = random.Random(seed)
        self.alerts: list[dict] = []
        self.cust_cat: dict[str, set[str]] = {}
        self.cust_programme: dict[str, str] = {}
        self.clustered_alert_ids: set[str] = set()
        self.near_miss_alert_ids: set[str] = set()
        self._seq = 0
        self.d: dict[str, dict] = {k: {} for k in
                                   ["customers", "programmes", "transaction_summaries", "transactions",
                                    "topup_summaries", "topups", "screenings", "kyb_history",
                                    "counterparty_summaries", "prior_alerts"]}

    # ------------------------------------------------------------- helpers
    def aid(self) -> str:
        self._seq += 1
        return f"ALRT-{self._seq:04d}"

    def ts(self, hours_ago: float) -> str:
        return (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")

    def add_alert(self, category: str, customer: str, programme: str, hours_ago: float,
                  shared_match_key: str | None = None, tag: str = "") -> dict:
        a = {
            "alert_id": self.aid(),
            "rule_id": RULE_BY_CATEGORY[category],
            "customer_ref": customer,
            "programme_ref": programme,
            "created_at": self.ts(hours_ago),
            "shared_match_key": shared_match_key,
            "cluster_ref": None,
            "_category_intent": category,
            "_tag": tag,
        }
        self.alerts.append(a)
        self.cust_cat.setdefault(customer, set()).add(category)
        self.cust_programme[customer] = programme
        return a

    # ----------------------------------------------------------- programmes
    def build_programmes(self) -> None:
        specs = [
            ("P001", "Programme Alpha (media buying, white label)", 148, 14,
             ["advertising_services", "digital_goods", "money_transfer"], ["AE", "CY"]),
            ("P002", "Programme Beta (marketplace payouts)", 96, 9,
             ["money_transfer", "precious_metals"], ["AE"]),
            ("P003", "Programme Gamma (gig platform)", 210, 22,
             ["digital_goods", "software_subscriptions", "money_transfer"], ["CY"]),
            ("P004", "Programme Delta (travel and expense)", 72, 6,
             ["travel_agencies", "fuel"], ["AE", "CY"]),
        ]
        for ref, name, tx_base, tu_base, mccs, hrj in specs:
            self.d["programmes"][ref] = {
                "programme_ref": ref,
                "programme_name_placeholder": name,
                "programme_transaction_baseline": tx_base,
                "programme_topup_baseline": tu_base,
                "monitored_merchant_categories": mccs,
                "high_risk_jurisdictions": hrj,
            }

    # --------------------------------------------------------------- alerts
    def build_cluster_sets(self) -> None:
        """Qualifying groups for all four cluster types, plus near-misses."""
        # velocity: 6 alerts / 4 customers / within 48h in P001
        for i, (c, h) in enumerate([("CUST-0101", 40), ("CUST-0102", 34), ("CUST-0103", 29),
                                    ("CUST-0101", 21), ("CUST-0104", 14), ("CUST-0102", 6)]):
            a = self.add_alert("unusual_transaction_velocity", c, "P001", h, tag="cluster:velocity")
            self.clustered_alert_ids.add(a["alert_id"])
        for c, h in [("CUST-0110", 30), ("CUST-0111", 22), ("CUST-0112", 11)]:
            a = self.add_alert("unusual_transaction_velocity", c, "P002", h, tag="nearmiss:velocity:count")
            self.near_miss_alert_ids.add(a["alert_id"])

        # top-up: 5 alerts / 3 customers / within 72h in P002
        for c, h in [("CUST-0201", 66), ("CUST-0202", 54), ("CUST-0201", 40),
                     ("CUST-0203", 25), ("CUST-0202", 9)]:
            a = self.add_alert("unusual_topup_pattern", c, "P002", h, tag="cluster:topup")
            self.clustered_alert_ids.add(a["alert_id"])
        for c, h in [("CUST-0210", 50), ("CUST-0211", 30), ("CUST-0212", 12)]:
            a = self.add_alert("unusual_topup_pattern", c, "P003", h, tag="nearmiss:topup:count")
            self.near_miss_alert_ids.add(a["alert_id"])

        # counterparty/MCC: 5 alerts / 4 customers / shared key / across programmes
        for cat, c, p, h in [("linked_party_or_counterparty_review", "CUST-0301", "P001", 150),
                             ("high_risk_merchant_category", "CUST-0302", "P003", 120),
                             ("linked_party_or_counterparty_review", "CUST-0303", "P001", 88),
                             ("linked_party_or_counterparty_review", "CUST-0304", "P002", 50),
                             ("high_risk_merchant_category", "CUST-0301", "P001", 20)]:
            a = self.add_alert(cat, c, p, h, shared_match_key="CP-SHARED-7781", tag="cluster:counterparty")
            self.clustered_alert_ids.add(a["alert_id"])
        for cat, c, h in [("linked_party_or_counterparty_review", "CUST-0310", 140),
                          ("linked_party_or_counterparty_review", "CUST-0311", 100),
                          ("high_risk_merchant_category", "CUST-0310", 60),
                          ("linked_party_or_counterparty_review", "CUST-0311", 20)]:
            a = self.add_alert(cat, c, "P004", h, shared_match_key="CP-SHARED-9120",
                               tag="nearmiss:counterparty:customers")
            self.near_miss_alert_ids.add(a["alert_id"])

        # screening: 4 alerts / 3 customers / within 168h in P003
        for c, h in [("CUST-0401", 150), ("CUST-0402", 110), ("CUST-0401", 60), ("CUST-0403", 18)]:
            a = self.add_alert("sanctions_pep_screening_review_required", c, "P003", h, tag="cluster:screening")
            self.clustered_alert_ids.add(a["alert_id"])
        for c, h in [("CUST-0410", 330), ("CUST-0411", 220), ("CUST-0410", 100)]:
            a = self.add_alert("sanctions_pep_screening_review_required", c, "P004", h,
                               tag="nearmiss:screening:window")
            self.near_miss_alert_ids.add(a["alert_id"])

    def build_singletons(self) -> None:
        """Background alerts covering every category, none clustered."""
        counts = {
            "unusual_transaction_velocity": 6,
            "unusual_topup_pattern": 6,
            "high_risk_geography_exposure": 7,
            "high_risk_merchant_category": 5,
            "repeated_failed_kyb_kyc_checks": 7,
            "sanctions_pep_screening_review_required": 5,
            "linked_party_or_counterparty_review": 5,
            "possible_account_misuse": 6,
            "possible_card_misuse": 5,
            "repeated_failed_transaction_or_topup_pattern": 5,
        }
        # Programmes reserved by a cluster or near-miss group for a given
        # category. Singletons of that category are kept out of them, so a
        # background alert can never push a near-miss over its threshold.
        reserved = {
            "unusual_transaction_velocity": {"P001", "P002"},
            "unusual_topup_pattern": {"P002", "P003"},
            "sanctions_pep_screening_review_required": {"P003", "P004"},
            "linked_party_or_counterparty_review": set(),
            "high_risk_merchant_category": set(),
        }
        all_progs = ["P001", "P002", "P003", "P004"]
        n = 500
        for cat, k in counts.items():
            allowed = [p for p in all_progs if p not in reserved.get(cat, set())]
            # Space singletons of the same category and programme beyond the
            # widest cluster window (168h) so they cannot cluster together.
            spacing = defaultdict(int)
            for _ in range(k):
                n += 1
                cust = f"CUST-{n:04d}"
                prog = allowed[(n - 501) % len(allowed)]
                spacing[(cat, prog)] += 1
                hours = 6 + (spacing[(cat, prog)] - 1) * 200
                self.add_alert(cat, cust, prog, hours, tag="singleton")

    # ------------------------------------------------------------- evidence
    def _customer(self, ref: str, cats: set[str]) -> dict:
        prog = self.cust_programme[ref]
        kyb_broken = "repeated_failed_kyb_kyc_checks" in cats
        recent = "unusual_transaction_velocity" in cats and self.rng.random() < 0.5
        onboard_days = self.rng.choice([2, 3, 5]) if recent else self.rng.choice([40, 120, 300, 600])
        return {
            "customer_ref": ref,
            "business_name_placeholder": f"Synthetic Business {ref.split('-')[1]} OU",
            "entity_type": self.rng.choices(ENTITY_TYPES, weights=[70, 18, 8, 4])[0],
            "onboarding_date": (NOW - timedelta(days=onboard_days)).date().isoformat(),
            "country": self.rng.choice(["EE", "LV", "LT", "FR", "DE", "PL", "ES"]),
            "industry_segment": self.rng.choice(INDUSTRIES),
            "programme_ref": prog,
            "kyb_status": "pending_beneficial_owner_verification" if kyb_broken else "complete",
            "kyc_status": "pending" if kyb_broken else "complete",
            "beneficial_owner_status": "pending" if kyb_broken else "verified",
            "source_of_funds": None if kyb_broken else self.rng.choice(
                ["trading_income_declared", "investor_funding_declared", "retained_earnings_declared"]),
            "open_case_count": self.rng.randint(0, 2),
        }

    def _tx_summary(self, ref: str, cats: set[str]) -> dict:
        prog = self.d["programmes"][self.cust_programme[ref]]
        base = self.rng.randint(80, 200)
        velocity = "unusual_transaction_velocity" in cats
        misuse = "possible_account_misuse" in cats
        card = "possible_card_misuse" in cats
        geo = "high_risk_geography_exposure" in cats
        mcc = "high_risk_merchant_category" in cats
        fails = "repeated_failed_transaction_or_topup_pattern" in cats

        count = base * self.rng.randint(7, 13) if velocity else (
            base * self.rng.randint(2, 3) if misuse else self.rng.randint(int(base * 0.4), int(base * 1.2)))
        monitored = self.rng.choice(prog["monitored_merchant_categories"])
        return {
            "customer_ref": ref,
            "transaction_count": count,
            "customer_transaction_baseline": base,
            "declined_transaction_count": self.rng.randint(40, 90) if card else self.rng.randint(0, 6),
            "failed_transaction_count": self.rng.randint(25, 70) if fails else self.rng.randint(0, 5),
            "distinct_merchant_category_count": self.rng.randint(6, 11) if (card or misuse) else self.rng.randint(1, 4),
            "top_merchant_category": monitored if mcc else self.rng.choice(MCCS),
            "monitored_mcc_transaction_count": int(count * self.rng.uniform(0.55, 0.85)) if mcc else int(count * self.rng.uniform(0.0, 0.15)),
            "distinct_country_count": self.rng.randint(5, 12) if geo else self.rng.randint(1, 3),
            "top_country_exposure": self.rng.choice(prog["high_risk_jurisdictions"]) if geo else self.rng.choice(["EE", "LV", "FR", "DE"]),
            "high_risk_country_transaction_count": int(count * self.rng.uniform(0.35, 0.7)) if geo else 0,
            "card_present_count": self.rng.randint(0, 8) if card else self.rng.randint(5, 120),
            "card_not_present_count": count - (self.rng.randint(0, 8) if card else self.rng.randint(5, 120)),
        }

    def _transactions(self, ref: str, cats: set[str]) -> list[dict]:
        prog_ref = self.cust_programme[ref]
        prog = self.d["programmes"][prog_ref]
        geo = "high_risk_geography_exposure" in cats
        mcc = "high_risk_merchant_category" in cats
        card = "possible_card_misuse" in cats
        rows = []
        for i in range(self.rng.randint(6, 12)):
            rows.append({
                "transaction_id": f"TXN-{ref.split('-')[1]}-{i:03d}",
                "customer_ref": ref,
                "programme_ref": prog_ref,
                "timestamp": self.ts(self.rng.uniform(1, 160)),
                "transaction_type": "purchase",
                "amount": round(self.rng.uniform(40, 2400), 2),
                "currency": "EUR",
                "merchant_category": self.rng.choice(prog["monitored_merchant_categories"]) if mcc else self.rng.choice(MCCS),
                "counterparty_ref": f"CP-{self.rng.randint(1000, 9999)}",
                "country": self.rng.choice(prog["high_risk_jurisdictions"]) if geo else "EE",
                "channel": "card_not_present" if card else self.rng.choice(["card_present", "card_not_present"]),
                "transaction_status": "declined" if (card and self.rng.random() < 0.6) else "settled",
            })
        return rows

    def _topup_summary(self, ref: str, cats: set[str], impacted: bool) -> dict:
        base = self.rng.randint(5, 14)
        heavy = "unusual_topup_pattern" in cats
        fails = "repeated_failed_transaction_or_topup_pattern" in cats
        count = base * self.rng.randint(3, 6) if heavy else self.rng.randint(1, base)
        delayed = self.rng.randint(1, 4) if impacted else 0
        return {
            "customer_ref": ref,
            "topup_count": count,
            "customer_topup_baseline": base,
            "failed_topup_count": self.rng.randint(8, 25) if fails else self.rng.randint(0, 2),
            "delayed_topup_count": delayed,
            "distinct_funding_source_count": self.rng.randint(4, 9) if heavy else self.rng.randint(1, 3),
            "customer_impact": impacted,
            "customer_impact_type": "topup_delayed" if impacted else "none",
        }

    def _topups(self, ref: str, impacted: bool) -> list[dict]:
        rows = []
        for i in range(self.rng.randint(4, 9)):
            delayed = impacted and i < 2
            rows.append({
                "topup_id": f"TUP-{ref.split('-')[1]}-{i:03d}",
                "customer_ref": ref,
                "programme_ref": self.cust_programme[ref],
                "timestamp": self.ts(self.rng.uniform(1, 90)),
                "amount": round(self.rng.uniform(500, 60000), 2),
                "currency": "EUR",
                "funding_source_ref": f"FS-{self.rng.randint(100, 999)}",
                "topup_status": "pending_review" if delayed else "accepted",
                "delay_reason": "monitoring_review_in_progress" if delayed else None,
            })
        return rows

    def _screening(self, ref: str) -> dict:
        is_pep = self.rng.random() < 0.4
        return {
            "screening_ref": f"SCR-{ref.split('-')[1]}",
            "customer_ref": ref,
            "screening_type": "pep" if is_pep else "sanctions",
            "match_status": self.rng.choice(["possible_match", "review_required"]),
            "match_confidence_band": self.rng.choice(["low", "medium", "high"]),
            "list_type": "pep_domestic" if is_pep else "eu_consolidated",
            "review_required": True,
            "last_screened_at": self.ts(self.rng.uniform(2, 120)),
            "previous_screening_review_count": self.rng.randint(0, 3),
        }

    def _kyb_history(self, ref: str) -> dict:
        return {
            "customer_ref": ref,
            "failed_kyb_check_count": self.rng.randint(2, 5),
            "last_kyb_failure_reason": self.rng.choice([
                "beneficial_owner_document_not_supplied",
                "corporate_structure_unclear",
                "identity_document_expired"]),
            "outstanding_documents": self.rng.choice([
                ["ubo_declaration"], ["ubo_declaration", "corporate_structure_chart"],
                ["certificate_of_incorporation"]]),
            "beneficial_owner_verification_attempts": self.rng.randint(1, 4),
            "corporate_structure_available": self.rng.choice([True, False]),
        }

    def _counterparty(self, ref: str, shared_key: str | None) -> dict:
        return {
            "customer_ref": ref,
            "distinct_counterparty_count": self.rng.randint(3, 14),
            "top_counterparty_ref": shared_key or f"CP-{self.rng.randint(1000, 9999)}",
            "top_counterparty_share": round(self.rng.uniform(0.45, 0.82), 2),
            "new_counterparty_count": self.rng.randint(1, 6),
            "shared_counterparty_refs": [shared_key] if shared_key else [],
            "counterparty_jurisdictions": self.rng.sample(["EE", "LV", "CY", "AE", "DE", "PL"], 3),
        }

    def _prior_alerts(self, ref: str) -> dict:
        n = self.rng.randint(0, 4)
        return {
            "customer_ref": ref,
            "prior_alert_count": n,
            "prior_alert_refs": [f"ALRT-H{i:03d}" for i in range(n)],
            "prior_alert_categories": self.rng.sample(list(RULE_BY_CATEGORY), min(n, 2)) if n else [],
            "last_prior_alert_at": self.ts(self.rng.uniform(200, 2000)) if n else None,
            "prior_outcome_summary": self.rng.choice(
                ["no_action_required", "information_requested", "escalated_to_mlro"]) if n else "none",
        }

    def build_evidence(self) -> None:
        shared_keys: dict[str, str] = {}
        for a in self.alerts:
            if a["shared_match_key"]:
                shared_keys[a["customer_ref"]] = a["shared_match_key"]

        impacted_customers = {
            a["customer_ref"] for a in self.alerts
            if a["_category_intent"] == "unusual_topup_pattern" and self.rng.random() < 0.6
        }

        for ref, cats in self.cust_cat.items():
            tables: set[str] = set()
            for c in cats:
                tables |= set(TABLES_BY_CATEGORY[c])

            self.d["customers"][ref] = self._customer(ref, cats)
            if "transaction_summaries" in tables:
                self.d["transaction_summaries"][ref] = self._tx_summary(ref, cats)
            if "transactions" in tables:
                self.d["transactions"][ref] = self._transactions(ref, cats)
            if "topup_summaries" in tables:
                self.d["topup_summaries"][ref] = self._topup_summary(ref, cats, ref in impacted_customers)
            if "topups" in tables:
                self.d["topups"][ref] = self._topups(ref, ref in impacted_customers)
            if "screenings" in tables:
                self.d["screenings"][ref] = self._screening(ref)
            if "kyb_history" in tables:
                self.d["kyb_history"][ref] = self._kyb_history(ref)
            if "counterparty_summaries" in tables:
                self.d["counterparty_summaries"][ref] = self._counterparty(ref, shared_keys.get(ref))
            if "prior_alerts" in tables:
                self.d["prior_alerts"][ref] = self._prior_alerts(ref)

    def apply_gaps(self, n_gaps: int = 14) -> list[dict]:
        """
        Evidence gaps, only on customers whose alerts are all unclustered.
        A customer appearing in any cluster is excluded, so cluster
        demonstrations stay fully evidenced.
        """
        # Recompute clusters from configuration and exclude every customer who
        # appears in one, so cluster demonstrations stay fully evidenced even
        # if a cluster forms differently from the seeded intent.
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent))
        from backend.clustering import ClusterEngine as _CE
        _found, _claimed = _CE(Path(__file__).parent.parent / "UC3_Monitoring_KB").detect(self.alerts)
        self.actual_clusters = _found
        clustered_customers = {
            a["customer_ref"] for a in self.alerts if a["alert_id"] in _claimed
        }
        eligible = sorted(set(self.cust_cat) - clustered_customers)
        self.rng.shuffle(eligible)

        applied = []
        for ref in eligible:
            if len(applied) >= n_gaps:
                break
            for table, fld in self.rng.sample(GAP_KINDS, len(GAP_KINDS)):
                rec = self.d.get(table, {}).get(ref)
                if isinstance(rec, dict) and rec.get(fld) not in (None, [], 0):
                    rec[fld] = None if not isinstance(rec[fld], list) else []
                    applied.append({"customer_ref": ref, "table": table, "field": fld})
                    break
        return applied

    # ----------------------------------------------------------------- run
    def run(self) -> dict:
        self.build_programmes()
        self.build_cluster_sets()
        self.build_singletons()
        self.build_evidence()
        gaps = self.apply_gaps()

        self.alerts.sort(key=lambda a: a["created_at"])
        out = {
            "_meta": {
                "seed": SEED,
                "generated_backwards_from": "alert category -> rule_id -> supporting evidence",
                "display_allowlist": DISPLAY_ALLOWLIST,
                "risk_rating_generated": False,
                "priority_generated": False,
                "alert_count": len(self.alerts),
                "customer_count": len(self.cust_cat),
                "programme_count": len(self.d["programmes"]),
                "intended_cluster_members": len(self.clustered_alert_ids),
                "intended_near_miss": len(self.near_miss_alert_ids),
                "evidence_gaps_applied": gaps,
            },
            "alerts": self.alerts,
            **self.d,
        }
        return out


def main() -> None:
    gen = Generator()
    ds = gen.run()
    OUT.mkdir(exist_ok=True)
    (OUT / "dataset.json").write_text(json.dumps(ds, indent=1), encoding="utf-8")

    m = ds["_meta"]
    print(f"alerts        {m['alert_count']}")
    print(f"customers     {m['customer_count']}")
    print(f"programmes    {m['programme_count']}")
    print(f"cluster seed  {m['intended_cluster_members']} members / {m['intended_near_miss']} near-miss")
    print(f"gaps applied  {len(m['evidence_gaps_applied'])}")
    for t in ["customers", "transaction_summaries", "transactions", "topup_summaries",
              "topups", "screenings", "kyb_history", "counterparty_summaries", "prior_alerts"]:
        v = ds[t]
        n = sum(len(x) for x in v.values()) if t in ("transactions", "topups") else len(v)
        print(f"  {t:<24} {n}")
    print(f"\nwritten to {OUT / 'dataset.json'}")


if __name__ == "__main__":
    main()
