# Design review prototypes

Clickable design alternatives for the UC3 frontend. **Not part of the product.**

## Open them

```powershell
cd frontend
npm run dev
```

Then open <http://localhost:5173/design-review/>. No backend is needed.

The product at `/` is unaffected: this folder has its own `index.html`, is outside `src/`, is not in
the product's `tsconfig`, is not picked up by `vitest`, and is not part of `npm run build`.

### Or: one standalone file

The prototype can be built as a single self-contained HTML file (scripts, styles, data and
screenshots embedded) that opens by double-click. The file is **not kept in the repository**:

```powershell
cd frontend
node design-review/build-standalone.mjs
```

This writes `design-review/UC3_Design_Review.html`; move it out of the repo afterwards.

## What's inside

| Path | |
|---|---|
| `build-standalone.mjs`, `vite.config.mjs` | builds the standalone HTML file (output not kept in the repo; the product's build is untouched) |
| `DESIGN_REVIEW.md` | the written review: assessment, options, KEEP/REMOVE table, backend needs, sequence |
| `index.html`, `src/` | the prototype app (React + TypeScript, same toolchain as the product) |
| `src/screens/*` | one file per area; each exports its options with design notes |
| `proto.css` | the four visual directions as CSS variable sets |
| `data/snapshot.json` | read-only snapshot of real API responses (see below) |
| `current/*.png` | screenshots of today's UI, for comparison |

## Data

`data/snapshot.json` was produced by calling the real application in-process with read-only
requests only (`GET /health`, `GET /alerts`, `GET /clusters`, `POST /investigate` — which stores
nothing). It also contains, under clearly named keys:

- `engine_not_api` — the real `build_llm_input()` payload for three alerts (exists in code, not exposed, never sent)
- `kb_not_api` — KB text the API does not return yet (evidence `required` flags, playbook purpose, cluster interpretation)

The dataset hash was verified unchanged before and after. Field names are exactly the API's.

## Labels

| Label | Meaning |
|---|---|
| Deterministic engine | returned by the API today |
| Computed in UI from existing API | derivable today by combining endpoints |
| From KB playbook | KB text |
| Future backend field | exists in KB/engine, not in the API — needs backend work |
| AI summary — planned, not built | needs a model call that does not exist |

Illustrative AI text is hand-written for layout review, restates only real evidence values, cites
every key, and is flagged on screen as not model output.

## Removing

Delete this folder. Nothing else references it.
