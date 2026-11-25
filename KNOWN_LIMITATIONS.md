# UC3 Monitoring POC — Known Limitations (Phase 1)

Things that are deliberately not implemented, or that work differently from how
a reader of the contracts would expect. Recorded so nobody discovers them by
surprise during integration.

This file sits outside `UC3_Monitoring_KB/` on purpose: the canonical KB and its
ZIP are unchanged by it. If a later phase wants these recorded inside the KB,
that is a `manifest.yaml` edit plus a version bump and a re-package.

Status at time of writing: 34/34 canonical KB checks, 29/29 e2e, 19/19 cluster
coverage, 24/24 dataset validation, strict validation 0/0. None of the
limitations below causes a failure.

---

## L1 — Time-window and lookback parameters are advisory

**Affects:** `get_transaction_summary`, `get_transactions`, `get_topup_summary`,
`get_topups`, `get_counterparty_summary` (`window_hours`); `get_prior_alerts`
(`lookback_days`).

Six tool parameters are accepted, participate in the deduplication key, and are
echoed into the returned record — but none filters anything. The same stored
summary is served whether the playbook asks for 24, 48, 72 or 168 hours.

`tools.yaml` describes two of these tools as returning activity "over a window"
and `get_prior_alerts` as covering a lookback period. In Phase 1 that wording
describes intent, not behaviour.

**Why it is not a defect today:** the dataset holds one precomputed summary per
customer, generated to suit its playbook's intended window, and no customer in
the 90-alert dataset has alerts in two categories that request the same summary
at different windows. Every served value is appropriate for the alert it serves.

**Why it matters later:** connect a real data source behind these tools and every
window silently returns everything. The parameter must become functional, or be
removed, before any non-synthetic backing store.

**Not in scope for Phase 1.** No cluster parameter is affected —
`time_window_hours` in `config.yaml` is genuinely functional and is proven by the
cluster formation and dissolution tests.

---

## L2 — Summaries do not declare the period they cover

**Affects:** `transaction_summaries`, `topup_summaries`, `counterparty_summaries`.

The scope brief's canonical `TransactionPatternSummary` (§8.3) declares
`period_start` and `period_end`. Neither is implemented: not in the generator,
not in the dataset, not in the tool contracts, not requested by any playbook.

**Consequence:** an evidence pack states `transaction_activity: 2352` with no
period attached. The analyst and the model cannot tell from the pack whether that
count covers a day or a week, and `window_hours` is not visible in the model
input either. For a velocity investigation this is a real ambiguity, not a
cosmetic one.

**Decision taken:** leave the fields out for Phase 1. The evidence summary does
not need to showcase them now, and restoring them would require regenerating the
dataset, which is deliberately frozen at
`e5d346635468dee6ece25c262685d358`.

**Revisit when:** moving toward production, or whenever the evidence pack needs
to be fully self-describing. The restoration is descriptive, not functional — the
summary would assert the period it represents rather than compute it, which works
with precomputed data and needs no alert timestamp.

---

## L3 — A genuine prior-alert lookback cannot be computed

**Affects:** `get_prior_alerts`.

`lookback_days` takes values of 30, 90 and 180 across the playbooks, but the
prior-alert record holds no per-alert dates — only a single aggregate
`last_prior_alert_at`. The three values are therefore indistinguishable.

The current alert can never be counted in its own prior list: prior references
use an `ALRT-H*` namespace with zero overlap against the 90 live `ALRT-NNNN`
identifiers. Self-exclusion is structural rather than enforced, which is safe but
is not the same as being implemented.

---

## L4 — Stored event rows are a sample, not the population

**Affects:** `get_transactions`, `get_topups`.

Individual transaction and top-up rows carry real timestamps, so filtering is
mechanically possible. It is not semantically possible: a customer whose summary
reports 868 transactions has roughly 9 stored rows. The rows exist for UI
drill-down. Computing any aggregate from them would produce a number that looks
authoritative and is not.

Consistent with this, the playbooks request only `transaction_id` and `topup_id`
from these tools — the rest of each row is drill-down payload, classified as `ui`
in the schema.

---

## L5 — Fourteen declared tool fields are never populated

**Affects:** `get_customer` (`risk_rating`, `previous_alert_count`,
`last_review_date`), `get_programme` (`partner_type`, `primary_jurisdiction`,
`active_customer_count`), `get_transaction_summary` (`total_amount`,
`average_amount`, `unusual_velocity_flag`), `get_topup_summary`
(`topup_total_amount`, `topup_average_amount`), `get_screening`
(`analyst_action`), `get_kyb_history` (`kyb_check_count`, `last_kyb_check_at`).

