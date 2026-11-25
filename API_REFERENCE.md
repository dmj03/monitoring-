# API reference

Twelve endpoints. Every field below was read from the running application's
OpenAPI schema, not written from memory. The live, interactive version is at
<http://127.0.0.1:8000/docs> while the backend is running.

Base URL: `http://127.0.0.1:8000`
From the frontend: `/api/...`, rewritten by the Vite proxy.

Nine endpoints are read-only. Three write, to two stores:

- `POST /alerts` appends an alert to `data/dataset.json`.
- `PUT /alerts/{alert_id}/routing` and `PUT /clusters/routing` record the
  analyst's routing decisions in `data/routing.json`. Neither touches `dataset.json`.

`POST /investigate/summary` and `POST /clusters/summary` are the only endpoints
that call a language model. No endpoint writes the knowledge base.

---

## GET /health

Liveness, plus the identity of the loaded knowledge base. Runs no investigation.

**Response 200**

```json
{
  "status": "ok",
  "kb_version": "kb-playbooks-1.0.1-phase1",
  "playbooks": 10,
  "cluster_types": 4
}
```

The frontend polls this every 15 seconds for the header indicator.

---

## GET /alerts

List alerts held in the dataset.

**Query parameters** — all optional, all exact-match:

| Name | Type | Default | Notes |
|---|---|---|---|
| `rule_id` | string | — | e.g. `TM-VEL-014` |
| `programme_ref` | string | — | e.g. `P001` |
| `customer_ref` | string | — | e.g. `CUST-0101` |
| `limit` | int | 50 | 1–500 |
| `offset` | int | 0 | ≥ 0 |

There is no free-text search parameter.

**Response 200**

```json
{
  "alerts": [
    {
      "alert_id": "ALRT-0001",
      "rule_id": "TM-VEL-014",
      "customer_ref": "CUST-0101",
      "programme_ref": "P001",
      "created_at": "2026-09-22T06:00:00Z",
      "shared_match_key": null,
      "cluster_ref": null
    }
  ],
  "total": 90,
  "limit": 50,
  "offset": 0
}
```

`total` is the count of matching alerts before paging.

The dataset also stores `_tag` and `_category_intent` on generated alerts. These
are generator metadata and are deliberately not exposed.

**422** — `limit` or `offset` out of range.
A filter that matches nothing returns **200** with an empty list, not 404.

---

## GET /alerts/{alert_id}

Fetch one alert by its stable dataset identifier.

**Response 200** — a single alert object, same shape as above.

**404**

```json
{"detail": "no alert with alert_id 'NOPE'"}
```

---

## GET /alerts/form-options

The choices the Add Alert form offers. Read-only. Rules come from the knowledge
base routing table (the one investigation uses), with each playbook's category;
customers and programmes come from the dataset.

```json
{
  "rules": [{"rule_id": "TM-VEL-014", "playbook_id": "unusual_transaction_velocity",
             "category": "unusual_transaction_velocity"}],
  "customers": [{"customer_ref": "CUST-0101", "programme_ref": "P001",
                 "name": "Synthetic Business 0101 OU"}],
  "programmes": [{"programme_ref": "P001", "name": "Programme Alpha (media buying, white label)"}],
  "next_alert_id": "ALRT-0091",
  "alert_ids": ["ALRT-0001", "..."]
}
```

`next_alert_id` is one above the highest `ALRT-<number>` stored. It is a
suggestion only; `POST /alerts` still decides uniqueness. The programme-level
pattern is never listed: it has no upstream rule ID.

---

## POST /alerts

Persist a new alert. **The only endpoint that writes the dataset** (routing
decisions go to `data/routing.json`).

Not to be confused with `POST /investigate`, which runs an investigation over an
alert supplied in the request and stores nothing.

**Request body** — extra fields are rejected, so generator metadata cannot be
injected:

| Field | Type | Required |
|---|---|---|
| `alert_id` | string | yes, non-empty, must be unique |
| `rule_id` | string | yes — must be a rule the knowledge base routes |
| `customer_ref` | string | yes — must be a customer in the dataset |
| `programme_ref` | string | no — taken from the customer record; if sent, must match it |
| `created_at` | string | yes — clustering needs it. ISO-8601 with a UTC offset, e.g. `2026-10-03T09:00:00Z` or `2026-10-03T15:30:00+01:00`; stored exactly as submitted. The UI always sends UTC (`Z`) |
| `shared_match_key` | string or null | no — upstream correlation key, used only by the shared-counterparty cluster. Not offered in the UI |
| `cluster_ref` | null | no — any other value is 422. Cluster membership is calculated by the cluster engine |

