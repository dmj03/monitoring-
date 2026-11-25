# Project structure

Every path below exists in the shipped ZIP. Nothing is aspirational.

```
UC3_Monitoring_POC/
|
+-- backend/                     Python application. No web framework below api/.
|   +-- __init__.py              re-exports the project paths
|   +-- paths.py                 PROJECT_ROOT, KB_DIR, DATASET_PATH - all relative
|   +-- api/                     the HTTP layer, a thin wrapper over the engines
|   |   +-- main.py              FastAPI app, lifespan, error handling
|   |   +-- routes.py            the 7 endpoints
|   |   +-- schemas.py           Pydantic request/response models
|   +-- tools/
|   |   +-- datastore.py         loads the dataset; 10 read tools + add_alert()
|   +-- investigation/
|   |   +-- engine.py            routes an alert to its playbook, builds the evidence pack
|   +-- clustering/
|   |   +-- engine.py            groups alerts on alert-level fields only
|   +-- tests/
|       +-- unit/                4 suites - datastore, dedup, windows, read-only
|       +-- integration/         2 suites - end to end, cluster coverage
|       +-- api/                 3 suites - API, real uvicorn server, persistence
|       +-- fixtures/            2 small datasets used only by the integration tests
|
+-- frontend/                    React 18 + Vite 5 + TypeScript analyst console
|   +-- src/
|   |   +-- api/                 one HTTP client; components never call fetch()
|   |   +-- components/          app shell, status badges, Add alert modal
|   |   +-- pages/               Alerts (landing), AlertDetail, Clusters
|   |   +-- test/                component tests with mocked API responses
|   +-- vite.config.ts           dev server + /api proxy to FastAPI
|   +-- package.json             pinned via package-lock.json
|   +-- .env.example             API base URL and proxy target
|
+-- data/
|   +-- dataset.json             source of truth for alerts and mock operational data
|   +-- routing.json             analyst routing decisions (created on first use; gitignored)
|   +-- ai_cache.json            accepted AI summaries (created on first use; gitignored)
|
+-- UC3_Monitoring_KB/           the canonical knowledge base - procedure, not data
|   +-- manifest.yaml            KB identity and version
|   +-- playbooks.yaml           10 investigation playbooks
|   +-- tools.yaml               contracts for the 10 data-access tools
|   +-- config.yaml              rule routing, cluster thresholds, outcome enum
|   +-- clusters/                business interpretation for the 4 cluster types
|   +-- derived_schema.json      generated field classification
|   +-- build_schema.py          the only writer of derived_schema.json
|   +-- validate_kb.py           read-only KB validator
|
+-- scripts/                     operational tooling, not part of the running app
|   +-- validate_dataset.py      generator invariants over the dataset
|   +-- strict_validation.py     compares evidence against the raw dataset
|   +-- verify_canonical_kb.py   34 KB conformance checks
|   +-- audit.py                 locked-decision conformance
|   +-- schema_audit.py          brief section 8 vs implementation
|   +-- generate_bulk.py         regenerates the dataset - WRITES, do not run casually
|
+-- requirements.txt             pinned Python dependencies
+-- README.md                    start here
+-- RUNNING.md                   how to run it on Windows
+-- API_REFERENCE.md             every endpoint
+-- TESTING.md                   how to run the suites, with verified counts
+-- KNOWN_LIMITATIONS.md         what is deliberately not implemented
+-- PROJECT_STRUCTURE.md         this file
```

## What each layer is responsible for

**`UC3_Monitoring_KB/`** holds procedure, never customer facts. The ten alert
categories, their required evidence, which tool supplies each field, the cluster
thresholds and the permitted outcomes all live in YAML. No Python file contains
a category name. Adding an eleventh category would be a KB edit, not a code
change.

**`backend/tools/datastore.py`** is the only code that touches the dataset file.
Ten read methods return a record or `{}`; an absent record becomes MISSING
evidence rather than an error. One write method, `add_alert()`, appends
atomically.

**`backend/investigation/engine.py`** looks up the playbook for an alert's
`rule_id`, plans the tool calls, deduplicates identical ones, executes them, and
assembles an evidence pack. It contains no model call.

**`backend/clustering/engine.py`** groups alerts using only fields present on the
alert itself, so clustering never waits for enrichment. Membership is recomputed
on every call.

**`backend/api/`** translates HTTP to those engines and back. It holds no
business logic. The engines import and run with FastAPI never loaded.

**`frontend/`** calls the API and renders the result. It has no mock data, no
local persistence, and no copy of the knowledge base.

## Shipped dataset

```
data/dataset.json
md5  e5d346635468dee6ece25c262685d358
90 alerts, 81 customers, 4 programmes
```

`POST /alerts` appends to this file, so the checksum changes as soon as you add
an alert through the UI. That is expected.

## Knowledge base version

```
uc3-monitoring-alert-triage-kb  1.0.1-phase1
playbooks  kb-playbooks-1.0.1-phase1
tools      kb-tools-0.1.0
config     kb-config-1.0.0-phase1
```
