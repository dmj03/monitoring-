# UC3 frontend — design review

Status: **options for decision. Nothing here is implemented in the product.**
Prototypes: `npm run dev` in `frontend/`, then open `http://localhost:5173/design-review/` (see README.md).

Every option below exists as a clickable prototype. Each prototype screen has its own
notes panel (structure, emphasis, pros, cons, suits, backend impact, feasibility now).
This document is the overview; the prototypes are the detail.

Label key used everywhere:

| Label | Meaning |
|---|---|
| Deterministic engine | returned by the current API today |
| Computed in UI from existing API | derivable today by combining existing endpoints |
| From KB playbook | text that exists in the KB |
| **Future backend field** | exists in the KB or engine but **not** returned by the API; needs backend work |
| **AI summary — planned, not built** | needs a model call that does not exist |

---

## 1. Current frontend — assessment

The current UI is honest and functional: it never fakes data, keeps MISSING distinct from
false, and talks only to the API. Its weaknesses are hierarchy and framing, plus three
genuine defects.

**Defects found during this review**

| # | Defect | Where |
|---|---|---|
| D1 | **Cluster membership is never shown on Alerts or Alert Detail.** Both read the stored `alert.cluster_ref`, which is `null` on all 90 alerts because clusters are computed, not stored. ALRT-0001 is in a cluster yet shows "Cluster —". The data is available from `GET /clusters` → `membership`. | `Alerts.tsx`, `AlertDetail.tsx` |
| D2 | The 422 response carries a field-specific message (e.g. the new `created_at` rule), but the modal shows only "Please check the highlighted fields." | `ui.tsx` `errorText`, `AddAlertModal.tsx` |
| D3 | A newly created alert is appended to dataset order, so it lands on the last page; after "created successfully" the user cannot see it. | `Alerts.tsx` |

**Framing issues**

- Header says "AI operations console"; there is no AI. Reads as an overclaim.
- `FlowStrip` (Alert → Cluster context → Playbook → Evidence → Classification → Human review) is decorative: "Cluster context" and "Human review" steps have no content, and it lights all steps at once.
- "Not investigated" badge on every row is not a backend state. Investigations are stateless and recomputed on demand, so the badge is permanently true and conveys nothing.
- Overview's "Recent alerts" duplicates the Alerts table.
- Investigation output is a stack of equal-weight panels; nothing says what matters first.
- KB content that would help the analyst (whether a MISSING item matters, playbook purpose, cluster interpretation and boundaries) is not exposed by the API, so the UI cannot show it.

## 2. Screen-by-screen inventory

| Screen | Displays / source | User can | Useful | Unnecessary | Missing | Visually weak |
|---|---|---|---|---|---|---|
| **Overview** | 4 count cards (alerts, clusters, distinct customers, distinct programmes) and 8 recent alerts. `GET /alerts?limit=500` + `GET /clusters` | click a row → detail | cluster count | customer/programme counts; recent-alerts table (dup of Alerts); "Not investigated" | anything actionable: gaps, which patterns, why | four identical cards, no emphasis |
| **Alerts** | paged table (25): id, rule, customer, programme, created, cluster, status. `GET /alerts` with rule/programme/customer filters | filter, page, search current page, add alert, open detail | server-side filters & paging; Add | Cluster column (always empty, D1); Status column | category, evidence state, pattern membership, sort order | dense but flat; filters are raw IDs |
| **Alert detail** | key/values of the alert, decorative flow strip, Investigate button. `GET /alerts/{id}` | run / re-run investigation | clean fact list | flow strip; Cluster field (D1) | pattern context; what the rule means | page is nearly empty until you click Investigate |
| **Investigation output** | classification, evidence table (key/status/value/source), missing chips, tool calls (collapsed), checklist, escalation guidance, AI boundaries, allowed outcomes. `POST /investigate` | expand tool calls | evidence table; MISSING rendering; checklist; tool-call collapse | repeated MISSING (table + chips); AI boundaries shown to analyst at full weight | whether a gap matters (KB `required`); pattern context; summary | all panels same weight; long scroll |
| **Clusters** | table of clusters + detail panel for the selected one: type, rules, programmes, thresholds, members. `GET /clusters` + `GET /alerts/{id}` per member | select a cluster, open a member | membership, thresholds, run-scoped note | category badge text; `C001` refs as the leading column (look durable) | why it formed (vs thresholds), time distribution, interpretation guidance | master/detail hides everything but one cluster |
| **Add alert** | modal with 7 free-text fields | create; 409/422 handling | posts exactly AlertCreate; duplicate message | `cluster_ref` field (stored but never used by anything) | rule/programme pickers; field-level API messages (D2); where the alert went (D3) | all fields equal weight, no grouping |
| **Navigation / header** | sidebar (Overview, Alerts, Clusters); header with API health + KB version | navigate | health indicator | "AI operations console" subtitle | — | — |
| **Empty / error / loading** | text states; error box mapping 404/409/422/5xx/network | — | never shows tracebacks; network message is actionable | — | 422 detail (D2) | plain, but adequate |