```json
{
  "alert_id": "ALRT-0091",
  "rule_id": "TM-VEL-014",
  "customer_ref": "CUST-0101",
  "created_at": "2026-10-03T09:00:00Z"
}
```

**201** — the stored alert, with `programme_ref` derived from the customer and
`shared_match_key` / `cluster_ref` null unless supplied.

**409** — duplicate `alert_id`. Checked first, so it is returned even if other
fields are also wrong. The existing alert is never overwritten.

```json
{"detail": "alert_id 'ALRT-0091' already exists; alert_id must be unique and existing alerts are never overwritten"}
```

**422** — missing or malformed fields, or an unknown field. This includes:

- a `created_at` that is not an ISO-8601 timestamp (`"yesterday"`,
  `"2026-13-01T00:00:00Z"`) or has no UTC offset (`"2026-09-22T17:00:00"`). Such a
  value would break cluster detection for the whole dataset once stored;
- a `rule_id` the knowledge base does not route (it could never be investigated);
- a `customer_ref` not in the dataset;
- a `programme_ref` that is not the customer's programme;
- a non-null `cluster_ref`.

All are in FastAPI's field-error shape, every problem reported at once, and
nothing is written:

```json
{"detail": [{"loc": ["body", "programme_ref"], "type": "value_error",
             "msg": "Programme 'P004' does not match customer 'CUST-0101', which belongs to 'P001'."}]}
```

**500** — the atomic write failed. The dataset file is left intact. No
filesystem path is returned.

What happens on success: the dataset file is re-read, the `alert_id` checked
against it, the alert appended, the whole file written to a temp file in the same
directory, fsynced, and swapped in with `os.replace`. The in-memory store then
reloads from what was written.

---

## POST /investigate

Run the deterministic investigation for one alert. Stores nothing.

**Request body**

```json
{"alert": { ...an alert object... }}
```

The alert needs `alert_id`, `rule_id`, `customer_ref` and `programme_ref`.
Extra keys are tolerated here, unlike `POST /alerts`.

**Response 200**

```json
{
  "alert_id": "ALRT-0001",
  "rule_id": "TM-VEL-014",
  "category": "unusual_transaction_velocity",
  "playbook_id": "unusual_transaction_velocity",
  "cluster_ref": null,
  "evidence": [
    {"key": "kyb_status", "status": "FOUND", "value": "complete",
     "tool": "get_customer", "field": "kyb_status"},
    {"key": "source_of_funds", "status": "MISSING", "value": null,
     "tool": "get_customer", "field": "source_of_funds"}
  ],
  "missing_evidence": ["source_of_funds"],
  "tool_calls": [{"tool": "get_customer", "args": {"customer_ref": "CUST-0101"}}],
  "investigation_focus": "...",
  "checklist": ["..."],
  "escalation_guidance": "...",
  "ai_boundaries": "...",
  "allowed_outcomes": ["no_action_required", "information_requested",
                       "escalate_to_mlro",
                       "insufficient_evidence_manual_investigation_required"]
}
```

Points that matter when consuming this:

- `status` is the literal string `FOUND` or `MISSING`. A MISSING item always has
  `value: null`. It means the source record or field was absent — not false, not
  failed.
- An unknown `customer_ref` or `programme_ref` is **not** an error. You get a
  complete pack whose unresolvable items are MISSING.
- `tool_calls` is fewer than `evidence` because items reading different fields
  of the same record share one call.
- `tool_calls` carries no result field. Each call reports which tool ran and with
  which arguments; the values appear in `evidence`.
- `allowed_outcomes` is the playbook's subset of the global enum, not the whole
  enum. A screening alert is never offered a clearing outcome.

**400** — `rule_id` is not routed by the knowledge base.

```json
{"detail": "unknown rule_id 'NOT-A-RULE'; it is not routed by the knowledge base"}
```

**422** — missing required alert fields.

---

## POST /investigate/summary

**Query:** `regenerate=true` bypasses the cache and makes a fresh model call; the
cached summary is replaced only if the new one is accepted. Without it, an accepted
summary cached for this exact input, prompt, schema and model is returned with no
model call (`cached: true`, original `generated_at`), after being re-checked against
the current grounding checks. Unavailable, failed and rejected results are never
cached. The cache is `data/ai_cache.json` (or `UC3_AI_CACHE_PATH`).

