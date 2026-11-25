# Testing

Every count below was produced by running the suite immediately before this
project was packaged. Nothing here is claimed without having been executed.

There is no test runner and no `conftest.py` — each suite is a plain script with
its own `main()` that prints a count and exits non-zero on failure. Run them
individually.

Activate the virtual environment first (see `RUNNING.md`). All Python commands
assume the project root as the working directory.

## Backend — unit tests

```powershell
python backend\tests\unit\test_datastore.py
python backend\tests\unit\test_deduplication.py
python backend\tests\unit\test_window_parameters.py
python backend\tests\unit\test_read_only.py
```

| Suite | Result | What it proves |
|---|---|---|
| `test_datastore.py` | **148 passed** | all 10 tools against valid, absent, empty and malformed references; values compared field-by-field against the dataset |
| `test_deduplication.py` | **21 passed** | identical arguments merge into one call; different arguments, customers or programmes never merge |
| `test_window_parameters.py` | **55 passed** | the window and lookback arguments are advisory in Phase 1 — accepted and echoed, but they do not filter |
| `test_read_only.py` | **21 passed** | 180 investigations and 4 clustering runs leave every project file unchanged by hash and modification time |

## Backend — integration tests

```powershell
python backend\tests\integration\test_e2e.py
python backend\tests\integration\test_cluster_coverage.py
```

| Suite | Result | What it proves |
|---|---|---|
| `test_e2e.py` | **29 passed** | routing, evidence resolution, MISSING handling, outcome restriction, cluster formation and dissolution |
| `test_cluster_coverage.py` | **19 passed** | all four cluster types form; all four near-misses correctly fail, each on its intended threshold |

## Backend — API tests

```powershell
python backend\tests\api\test_api.py
python backend\tests\api\test_server.py
python backend\tests\api\test_alert_persistence.py
python backend\tests\api\test_created_at_validation.py
python backend\tests\api\test_ai_summary.py
python backend\tests\api\test_routing.py
python backend\tests\api\test_cluster_routing.py
python backend\tests\api\test_ai_cache.py
python backend\tests\api\test_alert_form.py
python backend\tests\api\test_alert_freeze.py
```

Adding alerts is frozen by default (`UC3_ALERT_CREATION` off). The suites that
add alerts switch it on for themselves; `test_alert_freeze.py` (**12 passed**)
checks the frozen default: `POST /alerts` is 403 before any validation, the
dataset is unchanged, `/health` reports `alert_creation: false`, and switching
it on is honoured.

