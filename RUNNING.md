# Running the UC3 Monitoring POC

From extracted ZIP to a working console, on Windows 11 with VS Code and PowerShell.

## Prerequisites

| Tool | Version used to build and verify this project |
|---|---|
| Python | 3.12.3 (3.10 or newer should work; 3.12 is what was tested) |
| Node.js | 22.22.2 (18 or newer should work) |
| npm | 10.9.7 |

Nothing else is required. No database, no Docker, no globally installed packages.

## 1. Extract and open

Extract the ZIP, then open the `UC3_Monitoring_POC` folder in VS Code —
`File → Open Folder`, and pick the folder that directly contains `backend/`
and `requirements.txt`.

## 2. Terminal 1 — backend

```powershell
cd <path-to>\UC3_Monitoring_POC

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

If PowerShell blocks the activation script, allow it for the current session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

Run uvicorn from the project root, not from inside `backend\`. The module path
`backend.api:app` is resolved relative to the working directory.

You should see:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

Check it:

```powershell
curl http://127.0.0.1:8000/health
```

```json
{"status":"ok","kb_version":"kb-playbooks-1.0.1-phase1","playbooks":10,"cluster_types":4}
```

Interactive API docs: <http://127.0.0.1:8000/docs>

**Optional — AI analyst summary.** Copy `.env.example` to `.env` in the project
root and set `OPENAI_API_KEY`. `LLM_MODEL` is read from the same file
(`gpt-5-mini` in the example). Restart uvicorn after editing `.env`. Without a
key the "Generate AI summary" button reports that the AI summary is unavailable;
the investigation itself is unaffected. `.env` is gitignored — never commit it.

## 3. Terminal 2 — frontend

Leave the backend running. Open a second terminal:

```powershell
cd <path-to>\UC3_Monitoring_POC\frontend

npm install
npm run dev
```

```
➜  Local:   http://localhost:5173/
```

## 4. Open the application

<http://localhost:5173>

## The two-terminal workflow

```
TERMINAL 1                        TERMINAL 2
backend / FastAPI                 frontend / Vite
        ↓                                 ↓
http://127.0.0.1:8000             http://localhost:5173
        ↑                                 │
        └────── /api proxy ───────────────┘
```

The browser only ever talks to Vite. Vite forwards every request beginning
`/api` to FastAPI, rewriting `/api/alerts` to `/alerts`. Because the browser
sees a single origin, no CORS configuration is needed on the backend.

The proxy target is set in `frontend/vite.config.ts` and can be overridden:

```powershell
$env:VITE_API_TARGET = "http://127.0.0.1:9000"
npm run dev
```

If you change the backend port, change this too. See `frontend/.env.example`.

## What happens when you click "Add alert"

```
Frontend (Add alert modal)
   |  POST /api/alerts  ->  proxied to  POST /alerts
FastAPI route create_alert
   |
DataStore.add_alert()
   |  re-reads data/dataset.json from disk
   |  rejects a duplicate alert_id with 409
   |  appends the alert
   |  writes a temp file, fsyncs, os.replace() - atomic
data/dataset.json          <- the alert is now persistent
   |
DataStore reloads from what was written
   |
Frontend refreshes with GET /alerts
   |
the new alert appears in the queue
```

The alert survives a backend restart, because the dataset file is the source of
truth. The frontend never writes the file; it only calls the API.

## What happens on the Clusters page

```
Frontend
   |  GET /api/clusters
ClusterEngine.detect(store.alerts)
   |  groups alerts on alert-level fields only
   |  applies min_alerts, min_customers, time_window_hours from the KB
   |
clusters returned to the frontend
```

**Clusters are not stored.** They are recalculated on every request from the
current alerts. Nothing about clustering is written to `dataset.json`, and a
cluster reference such as `C001` is assigned per detection run — stable for
identical input, but not a durable identifier. That is why there is no
`GET /clusters/{cluster_id}` endpoint.

An alert added through the UI is included in the next `/clusters` call
automatically, and may change which clusters form.

## Stopping

`Ctrl+C` in each terminal. Both shut down cleanly; there is no state to flush.

## Troubleshooting

**"API unreachable" in the header.** The backend is not running, or is on a
different port than the proxy target. Check terminal 1 and
`frontend/vite.config.ts`.

**`ModuleNotFoundError: No module named 'backend'`.** uvicorn was started from
the wrong directory. Run it from the project root.

**Port already in use.** Pick another port and keep the two in step:
`uvicorn backend.api:app --port 8001` plus
`$env:VITE_API_TARGET="http://127.0.0.1:8001"`.

**`npm run dev` cannot find vite.** `npm install` did not finish. Re-run it
inside `frontend\`.

## Restoring the dataset

`POST /alerts` writes to `data/dataset.json`. If you want to return to the
shipped state, re-extract that one file from the ZIP. Its original checksum is
recorded in `PROJECT_STRUCTURE.md`.
