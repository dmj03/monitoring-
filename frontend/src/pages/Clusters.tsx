import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { getAlert } from "../api/alerts";
import { listClusters } from "../api/clusters";
import { clusterKey, setClusterRouting } from "../api/routing";
import { ClusterSummaryCard } from "../components/ClusterSummaryCard";
import { RoutingPanel } from "../components/RoutingPanel";
import { Empty, ErrorBox, Loading, SectionHead } from "../components/ui";
import { routingState, useInvestigations, useRouting, viewOf } from "../lib/hooks";
import { fmtDetected, human } from "../lib/format";
import type { Alert, ClusterList } from "../api/types";

export default function Clusters() {
  const nav = useNavigate();
  const loc = useLocation();
  const fromAlert = (loc.state as { alertId?: string } | null)?.alertId;
  const [data, setData] = useState<ClusterList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);
  // alert_id -> the alert itself, read via GET /alerts/{id}. null = lookup failed.
  const [alertOf, setAlertOf] = useState<Record<string, Alert | null>>({});
  const [routingRefresh, setRoutingRefresh] = useState(0);
  const routing = useRouting(routingRefresh);

  useEffect(() => {
    listClusters()
      .then((d) => {
        setData(d);
        // Arriving from an alert selects that alert's cluster in this run;
        // cluster_ref is assigned per run, so it is never carried in the URL.
        const viaAlert = fromAlert ? d.membership?.[fromAlert] : undefined;
        setSelected(viaAlert ?? d.clusters[0]?.cluster_ref ?? null);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [fromAlert]);

  const current = data?.clusters.find((c) => c.cluster_ref === selected) ?? null;

  // A cluster's customer_refs is the sorted set of distinct customers, not a
  // list aligned with alert_ids, so each member is looked up individually.
  useEffect(() => {
    const ids = (current?.alert_ids ?? []).filter((id) => !(id in alertOf));
    if (!ids.length) return;
    let live = true;
    Promise.all(
      ids.map((id) =>
        getAlert(id)
          .then((a): [string, Alert | null] => [id, a && typeof a === "object" ? a : null])
          .catch((): [string, Alert | null] => [id, null]),
      ),
    ).then((pairs) => {
      if (live) setAlertOf((m) => ({ ...m, ...Object.fromEntries(pairs) }));
    });
    return () => {
      live = false;
    };
  }, [current, alertOf]);

  // Member investigations (read-only) give each member's category and its
  // playbook's allowed_outcomes, which decide whether the cluster may be closed.
  const memberAlerts = useMemo(
    () => (current?.alert_ids ?? []).map((id) => alertOf[id]).filter((a): a is Alert => !!a && !!a.alert_id),
    [current, alertOf],
  );
  const inv = useInvestigations(memberAlerts);
  const clusterOutcomes = useMemo(() => {
    const ids = current?.alert_ids ?? [];
    const packs = ids.map((id) => inv[id]);
    if (!ids.length || packs.some((p) => p?.st !== "ok")) return null;
    const lists = packs.map((p) => (p?.st === "ok" ? p.pack.allowed_outcomes : []));
    return lists.reduce((a, b) => a.filter((o) => b.includes(o)));
  }, [current, inv]);

  if (loading) return <Loading what="Loading clusters…" />;
  if (error) return <ErrorBox error={error} />;

  const th = (current?.thresholds ?? {}) as Record<string, number>;
  const criteria = current
    ? [
        { k: "Upstream rules", actual: current.rule_ids.join(", "), rule: "match_on.rule_ids", ok: true },
        ...("min_alerts" in th
          ? [{ k: "Alerts", actual: String(current.alert_ids.length), rule: `min_alerts ${th.min_alerts}`, ok: current.alert_ids.length >= th.min_alerts }]
          : []),
        ...("min_customers" in th
          ? [{ k: "Distinct customers", actual: String(current.customer_refs.length), rule: `min_customers ${th.min_customers}`, ok: current.customer_refs.length >= th.min_customers }]
          : []),
        ...("time_window_hours" in th
          ? [{ k: "Time span", actual: `${current.time_span_hours}h`, rule: `time_window_hours ${th.time_window_hours}`, ok: current.time_span_hours <= th.time_window_hours }]
          : []),
      ]
    : [];

  // The decision made for this exact membership, if any. A cluster whose
  // membership has changed has a different key and so shows as not routed.
  const record = current
    ? routing.data?.clusters?.find((r) => r.key === clusterKey(current.cluster_type, current.alert_ids)) ?? null
    : null;
  const clusterView = viewOf(routing.data, record);

  return (
    <div className="stack">
      <div>
        <h1>Clusters</h1>
        <p className="sub">
          {data?.alerts_clustered ?? 0} of {data?.alerts_considered ?? 0} alerts fall into{" "}
          {data?.clusters.length ?? 0} detected patterns at the configured thresholds. Clusters are recomputed
          from all alerts on every request and never stored. A cluster groups alerts; it does not establish
          wrongdoing.
        </p>
      </div>

      <div className="card table-card">
        {!data || data.clusters.length === 0 ? (
          <Empty title="No clusters found" hint="No group of alerts meets the knowledge-base thresholds right now." />
        ) : (
          <table className="queue">
            <thead>
              <tr>
                <th>Run ref</th>
                <th>Pattern</th>
                <th>Alerts</th>
                <th>Customers</th>
                <th>Programmes</th>
                <th>Span</th>
                <th>Window</th>
              </tr>
            </thead>
            <tbody>
              {data.clusters.map((c) => (
                <tr
                  key={c.cluster_ref}
                  className={`row-link is-cl${c.cluster_ref === selected ? " is-sel" : ""}`}
                  onClick={() => setSelected(c.cluster_ref)}
                >
                  <td>
                    <span className="id">{c.cluster_ref}</span>
                  </td>
                  <td>
                    <div className="cat">{c.label}</div>
                    <div className="rule mono">{c.rule_ids.join(" · ")}</div>
                  </td>
                  <td className="mono">{c.alert_ids.length}</td>
                  <td className="mono">{c.customer_refs.length}</td>
                  <td className="mono">{c.programme_refs.join(", ")}</td>
                  <td className="mono">{c.time_span_hours}h</td>
                  <td className="mono dim">≤ {String(c.thresholds.time_window_hours ?? "—")}h</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data?.note ? <div className="note">{data.note}</div> : null}
      </div>

      {current ? (
        <>
          {/* Cluster header */}
          <section className="card sec">
            <div className="head-top">
              <div className="head-id">
                <div className="chips">
                  <span className="pill pill-cl">
                    <span className="sw sw-cl" />
                    Cluster
                  </span>
                  <span className="dim mono">{`run reference ${current.cluster_ref} · stable only for identical input`}</span>
                  {clusterView.kind !== "none" ? (
                    <span className={`pill ${clusterView.kind === "destination" ? "pill-route" : "pill-closed"}`}>
                      {clusterView.kind === "destination" ? `Routed · ${clusterView.label}` : `Closed · ${clusterView.label}`}
                    </span>
                  ) : null}
                </div>
                <h1 className="head-title">{`Pattern: ${current.label}`}</h1>
              </div>
            </div>
            <dl className="facts">
              <div>
                <dt>Alerts</dt>
                <dd>{current.alert_ids.length}</dd>
              </div>
              <div>
                <dt>Customers</dt>
                <dd>{current.customer_refs.length}</dd>
              </div>
              <div>
                <dt>Programmes</dt>
                <dd className="mono">{current.programme_refs.join(", ")}</dd>
              </div>
              <div>
                <dt>Time span</dt>
                <dd>{current.time_span_hours}h</dd>
              </div>
              <div>
                <dt>Category</dt>
                <dd>{human(current.cluster_category)}</dd>
              </div>
            </dl>
          </section>

          {/* 1. Why these alerts were grouped */}
          <section className="card sec sec-sys">
            <SectionHead
              n={1}
              tone="sys"
              title="Why these alerts were grouped"
              right={<span className="dim">Deterministic detection criteria from the knowledge-base configuration</span>}
            />
            <div className="criteria">
              {criteria.map((c) => (
                <div key={c.k} className="crit">
                  <span className="crit-k">{c.k}</span>
                  <span className="crit-v">
                    <span>{c.actual}</span>
                    <span className="ok-mark">{c.ok ? "✓ met" : ""}</span>
                  </span>
                  <span className="mono dim">{c.rule}</span>
                </div>
              ))}
            </div>
          </section>

          {/* 2. AI Cluster Summary */}
          <ClusterSummaryCard key={current.alert_ids.join("|")} alertIds={current.alert_ids} n={2} />

          {/* 3. Member alerts */}
          <section className="card sec sec-ev">
            <SectionHead
              n={3}
              tone="ev"
              title={`Member alerts · ${current.alert_ids.length}`}
              right={<span className="dim">Each alert keeps its own investigation and evidence. Open one to see it.</span>}
            />
            <table className="queue">
              <thead>
                <tr>
                  <th>Member alert</th>
                  <th>Category</th>
                  <th>Customer</th>
                  <th>Detected</th>
                  <th>Destination</th>
                </tr>
              </thead>
              <tbody>
                {current.alert_ids.map((id) => {
                  const a = alertOf[id];
                  const s = inv[id];
                  const pending = !(id in alertOf);
                  const r = routingState(routing.data, id);
                  return (
                    <tr key={id} className="row-link" onClick={() => nav(`/alerts/${id}`)}>
                      <td className="id">{id}</td>
                      <td>
                        {s?.st === "ok" ? human(s.pack.category) : pending || s?.st === "loading" ? "…" : "Not available"}
                      </td>
                      <td className="mono">{pending ? "…" : (a?.customer_ref ?? "—")}</td>
                      <td className="mono dim">{a?.created_at ? fmtDetected(a.created_at) : pending ? "…" : "Not available"}</td>
                      <td>
                        {!routing.data ? (
                          <span className="dim">{routing.failed ? "Unavailable" : "…"}</span>
                        ) : r.kind === "none" ? (
                          <span className="dim">Not routed</span>
                        ) : (
                          <span className="dest">
                            <span className={`pill ${r.kind === "destination" ? "pill-route" : "pill-closed"}`}>{r.label}</span>
                            {r.via ? <span className="via">via cluster</span> : <span className="via">individually</span>}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>

          {/* 4. Investigation outcome / cluster routing */}
          <section className="card sec sec-sys">
            <SectionHead
              n={4}
              tone="sys"
              title="Investigation outcome"
              right={<span className="dim">The analyst decides · the AI does not route clusters</span>}
            />
            <RoutingPanel
              id={`cluster-${current.cluster_ref}`}
              noun="cluster"
              current={clusterView}
              routing={routing.data}
              failed={routing.failed}
              allowedOutcomes={clusterOutcomes}
              blockedReason="not every member alert's playbook permits it"
              note={`Routing the cluster applies to all ${current.alert_ids.length} current member alerts and takes precedence over their earlier individual routing. A member routed individually afterwards keeps its own routing.`}
              submit={async (body) => {
                await setClusterRouting(current.alert_ids, body);
                setRoutingRefresh((n) => n + 1);
              }}
            />
          </section>
        </>
      ) : null}
    </div>
  );
}
