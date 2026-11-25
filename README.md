# UC3 — Monitoring Alert Triage POC

An analyst console for triaging monitoring alerts, with a deterministic
investigation engine behind it.

The system starts **after** an alert has been generated. It is not a monitoring
or detection engine: alerts arrive already made, and this POC decides what
evidence an analyst needs, gathers it, and shows which alerts form a pattern
together.

## What it does

An alert's `rule_id` selects a playbook from the knowledge base. The playbook
lists the evidence the investigation needs and which tool supplies each field.
The engine plans those tool calls, removes duplicates, runs them against the
dataset, and assembles an evidence pack where every item is either FOUND with a
value or MISSING with none. Separately, alerts are grouped into clusters using
only fields present on the alert itself.

Two properties shape everything:

**The knowledge base holds the procedure, not the code.** The ten alert
categories, their evidence, their tools and their permitted outcomes exist only
in YAML. No Python file contains a category name.

**Missing evidence is a finding, not a failure.** An absent record yields
MISSING, the investigation completes, and the gap becomes part of the analyst's
picture.

## Running it

See **[RUNNING.md](RUNNING.md)** — two terminals, Python and Node, no database.

In short:

```powershell
# terminal 1
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.api:app --host 127.0.0.1 --port 8000

# terminal 2
cd frontend ; npm install ; npm run dev
```

Then open <http://localhost:5173>.

## Documentation

| File | What it covers |
|---|---|
| [RUNNING.md](RUNNING.md) | install, start, the two-terminal workflow, troubleshooting |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | the directory tree and what each layer is responsible for |
| [API_REFERENCE.md](API_REFERENCE.md) | all thirteen endpoints, request and response shapes, status codes |
| [TESTING.md](TESTING.md) | how to run every suite, with the counts verified at packaging |
| [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) | what is deliberately not implemented, and why |

## Architecture

```
React + Vite          frontend/
      |  /api proxy
FastAPI               backend/api/
      |
InvestigationEngine   backend/investigation/
ClusterEngine         backend/clustering/
      |
DataStore             backend/tools/          RoutingStore   backend/routing/
      |                                              |
dataset + KB          data/dataset.json,      routing state  data/routing.json
                      UC3_Monitoring_KB/
```

Dependencies point one way. The engines import and run with FastAPI never
loaded, so the HTTP layer is replaceable.

## Persistence

There are three persistent files, each the single source of truth for its own kind of record:

| Store | Holds | Written only by |
|---|---|---|
| `data/dataset.json` | alerts and the mock operational data the tools read | `POST /alerts` (appends an alert) |
| `data/routing.json` | analyst routing decisions for alerts and for clusters: a destination, or the outcome "no further action" | `PUT /alerts/{alert_id}/routing`, `PUT /clusters/routing` |
| `data/ai_cache.json` | accepted AI summaries (alert and cluster), so a repeat view makes no model call | `POST /investigate/summary`, `POST /clusters/summary` |

The generated alert data stays immutable source data: routing never writes to
`dataset.json`, and the alert record has no routing field. Both stores are written
atomically and survive a restart. Every other endpoint is read-only, and nothing
writes the knowledge base. The AI never routes an alert or a cluster; the analyst does.

A cluster decision is stored for the cluster's exact member list and becomes the
routing in force for every member, ahead of earlier individual decisions, which
are kept. A member routed individually afterwards uses its own decision. If the
cluster's membership changes, it is a new, unrouted cluster.

**Clusters are not persisted.** They are recomputed on every request, so
changing a threshold changes the result immediately and no stale membership can
be read back.

## State at packaging

```
10 alert playbooks          4 cluster types
90 alerts in the dataset    81 customers, 4 programmes
7 API endpoints             6 read, 1 write
                            (since packaging: 13 endpoints - 10 read, 3 write;
                             see Persistence)

453 backend assertions across 9 suites     0 failures
13 frontend tests                           0 failures
5 validation scripts                        0 failures, 0 warnings
```

There are exactly two LLM calls, both in `backend/ai/`, sharing one OpenAI client
and model (`OPENAI_MODEL`):

- `POST /investigate/summary` (`analyst.py`) explains an alert's deterministic
  evidence pack; every statement cites evidence keys.
- `POST /clusters/summary` (`cluster_summary.py`) explains why the deterministic
  cluster engine grouped a cluster's alerts; every statement cites cluster fields.

Both explain; neither investigates, decides membership, recommends or routes.

Accepted summaries are cached in `data/ai_cache.json`, keyed by a fingerprint of the
exact model input, prompt, schema and model, so changed evidence, cluster data,
prompt or model always produces a fresh call. A cached summary is re-checked against
the current grounding checks before it is reused. Unavailable, failed and rejected
results are never cached. "Regenerate AI summary" (`?regenerate=true`) always makes
a fresh call and replaces the cached summary only if the new one is accepted. Without
`OPENAI_API_KEY` it reports "unavailable" and everything else works unchanged.
Copy `.env.example` to `.env` to enable it.