## 3. Information architecture — options

| | IA-1 Four areas | IA-2 Queue-centred | IA-3 Queue + pattern monitor |
|---|---|---|---|
| Pages | Overview · Alerts · Alert detail (+ investigation) · Clusters | Alerts · Alert detail (pattern context inside) | Alerts · Alert detail · Pattern monitor (replaces Overview *and* Clusters) |
| Fits | Overview A/B, Clusters A/C | Overview C, Clusters B | Overview C, Clusters D |
| Trade-off | most complete, most to build | least to build, no pattern-level view | one fixed monitoring page instead of two |

Independent of IA: **should Alert detail auto-run the investigation on open?** It is deterministic,
fast and stores nothing, so the explicit "Investigate" click adds a step without adding meaning —
but the click does make "the engine ran now" visible. Decision needed.

### Information hierarchy per page (applies across options)

| Page | Primary | Secondary | Tertiary / technical | Remove | Future |
|---|---|---|---|---|---|
| Overview | alerts with evidence gaps; detected patterns | recent alerts | totals | customer/programme counts, "Not investigated" | AI digest of the day |
| Alerts | alert id, category, evidence state, pattern | customer, programme, created | rule id | Status column; stored `cluster_ref` column | sort param, category filter |
| Alert detail | category + what to establish (focus); gaps | evidence values; pattern context | rule→playbook mapping, sources | flow strip as-is | decision recording |
| Investigation | gaps and whether they matter; evidence values | checklist; escalation guidance; allowed outcomes | tool calls + args; playbook AI boundaries; sources | duplicated MISSING chips (if banner kept) | `required` flag; AI summary |
| Clusters | which patterns exist now and why (vs thresholds) | members with customer + evidence state | run ref; cluster_category | `C001` as the leading identifier | interpretation guidance text |
| Add alert | id, rule, customer, programme, created_at | shared_match_key | — | `cluster_ref` field | routing preview |

## 4. Design system — alternatives (prototype: *Design system*)

| | A Enterprise | B AI ops | C Minimal | D Dark SOC |
|---|---|---|---|---|
| Mode | light | light | light (paper) | dark |
| Navigation | dark side rail with labels | white sidebar, soft active pill | top navigation, no sidebar | icon rail |
| Cards | 4px radius, 1px border, flat | 12px radius, soft shadow | none — hairline rules | 3px radius, border |
| Tables | dense, zebra rows | airy | airy, ruled | dense, monospace |
| Type | Segoe UI, Consolas numbers | Segoe UI, semibold headings | Georgia headings | Consolas headings & data |
| Density | high | medium-low | low | very high |
| AI colour | violet, dashed | violet, dashed | indigo, dashed | lilac on deep violet |
| Primary action | solid blue | solid indigo | solid teal | solid cyan |

Also prototyped: **4 MISSING renderings** (badge+dash, dashed tile, explicit sentence, icon pair) and
**4 cluster visualisations** (time strip, threshold check, member chips, sentence). In every direction
MISSING is amber, never red, and never shows a value.