AI analyst summary: an AI-generated explanation of one investigation's evidence.
The only endpoint that calls a language model (OpenAI; model from `OPENAI_MODEL`).

**Request body** — identical to `POST /investigate`: `{"alert": {...}}`.

What happens:

1. The same deterministic investigation as `POST /investigate` runs server-side.
   The client cannot supply evidence.
2. The model is sent the evidence pack only: alert id and rule, playbook id,
   category, investigation focus and AI boundaries, the KB's `model_must_not`
   rules, and every evidence item with its status, value, tool, field and args.
   It is not sent allowed outcomes, escalation guidance, the checklist,
   `created_at` or raw dataset records.
3. The answer must match a strict JSON schema whose citable keys are the pack's
   own keys, and is then checked: every statement cites existing evidence;
   findings cite only FOUND items; missing information cites only MISSING items
   and covers all of them; a MISSING item is described as not available, never
   as false/failed/negative/none/zero; no number that is not in the evidence; no
   time period; no decision, recommendation, outcome, escalation or confidence
   language; analyst attention items are observations, not instructions, and each must be one of three kinds verified against the evidence it cites: a MISSING item, the same text value in two items, or a count beside the item named as its baseline (an empty list is allowed). A
   failing answer is discarded, not shown.

**200** — always, unless the request itself is invalid.

```json
{
  "alert_id": "ALRT-0047",
  "rule_id": "RG-GEO-031",
  "playbook_id": "high_risk_geography_exposure",
  "ai_summary": {
    "status": "generated",
    "label": "AI-generated",
    "generated_by": "llm",
    "model": "gpt-5-mini",
    "summary": "An alert for high risk geography exposure has been raised. The available data shows top country exposure of AE, … Prior alert count is not available in the collected evidence.",
    "summary_evidence_keys": ["country_exposure", "high_risk_country_volume", "…", "prior_alerts"],
    "summary_sentences": [
      {"text": "An alert for high risk geography exposure has been raised.", "evidence_keys": []},
      {"text": "The available data shows top country exposure of AE, …", "evidence_keys": ["country_exposure", "…"]},
      {"text": "Prior alert count is not available in the collected evidence.", "evidence_keys": ["prior_alerts"]}
    ],
    "key_findings": [{"text": "Top country exposure: AE", "evidence_keys": ["country_exposure"]}],
    "missing_information": [{"text": "Prior alert count is not available in the collected evidence.", "evidence_keys": ["prior_alerts"]}],
    "analyst_attention": [{"text": "…", "evidence_keys": ["country_exposure"]}],
    "evidence_basis": [{"key": "country_exposure", "status": "FOUND", "value": "AE",
                        "tool": "get_transaction_summary", "field": "top_country_exposure"}],
    "reason": null
  }
}
```

| `ai_summary.status` | Meaning | Content fields |
|---|---|---|
| `generated` | the model answered and every check passed | filled |
| `unavailable` | `OPENAI_API_KEY` or `OPENAI_MODEL` is not configured | empty; `reason` says which |
| `error` | API failure, timeout, malformed output, or the answer failed a check | empty; `reason` says why |

- `summary` is a short narrative (alert type, observed values, enrichment,
  records and history, missing evidence). The model writes it as
  `summary_sentences`, each citing its evidence and checked on its own;
  `summary` is those sentences joined. `missing_information` is the sentences
  about MISSING items, not a separate model answer.
- `key_findings` are 2–4 single facts for a quick scan, `"<Name>: <value>"`; a
  missing item may appear as `"<Name>: not available"`.
- A sentence is rejected if it states a value not in the evidence (or drops a
  word of one), names an item it does not cite, relates two numbers ("out of",
  ratios, "above", "times" — the backend supplies no comparisons), mentions a
  threshold or a cause, writes a number in words, uses "Name: value" form, or
  mixes a MISSING item into a FOUND sentence. The first sentence must name the
  alert type, and every MISSING item must be named.
- `evidence_basis` is copied from the investigation for every cited key. It is
  not model output.
- There is no outcome, decision, escalation or confidence field.
- The output is not deterministic: two calls can word the summary differently.
  The evidence it cites is deterministic.

**400** — `rule_id` is not routed by the knowledge base. The model is not called.

**422** — missing required alert fields.

`POST /investigate` does not depend on this endpoint. With no key, or with the
model failing, `POST /investigate` is unchanged.

---

## GET /routing

The routing options and every recorded routing decision. Read-only.

