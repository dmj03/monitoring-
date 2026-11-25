import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listAlerts, type AlertQuery } from "../api/alerts";
import { AddAlertModal } from "../components/AddAlertModal";
import { Empty, ErrorBox, Loading } from "../components/ui";
import { clusterOf, routingState, useClusters, useInvestigations, useRouting } from "../lib/hooks";
import { fmtDetected, human } from "../lib/format";
import type { Alert } from "../api/types";

const PAGE = 25;
const API_MAX = 500; // GET /alerts accepts limit up to 500
type Kind = "" | "standalone" | "clustered";
const NO_FILTERS = { rule_id: "", programme_ref: "", customer_ref: "" };

/**
 * Every alert matching the server-side filters, fetched in API-sized pages.
 * The list must be complete because ordering (newest first), the pattern and
 * destination filters, search and pagination all apply to the whole set.
 */
async function fetchAll(filters: AlertQuery): Promise<Alert[]> {
  const out: Alert[] = [];
  for (let offset = 0; ; offset += API_MAX) {
    const d = await listAlerts({ ...filters, limit: API_MAX, offset });
    const rows = Array.isArray(d?.alerts) ? d.alerts : [];
    out.push(...rows);
    if (!rows.length || offset + API_MAX >= (d?.total ?? 0)) return out;
  }
}

/** Newest first; alert_id breaks ties so the order is stable. */
function newestFirst(a: Alert, b: Alert): number {
  const t = String(b.created_at ?? "").localeCompare(String(a.created_at ?? ""));
  return t !== 0 ? t : b.alert_id.localeCompare(a.alert_id);
}

/** Page numbers to show: all of them when few, otherwise first, last and the neighbourhood of the current page. */
function pageList(page: number, pages: number): (number | "…")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const keep = new Set([1, pages, page - 1, page, page + 1].filter((p) => p >= 1 && p <= pages));
  const sorted = [...keep].sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  sorted.forEach((p, i) => {
    if (i && p - sorted[i - 1] > 1) out.push("…");
    out.push(p);
  });
  return out;
}

function Pager({
  where,
  page,
  pages,
  total,
  onPage,
}: {
  where: "top" | "bottom";
  page: number;
  pages: number;
  total: number;
  onPage: (p: number) => void;
}) {
  const from = total === 0 ? 0 : (page - 1) * PAGE + 1;
  const to = Math.min(page * PAGE, total);
  return (
    <nav className={`pager pager-${where}`} aria-label={`Pagination (${where})`}>
      <span>
        Showing {from}–{to} of {total}
        <span className="dim"> · Page {total === 0 ? 0 : page} of {pages}</span>
      </span>
      <span className="pager-btns">
        <button className="btn btn-sm" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          ‹ Previous
        </button>
        {pageList(page, pages).map((p, i) =>
          p === "…" ? (
            <span key={`gap-${i}`} className="pg-gap">
              …
            </span>
          ) : (
            <button
              key={p}
              className={`btn btn-sm pg${p === page ? " on" : ""}`}
              aria-label={`Page ${p}`}
              aria-current={p === page ? "page" : undefined}
              onClick={() => onPage(p)}
            >
              {p}
            </button>
          ),
        )}
        <button className="btn btn-sm" aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next ›
        </button>
      </span>
    </nav>
  );
}