The direction switch at the top of the prototype re-skins every screen, so a layout can be judged
in any direction.

## 5–11. Screen alternatives (all prototyped)

| Area | Options |
|---|---|
| **Overview** | A Triage home (gaps, patterns, recent) · B Monitoring dashboard (KPIs, by category, by programme) · C Remove Overview, open on Alerts |
| **Alerts** | A Dense triage table (category, pattern, evidence columns) · B List + preview split · C Grouped by pattern then category |
| **Alert detail** | A Investigation-first · B Analyst workspace (3 columns + decision area) · C Evidence-first (tile grid) · D Executive summary (narrative, details folded) |
| **Investigation** | A Summary → status → evidence by source → gaps → technical · B Workflow 1–6 with timeline · C Context \| Investigation \| Evidence |
| **AI summary** | A Card on top · B Dedicated section after evidence, with cited/not-cited list · C Assistant side panel · plus the real `build_llm_input()` payload |
| **Clusters** | A Separate page, one card per pattern · B Inside Alert detail only · C Both · D Pattern monitor (four fixed types) |
| **Add alert** | A Improved modal (+ error state) · B Dedicated page with routing preview · C Side drawer with pinned new alert |

### AI summary — design constraints common to all options

- **Input** (exists today, `build_llm_input()`): alert id/rule/category, investigation focus, checklist,
  escalation guidance, global + playbook AI boundaries, evidence map with literal `MISSING`,
  missing-evidence list, permitted outcomes, instruction.
- **Output** (future): short rationale; every sentence cites evidence keys; MISSING items cited as gaps;
  at most one recommended outcome from `permitted_outcomes`, labelled "for analyst confirmation".
- **Must not claim**: suspicion, confirmed matches, clearance, a final decision, any value not in the pack,
  the period a count covers (the pack has none — KNOWN_LIMITATIONS L2).
- **Shown alongside**: the deterministic evidence table, always; the summary never replaces it.
- **Distinguishing generated from deterministic**: separate container, dashed border, distinct colour,
  explicit label, citation chips. Prototype text is hand-written and flagged as such.
- **Certainty**: no confidence score (none exists), no "risk" wording; outcome shown as a suggestion chip.

### Clusters — constraints common to all options

No durable identity is implied: the run ref is secondary text labelled "run ref", links go by cluster
*type*, and every view says patterns are recomputed. There is no `GET /clusters/{id}` in any design.

## 12. KEEP / REMOVE / REDESIGN / MOVE / COLLAPSE / FUTURE — proposal for your decision

| Current element | Proposed | Note / alternative |
|---|---|---|
| Overview count cards | REDESIGN or REMOVE | depends on Overview A/B/C |
| Overview "Recent alerts" table | REMOVE (dup) or keep short | |
| "Not investigated" status badge | REMOVE | not a backend state |
| Alerts table | KEEP, REDESIGN columns | add category/pattern/evidence; drop Status |
| Alerts "Cluster" column (stored `cluster_ref`) | REDESIGN | use `/clusters` membership (D1) |
| Alerts free-text "search this page" | KEEP or REMOVE | only searches 25 rows |
| Header subtitle "AI operations console" | REMOVE / reword | until AI exists |
| API health indicator + KB version | KEEP | move KB version to a footer if wanted |
| FlowStrip | REMOVE or REDESIGN | becomes Investigation B if chosen |
| Alert facts key/value list | KEEP, REDESIGN as a strip | |
| "Investigate alert" button | KEEP or auto-run | decision needed |
| Classification panel | KEEP | |
| Investigation focus | KEEP, promote | it is the "what to establish" sentence |
| Evidence table | KEEP | central |
| Evidence source column (`tool.field`) | COLLAPSE / MOVE to technical | |
| Missing-evidence chips | REDESIGN as gap banner | avoid showing MISSING twice |
| Tool calls + raw arguments | COLLAPSE (technical) | keep for audit; arguments are useful for QA |
| Investigation checklist | KEEP | |
| Escalation guidance | KEEP, secondary | |
| Playbook AI boundaries | COLLAPSE / MOVE | relevant once AI exists; next to the AI block |
| Allowed outcomes | KEEP, label as "permitted" | display-only until a decision endpoint exists |
| Clusters page | KEEP-REDESIGN, MOVE, or REMOVE | Clusters A / B / C / D |
| Cluster `C001` refs as primary identifiers | REDESIGN | secondary "run ref" |
| Cluster thresholds | KEEP, as a formed-vs-threshold check | |
| Add alert (modal) | KEEP, REDESIGN | |
| Add alert `cluster_ref` field | REMOVE from form | stored but unused by any component |
| Add alert free-text rule | REDESIGN as picker | |
| 422 field message | REDESIGN | D2 |
| AI summary | FUTURE | position per AI option |
| Analyst decision recording | FUTURE | needs endpoint + persistence decision |
| "Bears on outcome" per MISSING item | FUTURE | needs `required` on EvidenceOut |
| Cluster interpretation text | FUTURE | needs API field |