**200**

```json
{
  "destinations": [
    {"id": "mlro", "label": "MLRO / Nominated Officer", "description": "AML suspicion requiring specialist judgement"},
    {"id": "sanctions_analyst", "label": "Sanctions Analyst", "description": "Screening/PEP result requiring manual review"},
    {"id": "monitoring_engineering", "label": "Monitoring Engineering", "description": "Monitoring rule/pattern appears mistuned"},
    {"id": "programme_operations", "label": "Programme Operations", "description": "Technical or programme/partner-specific issue"},
    {"id": "kyb_cdd", "label": "KYB / CDD Team", "description": "Incomplete customer/KYB information"}
  ],
  "outcomes": [
    {"id": "no_action_required", "label": "No Further Action", "description": "Close the alert without routing it"}
  ],
  "routing": {
    "ALRT-0047": {"alert_id": "ALRT-0047", "destination": "mlro", "outcome": null, "routed_at": "2026-10-05T10:12:03Z",
                  "source": "alert", "cluster_type": null, "cluster_label": null},
    "ALRT-0027": {"alert_id": "ALRT-0027", "destination": "sanctions_analyst", "outcome": null, "routed_at": "2026-10-05T11:00:00Z",
                  "source": "cluster", "cluster_type": "repeated_screening_review", "cluster_label": "Repeated Screening Review Pattern"}
  },
  "clusters": [
    {"key": "repeated_screening_review:ALRT-0027,ALRT-0028,ALRT-0029,ALRT-0030",
     "cluster_type": "repeated_screening_review", "label": "Repeated Screening Review Pattern",
     "alert_ids": ["ALRT-0027", "ALRT-0028", "ALRT-0029", "ALRT-0030"],
     "destination": "sanctions_analyst", "outcome": null, "routed_at": "2026-10-05T11:00:00Z"}
  ]
}
```

- A destination says where the alert goes. An outcome closes it without routing.
  A record has exactly one of the two.
- The outcome id is the knowledge base's own outcome name (`config.yaml`
  `investigation_outcomes`), so whether a playbook permits it is read from that
  playbook's `allowed_outcomes`, never decided by the client.
- `routing` holds the decision in force for each routed alert: its own decision or
  its cluster's, whichever was made later. `source` says which. An alert absent
  from it is not routed.
- `clusters` holds cluster decisions, each for the exact member list it was made
  for (`key`). A detected cluster whose membership differs from every key is not routed.
- `routed_at` is the server's clock at the moment of the decision. No analyst
  identity is recorded: the POC has no login.

---

## PUT /alerts/{alert_id}/routing

Record the analyst's routing decision for one alert. Writes `data/routing.json`
only. A later call replaces the earlier decision.

**Request body**, exactly one of:

```json
{"destination": "sanctions_analyst"}
```
```json
{"outcome": "no_action_required"}
```

**200**: the stored record, as it appears in `GET /routing`.

**404**: no alert with that `alert_id` in the dataset.

**409**: the outcome is not permitted by the alert's playbook. For example, the
sanctions/PEP and KYB playbooks have no no-action path:

```json
{"detail": "outcome 'no_action_required' is not permitted by playbook 'sanctions_pep_screening_review_required'; its allowed outcomes are ['escalate_to_sanctions_analyst', 'insufficient_evidence_manual_investigation_required']"}
```

**422**: neither or both fields given, or an unknown destination or outcome.

The decision is the analyst's. It does not re-run the investigation, does not call
the AI, and is not visible to the AI summary.

---

## PUT /clusters/routing

Route a cluster the engine detects right now, identified by its exact member list
(`cluster_ref` is only a per-run reference). Writes `data/routing.json` only.

**Request body**: `alert_ids` plus exactly one of `destination` or `outcome`.

```json
{"alert_ids": ["ALRT-0027", "ALRT-0028", "ALRT-0029", "ALRT-0030"], "destination": "sanctions_analyst"}
```

**200**: the stored cluster decision, as it appears in `GET /routing` `clusters`.

**409**: the alerts are not a currently detected cluster, or the outcome is not
permitted by every member's playbook `allowed_outcomes`.

**422**: neither or both of destination and outcome, an unknown value, an empty
member list, or an extra field.

The decision becomes the routing in force for every current member and takes
precedence over their earlier individual decisions, which are kept. Routing a member
individually afterwards takes precedence for that alert only. Nothing is re-run and
no AI is called.

---

## POST /clusters/summary