export default function Alerts() {
  const nav = useNavigate();
  const [all, setAll] = useState<Alert[] | null>(null); // unfiltered: options and tab counts
  const [matching, setMatching] = useState<Alert[] | null>(null); // after the server-side filters
  const [error, setError] = useState<unknown>(null);
  const [filters, setFilters] = useState(NO_FILTERS);
  const [kind, setKind] = useState<Kind>("");
  const [dest, setDest] = useState(""); // "" all | "none" | "destination:<id>" | "outcome:<id>"
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [modal, setModal] = useState(false);
  const [created, setCreated] = useState<Alert | null>(null);
  const [refresh, setRefresh] = useState(0);
  const clusters = useClusters(refresh);
  const routing = useRouting(refresh);
  const filtered = !!(filters.rule_id || filters.programme_ref || filters.customer_ref);

  useEffect(() => {
    let live = true;
    setError(null);
    fetchAll({})
      .then((rows) => live && setAll(rows))
      .catch((e) => live && (setError(e), setAll(null)));
    return () => {
      live = false;
    };
  }, [refresh]);

  // Rule, programme and customer are filtered by the API, as before.
  useEffect(() => {
    if (!filtered) {
      setMatching(null);
      return;
    }
    let live = true;
    setMatching(null);
    fetchAll(filters)
      .then((rows) => live && setMatching(rows))
      .catch((e) => live && setError(e));
    return () => {
      live = false;
    };
  }, [filters, filtered, refresh]);

  const base = filtered ? matching : all;

  // Pattern, destination and search narrow the whole matching set; then newest
  // first; then one page. The page controls therefore count only what matches.
  const list = useMemo(() => {
    if (!base) return null;
    const q = search.trim().toLowerCase();
    return base
      .filter((a) => {
        const member = !!clusters?.membership?.[a.alert_id];
        if (kind === "clustered" && !member) return false;
        if (kind === "standalone" && member) return false;
        if (dest) {
          const r = routingState(routing.data, a.alert_id);
          const here = r.record
            ? r.record.destination
              ? `destination:${r.record.destination}`
              : `outcome:${r.record.outcome}`
            : "none";
          if (here !== dest) return false;
        }
        return !q || [a.alert_id, a.rule_id, a.customer_ref, a.programme_ref].some((v) => v.toLowerCase().includes(q));
      })
      .sort(newestFirst);
  }, [base, kind, dest, search, clusters, routing.data]);

  const total = list?.length ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const current = Math.min(page, pages);
  useEffect(() => {
    if (page > pages) setPage(pages);
  }, [page, pages]);

  const rows = useMemo(() => {
    const slice = (list ?? []).slice((current - 1) * PAGE, current * PAGE);
    // A just-created alert stays visible at the top of the first page until dismissed.
    return created && current === 1 && !slice.some((a) => a.alert_id === created.alert_id) ? [created, ...slice] : slice;
  }, [list, current, created]);

  const inv = useInvestigations(rows);
  const categoryOf = useMemo(() => {
    const m: Record<string, string> = {};
    for (const s of Object.values(inv)) if (s.st === "ok") m[s.pack.rule_id] = s.pack.category;
    return m;
  }, [inv]);

  const options = useMemo(
    () => ({
      rules: [...new Set((all ?? []).map((a) => a.rule_id))].sort(),
      programmes: [...new Set((all ?? []).map((a) => a.programme_ref))].sort(),
      customers: [...new Set((all ?? []).map((a) => a.customer_ref))].sort(),
    }),
    [all],
  );

  const reset = () => setPage(1);
  const setFilter = (k: string, v: string) => {
    reset();
    setFilters((f) => ({ ...f, [k]: v }));
  };

  function onCreated(a: Alert) {
    setModal(false);
    setCreated(a);
    reset();
    setKind("");
    setDest("");
    setSearch("");
    setFilters(NO_FILTERS);
    setRefresh((n) => n + 1); // authoritative refresh from the API, and clusters recomputed
  }

  const nClustered = clusters ? Object.keys(clusters.membership ?? {}).length : 0;
  const tabs: [Kind, string, number | null][] = [
    ["", "All alerts", all ? all.length : null],
    ["standalone", "Standalone", clusters && all ? all.length - nClustered : null],
    ["clustered", "Clustered", clusters ? nClustered : null],
  ];

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Alerts</h1>
          <p className="sub">
            Alerts already raised by upstream monitoring, newest first. Open one to see its deterministic
            investigation and the AI analyst summary.
          </p>
        </div>
        <button className="btn btn-p" onClick={() => setModal(true)}>
          + Add alert
        </button>
      </div>

      {created ? (
        <div className="toast" role="status">
          <div>
            <strong>Alert {created.alert_id} created successfully</strong>
            <span>Saved to the dataset through POST /alerts. Clusters were recomputed.</span>
          </div>
          <div className="toast-actions">
            <button className="btn btn-p" onClick={() => nav(`/alerts/${created.alert_id}`)}>
              Open alert
            </button>
            <button className="btn" onClick={() => setCreated(null)}>
              Dismiss
            </button>
          </div>
        </div>
      ) : null}

      <div className="seg" role="tablist" aria-label="Alert type">
        {tabs.map(([k, label, n]) => (
          <button
            key={label}
            role="tab"
            aria-selected={kind === k}
            className={kind === k ? "on" : ""}
            onClick={() => {
              setKind(k);
              reset();
            }}
          >
            <span className={`sw ${k === "clustered" ? "sw-cl" : k === "standalone" ? "sw-sa" : "sw-all"}`} />
            {label}
            {n !== null ? <span className="n">{n}</span> : null}
          </button>
        ))}
      </div>

      <div className="filters">
        <label className="f-search">
          <span>Search</span>
          <input
            aria-label="Search alerts"
            placeholder="Alert, rule, customer or programme"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              reset();
            }}
          />
        </label>
        <label>
          <span>Rule</span>
          <select aria-label="Rule ID" value={filters.rule_id} onChange={(e) => setFilter("rule_id", e.target.value)}>
            <option value="">All rules</option>
            {options.rules.map((r) => (
              <option key={r} value={r}>
                {categoryOf[r] ? `${r} · ${human(categoryOf[r])}` : r}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Programme</span>
          <select
            aria-label="Programme"
            value={filters.programme_ref}
            onChange={(e) => setFilter("programme_ref", e.target.value)}
          >
            <option value="">All programmes</option>
            {options.programmes.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Customer</span>
          <select
            aria-label="Customer"
            value={filters.customer_ref}
            onChange={(e) => setFilter("customer_ref", e.target.value)}
          >
            <option value="">All customers</option>
            {options.customers.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Destination</span>
          <select
            aria-label="Destination"
            value={dest}
            disabled={!routing.data}
            onChange={(e) => {
              setDest(e.target.value);
              reset();
            }}
          >
            <option value="">All</option>
            <option value="none">Not routed</option>
            {(routing.data?.destinations ?? []).map((d) => (
              <option key={d.id} value={`destination:${d.id}`}>
                {d.label}
              </option>
            ))}
            {(routing.data?.outcomes ?? []).map((o) => (
              <option key={o.id} value={`outcome:${o.id}`}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button
          className="btn btn-ghost"
          onClick={() => {
            setFilters(NO_FILTERS);
            setSearch("");
            setKind("");
            setDest("");
            reset();
          }}
        >
          Clear
        </button>
      </div>

      {error ? <ErrorBox error={error} /> : null}

      {list && total > 0 ? <Pager where="top" page={current} pages={pages} total={total} onPage={setPage} /> : null}

      <div className="card table-card">
        {!list ? (
          error ? null : <Loading what="Loading alerts…" />
        ) : rows.length === 0 ? (
          <Empty title="No alerts found" hint="Clear the filters or search to see every alert." />
        ) : (
          <table className="queue">
            <thead>
              <tr>
                <th>Alert</th>
                <th>Category</th>
                <th>Customer</th>
                <th>Programme</th>
                <th>Detected</th>
                <th>Pattern</th>
                <th>Destination</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => {
                const c = clusterOf(clusters, a.alert_id);
                const s = inv[a.alert_id];
                const miss = s?.st === "ok" ? s.pack.missing_evidence.length : 0;
                const isNew = created?.alert_id === a.alert_id;
                return (
                  <tr
                    key={a.alert_id}
                    className={`row-link${c ? " is-cl" : ""}${isNew ? " is-new" : ""}`}
                    onClick={() => nav(`/alerts/${a.alert_id}`)}
                  >
                    <td>
                      <span className="id">{a.alert_id}</span>
                      {isNew ? <span className="tag-new">Just added</span> : null}
                    </td>
                    <td>
                      <div className="cat">{s?.st === "ok" ? human(s.pack.category) : "—"}</div>
                      <div className="rule mono">{a.rule_id}</div>
                    </td>
                    <td className="mono">{a.customer_ref}</td>
                    <td className="mono">{a.programme_ref}</td>
                    <td className="mono dim when">
                      {fmtDetected(a.created_at)
                        .split(", ")
                        .map((part) => (
                          <span key={part}>{part}</span>
                        ))}
                    </td>
                    <td>
                      {c ? (
                        <span className="pat pat-cl">
                          <span className="sw sw-cl" />
                          {c.label}
                        </span>
                      ) : (
                        <span className="pat">
                          <span className="sw sw-sa" />
                          Standalone
                        </span>
                      )}
                    </td>
                    <td>
                      <DestinationCell alertId={a.alert_id} routing={routing} />
                    </td>
                    <td>
                      {s?.st === "ok" ? (
                        <span className="evc">
                          <span className="ev-f">✓ {s.pack.evidence.length - miss}</span>
                          <span className={miss ? "ev-m" : "ev-0"}>◌ {miss}</span>
                        </span>
                      ) : (
                        <span className="dim">{s?.st === "error" ? "Not available" : "Running…"}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {list && total > 0 ? <Pager where="bottom" page={current} pages={pages} total={total} onPage={setPage} /> : null}
      <p className="fine">✓ found ◌ missing, from the read-only investigation of each visible alert.</p>

      {modal ? <AddAlertModal onClose={() => setModal(false)} onCreated={onCreated} /> : null}
    </div>
  );
}

/** Where an alert was routed, or that it was closed, or that it is not routed yet. Never blank. */
function DestinationCell({
  alertId,
  routing,
}: {
  alertId: string;
  routing: ReturnType<typeof useRouting>;
}) {
  if (!routing.data) return <span className="dim">{routing.failed ? "Unavailable" : "…"}</span>;
  const r = routingState(routing.data, alertId);
  const via = r.via ? <span className="via">via cluster</span> : null;
  if (r.kind === "destination")
    return (
      <span className="dest">
        <span className="pill pill-route">{r.label}</span>
        {via}
      </span>
    );
  if (r.kind === "outcome")
    return (
      <span className="dest">
        <span className="pill pill-closed">{r.label}</span>
        {via}
      </span>
    );
  return <span className="dim">Not routed</span>;
}