## 13. Prototypes

`frontend/design-review/` — 9 sections, 29 option screens, 4 visual directions, 3 sample alerts.
See README.md for how to open them and what each label means.

## 14. Backend / API changes that would eventually be required

Grouped by what they unlock. **None is required for a first redesign** (see 15).

| ID | Change | Unlocks | Size |
|---|---|---|---|
| F1 | `required` + `missing_behaviour` on each `EvidenceOut` (from playbooks.yaml) | "does this gap matter" | small, additive |
| F2 | playbook `description` / `purpose` on the investigation response | Alert detail D, better classification copy | small, additive |
| F3 | cluster interpretation (`purpose`, `interpretation_guidance`, `analyst_guidance`, `boundaries`) on `ClusterOut` or a `GET /cluster-types` | Clusters A/B/D explanation | small |
| F4 | `GET /rules` (routing: rule → playbook/category) | Add-alert rule picker; category column without investigating | small |
| F5 | category and/or evidence summary on `GET /alerts` (or a batch investigate) | Alerts A, Overview A/B without N calls | medium |
| F6 | sort parameter on `GET /alerts` (e.g. newest first) | queue order, new alert visible | small |
| F7 | AI summary endpoint using `build_llm_input()` + output guard | all AI options | large; new dependency |
| F8 | analyst decision endpoint + persistence decision | Alert detail B decision panel | large; architectural |
| F9 | decision on unknown `rule_id` at `POST /alerts` (pending from fix phase) | Add-alert validation | small |

## 15. Implementable immediately with the current API

- Any visual direction (A–D).
- Pattern membership on Alerts and Alert detail from `GET /clusters` (fixes D1).
- Field-level 422 messages in the Add-alert form (fixes D2); pin/highlight the new alert (fixes D3).
- Rule and programme pickers, populated from existing alerts (all 10 rules present today).
- All Alert detail layouts A–C; Investigation A–C; Clusters A–D (minus interpretation text).
- Evidence/category columns and the "needs attention" list, by calling `POST /investigate` per alert
  (works; 90 calls; acceptable for a POC, replaceable by F5 later).
- Placeholders for AI, clearly labelled — or omit AI entirely until F7.

## 16. Proposed implementation sequence (after decisions)

1. **Design tokens + shell** for the chosen direction (CSS variables, navigation form). No behaviour change.
2. **Defect fixes** D1–D3 (pattern membership, 422 messages, new-alert visibility). Small, testable.
3. **Alert detail + investigation layout** for the chosen options, existing API only.
4. **Alerts table** columns/filters for the chosen option (with N× investigate or deferred columns).
5. **Clusters** presentation for the chosen option.
6. **Overview** (or its removal).
7. **Add alert** redesign (pickers, inline errors).
8. Backend additions in order of value/size: F1, F4, F2, F3, F6, F5, F9.
9. **AI**: F7 and the chosen AI placement — a separate phase with its own design and test plan.
10. Decision recording (F8) only if the POC scope is widened.

Each step keeps the existing 15 frontend tests passing and adds tests for new behaviour.