Declared in `tools.yaml`, absent from the generated data. **None is required by
any playbook**, so nothing fails — every one resolves to a field no evidence item
requests. `risk_rating` is additionally excluded by locked decision and is
correctly absent from the data and from the active schema; it survives only in
the tool declaration.

The tool contract therefore over-promises relative to the dataset. Harmless in
Phase 1; misleading to whoever implements the real data source.

---

## L6 — Open: exclusion labelling conflates two different authorities

`derived_schema.json` records 22 fields under
`excluded_by_locked_decision`. Ten trace to decisions stated explicitly:
`priority`, `risk_rating`, `jurisdiction`, `card_ref_masked`, `risk_band`,
`alert_status`, `alert_age_days` and the three amount-band fields.

The other twelve were derived during implementation and filed under the same
heading: `alert_source`, `alert_type`, `business_ref`, `account_ref`,
`previous_alert_count`, `last_review_date`, `period_start`, `period_end`,
`unusual_velocity_flag`, `new_counterparty_flag`, `historical_baseline_ref`,
`analyst_action`.

Each is individually defensible, but they carry an authority they do not have.
Splitting the block into `locked_by_decision` and `derived_exclusions`, each with
its reason, is a one-file change to `build_schema.py` and changes no behaviour.

**Status: not actioned.** Raised and awaiting a decision.

---

## L7 — AI analyst summary: guarded, not verified sentence by sentence

**Affects:** `POST /investigate/summary`.

The model's answer is checked deterministically: citations must exist and match
FOUND/MISSING status, numbers must appear in the evidence, and wording that
turns MISSING into a conclusion, states a time period, recommends, decides or
instructs is rejected. These checks are pattern-based. They cannot prove that
each sentence is a faithful reading of the evidence it cites; a sentence could
cite the right key and still characterise it loosely. They can also reject a
harmless answer (for example one that says "no" in a neutral sense next to a
MISSING item), which surfaces as status `error`, not as a wrong summary.

The output is non-deterministic. The automated tests use a fake client and
never call OpenAI, so wording quality of the real model is verified manually.

Window and lookback arguments are passed to the model as provenance, but the
model is told — and checked — not to state any period, because of L1/L2.

---

## L8 — Routing is recorded, not delivered

**Affects:** `PUT /alerts/{alert_id}/routing`, the Investigation outcome section.

Routing an alert records the analyst's decision in `data/routing.json` and shows it
on the dashboard. Nothing is sent to an MLRO, sanctions, engineering, programme
operations or KYB/CDD system: there are no integrations in this POC.

Only the current decision per alert, and per cluster membership, is kept. Changing
a decision replaces it, and there is no history. A cluster decision is tied to the
exact members it was made for: if the cluster's membership changes, the new
membership is unrouted while the original members keep the decision made for them. No analyst identity is recorded, because the POC has no login. The
destination list is fixed in the backend schema; it is workflow configuration,
not knowledge-base procedure.

---

## L9 — AI summary cache is never pruned

**Affects:** `data/ai_cache.json`.

Every accepted summary for a distinct input stays in the file. Old entries are never
served for a different input, because the key covers the input, prompt, schema and
model, but they are not removed either. For the POC the file is small. Deleting it is
always safe: the next view regenerates and re-caches.

---

## L10 — Detected times: IST in the UI, UTC with any offset through the API

**Affects:** Add Alert, the alerts list order.

The UI enters and shows detected times in IST (fixed UTC+05:30) and always sends
UTC with a `Z`. `POST /alerts` still accepts any ISO-8601 offset and stores it as
submitted, so an alert created directly through the API with, say, `+01:00`
clusters correctly (the engine parses the offset) but the alerts list, which
orders by the stored text, may place it out of time order. Routing and AI
timestamps are still shown in UTC; only detection times use IST.

The Add Alert form also does not stop a detection time in the future.

---

## Not limitations

Recorded so they are not mistaken for gaps:

- `possible_card_misuse` requires no card identifier. That is the intended design
  under the PCI exclusion, and the playbook states the limitation explicitly.
- Cluster `time_window_hours` is functional.
- MISSING is non-fatal across all ten tools and all 90 alerts, and no MISSING
  evidence item ever carries a value.
- No tool can write. Running investigations leaves the dataset byte-identical.