**Query:** `regenerate=true` bypasses the cache and makes a fresh model call; the
cached summary is replaced only if the new one is accepted. Without it, an accepted
summary cached for this exact input, prompt, schema and model is returned with no
model call (`cached: true`, original `generated_at`), after being re-checked against
the current grounding checks. Unavailable, failed and rejected results are never
cached. The cache is `data/ai_cache.json` (or `UC3_AI_CACHE_PATH`).

AI explanation of why the deterministic cluster engine grouped a currently detected
cluster. Body: `{"alert_ids": [...]}`, the cluster's exact members.

The model receives only the deterministic cluster result (type, label, category,
rules, programmes, alert and customer counts, computed span, members), each
configured criterion with its threshold and actual value, the cluster type's
grouping rule, the member alert records, and the KB cluster `purpose` and
`boundaries`. Not sent: `analyst_guidance` (it contains routing advice),
`interpretation_guidance`, and any evidence.

**200**: always, unless the request is invalid.

```json
{"cluster_type": "repeated_screening_review", "label": "Repeated Screening Review Pattern",
 "alert_ids": ["ALRT-0027", "ALRT-0028", "ALRT-0029", "ALRT-0030"],
 "ai_summary": {"status": "generated", "label": "AI-generated", "generated_by": "llm", "model": "gpt-5-mini",
                "summary": "…", "summary_fields": ["cluster.alert_count"],
                "criteria_met": [{"text": "Alerts: 4 against a minimum of 3 (met).", "fields": ["criteria.min_alerts"]}],
                "characteristics": [{"text": "Programme: P003.", "fields": ["cluster.programme_refs"]}],
                "reason": null}}
```

`status` is `generated`, `unavailable` (no key or model configured) or `error`
(API failure or an answer that failed the checks). The answer is rejected if a
statement cites no or unknown fields, states a number or duration not in the cluster
data, names a destination or outcome, uses routing, decision, suspicion, judgement
or instruction language, or claims a screening match. There is no routing, outcome,
decision or confidence field.

**409**: the alerts are not a currently detected cluster. The model is not called.

---

## GET /clusters

Clusters detected over the whole dataset at the configured thresholds.

**Response 200**

```json
{
  "clusters": [
    {
      "cluster_ref": "C001",
      "cluster_type": "repeated_velocity",
      "label": "Repeated Velocity Pattern",
      "cluster_category": "programme_level_pattern",
      "alert_ids": ["ALRT-0003", "..."],
      "customer_refs": ["CUST-0101", "..."],
      "programme_refs": ["P001"],
      "rule_ids": ["TM-VEL-014"],
      "time_span_hours": 34.0,
      "thresholds": {"min_alerts": 5, "min_customers": 3, "time_window_hours": 48}
    }
  ],
  "membership": {"ALRT-0003": "C001"},
  "alerts_considered": 90,
  "alerts_clustered": 20,
  "note": "cluster_ref is assigned per detection run and is not a durable identifier; it is stable only for identical input"
}
```

Clusters are computed per request. Nothing about them is stored in the dataset.

There is no `GET /clusters/{cluster_id}`, deliberately: `cluster_ref` is assigned
sequentially per detection run, so the same string denotes different clusters for
different inputs. Cluster detail is rendered from this one response.

---

## POST /cluster

Run cluster detection over a supplied set of alerts, with optional per-request
threshold overrides. Useful for exploring sensitivity without touching the KB.

**Request body**

```json
{
  "alerts": [ ...alert objects, at least one... ],
  "overrides": {"repeated_velocity": {"min_alerts": 7}}
}
```

Each alert needs `created_at`, since the time window depends on it.

**Response 200** — same shape as `GET /clusters`, with `alerts_submitted` instead
of `alerts_considered` and no `note`.

**400** — an alert is missing `created_at`, or `overrides` names a cluster type
that is not configured.

**422** — empty `alerts` list.

Overrides apply to that request only. The knowledge base is never modified.

---

## Errors

| Status | Meaning |
|---|---|
| 400 | valid JSON, but the request cannot be served — unroutable rule, unknown cluster type, missing `created_at` |
| 404 | the alert does not exist |
| 409 | duplicate `alert_id` |
| 422 | schema validation failed |
| 500 | unexpected server error |

Every error body is `{"detail": "..."}`, except 422 where `detail` is a list of
field errors. No response ever contains a traceback or a filesystem path;
unexpected exceptions are logged server-side and returned as
`{"detail": "internal server error"}`.