| Suite | Result | What it proves |
|---|---|---|
| `test_api.py` | **61 passed** | every endpoint in-process; status codes, schema shape, error mapping, and that HTTP output matches the engine called directly |
| `test_server.py` | **43 passed** | starts a real uvicorn process on an ephemeral port and talks to it over a socket; clean shutdown; no file written |
| `test_alert_persistence.py` | **36 passed** | create, duplicate 409, investigate the new alert, survival across a fresh DataStore and a real server restart |
| `test_created_at_validation.py` | **63 passed** | added after packaging: an invalid or offset-less `created_at` is 422 and never written; valid formats still 201 and stored unchanged; duplicate 409 unchanged; `/clusters` keeps working. In-process, temp dataset copy |
| `test_ai_summary.py` | **254 passed** | wording polish (a numeric "<thing> count" finding relabelled "<things>", a value that only repeats its label trimmed) applied before the checks ("1 prior alerts are" → "1 prior alert is", topup → top-up, pep/kyb/kyc → PEP/KYB/KYC, a key finding's repeated label removed) never changes a number, leaves uncertain plurals alone, and never lets an unsafe sentence pass; "top-up" still counts as naming a topup item; the narrative summary: it begins with the alert type; natural sentences are accepted when every claim is grounded (each cited value stated with all its words, a true/false with its full field name, a count of 0 as "no"), and rejected for a "Name: value" list, an unsupported relationship between numbers (out of, of which, accounting for, share of, representing, %, times, above, higher than, exceeds, compared with, a rise, a difference), a threshold, a cause or inference, a number in words, an item named but not cited, a MISSING item mixed into a FOUND sentence or left out, fewer than 2 or more than 4 key findings, a finding of more than one item, or more than 8 sentences; a name inside a longer cited name and a cited value that equals another item's name are not mistaken for mentions; analyst-attention items must be a supported observation (missing item, shared text value, or count beside its named baseline), verified against the cited evidence;  AI analyst summary with a fake OpenAI client (no network, no key): evaluative words, overstated screening matches and unnamed missing items are rejected; evidence wording (a status value equal to an outcome name, a field named `*confidence*`) is not mistaken for a decision; the model input is exactly the evidence pack with provenance and no outcome/decision material; invented evidence keys are rejected in every section; MISSING cannot be stated as false/failed/none/zero/negative; fabricated numbers, time periods, decision, confidence and procedural wording are rejected; missing key, API errors and malformed output give a controlled 200; `POST /investigate` unchanged; no secrets, no hardcoded alert facts, `openai` imported in one place |
| `test_routing.py` | **28 passed** | `PUT /alerts/{id}/routing` and `GET /routing` against a temporary routing file: destinations and the outcome are separate; the outcome is accepted only where the playbook's `allowed_outcomes` permit it (409 otherwise, naming them); invalid input 422, unknown alert 404; re-routing replaces; decisions survive an app restart; `dataset.json`, the alert record and `POST /investigate` are unchanged |
| `test_cluster_routing.py` | **64 passed** | AI cluster summary with a fake OpenAI client: the input is the deterministic cluster result, criteria and KB purpose/boundaries only (no analyst_guidance, no evidence); answers with routing, destination names, outcomes, suspicion, judgement, instructions, screening-match claims, or numbers/durations not in the cluster data are rejected; missing key, API failure and malformed output give a controlled 200. Cluster routing: every current member routed via the cluster; earlier individual decisions kept but superseded; a later individual decision overrides; outcome follows every member's allowed_outcomes; survives restart; a changed membership (temporary dataset copy) is unrouted and the old membership can no longer be routed; a first-version routing file is still read; `dataset.json` and the project routing file unchanged |
| `test_ai_cache.py` | **34 passed** | the suite uses its own temporary cache and leaves the application's persistent `data/ai_cache.json` byte-identical (created, changed or removed would fail); cache of accepted AI summaries, with a counting fake client: a repeated alert or cluster request makes one model call; the cache survives a restart; a changed model, prompt, alert or evidence value makes a new call; `regenerate=true` always calls and replaces only an accepted answer; unavailable, failed and rejected results are never cached; a cached answer that fails the current checks is regenerated; with no key, a cached answer is still served; `dataset.json` and the project routing file are untouched and no cache file is written into the project |
| `test_alert_form.py` | **40 passed** | the controlled Add Alert flow, on a temporary dataset copy: `GET /alerts/form-options` returns exactly the KB routing table (in config order, with each playbook's category, no programme-level pattern), the dataset's customers with their own programme, the programmes with their names, and the next alert ID (ALRT-0091); `POST /alerts` refuses an unknown rule, an unknown customer, a programme that is not the customer's, a non-null `cluster_ref` and an empty ID with 422 naming the field (all problems at once, nothing written); a used ID is 409 and takes precedence; a valid alert without a programme is stored with the customer's programme, its UTC `created_at` unchanged and `cluster_ref` null; `shared_match_key` is still accepted through the API; the new alert is investigable at once with its rule's category, claims no cluster, is seen by clustering and is not routed; `dataset.json`, the project routing file and the KB are unchanged |

`test_server.py` and `test_alert_persistence.py` start a real uvicorn
subprocess. On Windows, allow them through the firewall prompt if one appears —
they bind to `127.0.0.1` only.

**`test_alert_persistence.py` works on a temporary copy of the dataset**, pointed
at by the `UC3_DATASET_PATH` environment variable, and asserts at the end that
the canonical `data/dataset.json` was never touched. It will not modify your
dataset.

## Knowledge base and dataset validation

```powershell
python scripts\verify_canonical_kb.py
python UC3_Monitoring_KB\validate_kb.py
python scripts\validate_dataset.py
python scripts\strict_validation.py
python scripts\audit.py
python scripts\schema_audit.py
```

| Script | Result | What it checks |
|---|---|---|
| `verify_canonical_kb.py` | **34/34 pass**, 0 blockers, 0 warnings | KB conformance: routing, playbook contract, cluster config, excluded fields, manifest |
| `validate_kb.py` | **PASS** | every evidence key resolves to a real tool, field and resolvable arguments; the classified schema is not stale |
| `validate_dataset.py` | **24 passed** | generator invariants: all ten categories present, reference integrity, gap placement |
| `strict_validation.py` | **0 failures, 0 warnings** | compares every FOUND evidence value against the raw dataset — the one independent oracle in the suite |
| `audit.py` | **0 blockers, 0 gaps** | locked decisions: four cluster types, no priority, no upstream rule for programme-level pattern |
| `schema_audit.py` | runs, exit 0 | brief §8 canonical model vs the implementation — a report, not a pass/fail check |

All of these are read-only. `validate_kb.py` validates the classified schema and
no longer regenerates it.

## Do not run casually

```
scripts\generate_bulk.py        rewrites data\dataset.json
UC3_Monitoring_KB\build_schema.py   rewrites derived_schema.json
```

Both are intentional writers. `generate_bulk.py` replaces the dataset entirely,
including any alerts you added through the UI.

## Frontend

```powershell
cd frontend
npm install
npm test
```

| Check | Result |
|---|---|
| `npm test` (vitest) | **53 passed** (Add Alert: the suggested ID from form-options, an edited ID kept even if typed before the suggestion arrives, a cleared ID shown as an error and never regenerated, a used ID flagged as typed, the server's 409 and field messages shown; Rule and Customer chosen only from the form-options lists, with customer search; Programme read-only, derived from the customer and shown as its code only; Detected At defaulting to the current IST date and time and editable; no Shared match key or Cluster reference fields; Detected At entered in IST and sent as UTC (14:30 IST → 09:00Z, 03:00 IST → previous day); detected times shown in IST on the alerts table, alert page and cluster member table; the alert list is fetched in full and shown newest first, with page controls above and below showing page numbers and "Page X of Y"; the Destination filter — All, Not routed, each destination, No Further Action — paginates only the matching alerts, and changing it returns to page 1; search covers every page; Regenerate sends regenerate=true and a normal open does not, for alert and cluster summaries; queue, add alert, clusters, the alert investigation with its AI summary, alert routing with the downward dropdown, and the cluster page: AI cluster summary, Route cluster, the outcome rule across member playbooks, a changed membership shown as not routed, "via cluster" on the alert page and dashboard) |
| `npx tsc --noEmit` | clean |
| `npm run build` | clean, 186 KB JS |

The frontend tests mock `fetch` entirely. They do not need the backend running
and never touch the dataset. They cover: the alert list renders, empty state,
filters produce the right query parameters, every API page is fetched (`offset=500`), network
failure message, Add Alert submits only alert_id, rule_id, customer_ref and a UTC created_at,
successful creation refreshes from the API, duplicate shows the 409 message,
empty required fields block submission, alert detail 404, investigation renders
evidence and tool calls, missing evidence renders as "—" rather than false or
failed, and the cluster list and its empty state.

## Totals at packaging time

```
backend    453 assertions across 9 suites, 0 failures
frontend    13 tests, 0 failures
validation  5 scripts, 0 failures, 0 warnings
```

## What is not tested

No browser was ever opened. The frontend is verified by component tests in jsdom
and by HTTP calls through the Vite dev proxy. Visual rendering, layout and
appearance are unverified — see `KNOWN_LIMITATIONS.md`.
