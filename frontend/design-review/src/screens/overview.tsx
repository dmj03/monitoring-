import {
  alerts, byCategory, clusterList, clusters, fmtTime, gapCount, human, investigation, programmes, recent,
} from "../data";
import { AppShell, Badge, Card, Prov, type OptionDef } from "../kit";
import { ClusterTimeline } from "../blocks";

const gaps = alerts.filter((a) => gapCount(a.alert_id) > 0);

function NeedsAttention({ limit = 6 }: { limit?: number }) {
  return (
    <table className="tb">
      <thead><tr><th>Alert</th><th>Category</th><th>Missing evidence</th><th>Created</th></tr></thead>
      <tbody>
        {gaps.slice(0, limit).map((a) => {
          const inv = investigation(a.alert_id);
          return (
            <tr key={a.alert_id}>
              <td className="id">{a.alert_id}</td>
              <td>{human(inv.category)}</td>
              <td><div className="chips">{inv.missing_evidence.map((k) => <Badge key={k} k="warn">○ {human(k)}</Badge>)}</div></td>
              <td className="sm muted">{fmtTime(a.created_at)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function PatternStrip() {
  return (
    <div className="grid4">
      {clusters.map((c) => (
        <div key={c.cluster_ref} className="card">
          <div className="bd">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b className="sm">{c.label}</b>
              <Badge k="acc">{c.alert_ids.length} alerts</Badge>
            </div>
            <div className="xs muted" style={{ margin: "4px 0 8px" }}>
              {c.customer_refs.length} customers · {c.programme_refs.join(", ")} · {c.time_span_hours}h
            </div>
            <ClusterTimeline c={c} />
          </div>
        </div>
      ))}
    </div>
  );
}

function OverviewA() {
  return (
    <AppShell active="overview">
      <div className="stack">
        <div>
          <h1 className="t">Where to start</h1>
          <p className="lede">{alerts.length} alerts in the dataset · {clusters.length} patterns detected in this run · {gaps.length} alerts with evidence gaps</p>
        </div>
        <Card title={<>Needs attention — evidence gaps</>} right={<><Prov kind="computed" /><button className="btn sm">View all {gaps.length}</button></>}>
          <NeedsAttention />
        </Card>
        <Card title="Patterns detected now" right={<span className="xs muted">recomputed on every load · not stored</span>}>
          <PatternStrip />
        </Card>
        <Card title="Most recent alerts">
          <table className="tb">
            <thead><tr><th>Alert</th><th>Rule</th><th>Customer</th><th>Created</th></tr></thead>
            <tbody>{recent(5).map((a) => (
              <tr key={a.alert_id}><td className="id">{a.alert_id}</td><td className="mono">{a.rule_id}</td><td className="mono">{a.customer_ref}</td><td className="sm muted">{fmtTime(a.created_at)}</td></tr>
            ))}</tbody>
          </table>
        </Card>
      </div>
    </AppShell>
  );
}

function OverviewB() {
  const cats = byCategory();
  const max = cats[0][1];
  const perProg = programmes.map((p) => [p, alerts.filter((a) => a.programme_ref === p).length] as const);
  return (
    <AppShell active="overview">
      <div className="stack">
        <h1 className="t">Monitoring overview</h1>
        <div className="grid4">
          {[
            [alerts.length, "alerts in dataset"],
            [`${clusterList.alerts_clustered}`, `alerts in detected patterns (${Math.round((clusterList.alerts_clustered / alerts.length) * 100)}%)`],
            [gaps.length, "alerts with evidence gaps"],
            [programmes.length, "programmes"],
          ].map(([n, l]) => (
            <div key={String(l)} className="card"><div className="bd stat"><div className="n">{n}</div><div className="l">{l}</div></div></div>
          ))}
        </div>
        <div className="grid2">
          <Card title="Alerts by category" right={<Prov kind="computed" />}>
            <div className="stack" style={{ gap: 7 }}>
              {cats.map(([c, n]) => (
                <div key={c} style={{ display: "grid", gridTemplateColumns: "230px 1fr 26px", gap: 8, alignItems: "center" }}>
                  <span className="sm">{human(c)}</span>
                  <div className="bar" style={{ width: `${(n / max) * 100}%` }} />
                  <span className="sm mono">{n}</span>
                </div>
              ))}
            </div>
          </Card>
          <div className="stack">
            <Card title="Alerts by programme">
              <div className="stack" style={{ gap: 7 }}>
                {perProg.map(([p, n]) => (
                  <div key={p} style={{ display: "grid", gridTemplateColumns: "50px 1fr 26px", gap: 8, alignItems: "center" }}>
                    <span className="mono sm">{p}</span><div className="bar" style={{ width: `${(n / 30) * 100}%`, background: "var(--text-3)" }} /><span className="sm mono">{n}</span>
                  </div>
                ))}
              </div>
            </Card>
            <Card title="Detected patterns">
              <table className="tb"><tbody>{clusters.map((c) => (
                <tr key={c.cluster_ref}><td>{c.label}</td><td className="mono">{c.alert_ids.length}</td><td className="mono">{c.programme_refs.join(", ")}</td></tr>
              ))}</tbody></table>
            </Card>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function OverviewC() {
  return (
    <AppShell active="alerts" nav={[["alerts", "Alerts", "≡"], ["clusters", "Clusters", "⋰"]]}>
      <div className="stack">
        <div className="banner info sm">Overview removed. The app opens on the alert queue; the headline numbers move into a one-line strip.</div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h1 className="t">Alerts</h1>
          <button className="btn p">+ Add alert</button>
        </div>
        <div className="row sm" style={{ gap: 18, padding: "8px 12px", border: "1px solid var(--border)", borderRadius: "var(--radius)", background: "var(--surface)" }}>
          <span><b>{alerts.length}</b> alerts</span>
          <span><b>{clusterList.alerts_clustered}</b> in {clusters.length} patterns</span>
          <span style={{ color: "var(--warn)" }}><b>{gaps.length}</b> with evidence gaps</span>
          <span className="right xs muted">patterns recomputed on load</span>
        </div>
        <div className="card"><table className="tb">
          <thead><tr><th>Alert</th><th>Category</th><th>Customer</th><th>Pattern</th><th>Evidence</th><th>Created</th></tr></thead>
          <tbody>{alerts.slice(0, 8).map((a) => {
            const inv = investigation(a.alert_id);
            const g = inv.missing_evidence.length;
            return (
              <tr key={a.alert_id}>
                <td className="id">{a.alert_id}</td><td>{human(inv.category)}</td><td className="mono">{a.customer_ref}</td>
                <td>{clusterList.membership[a.alert_id] ? <Badge k="acc">in pattern</Badge> : <span className="muted">—</span>}</td>
                <td>{g ? <Badge k="warn">{g} missing</Badge> : <Badge k="ok">complete</Badge>}</td>
                <td className="sm muted">{fmtTime(a.created_at)}</td>
              </tr>
            );
          })}</tbody>
        </table></div>
      </div>
    </AppShell>
  );
}

const gapBackend = "Evidence-gap counts need one POST /investigate per alert (90 calls today). Works now, but a FUTURE GET /alerts?include=evidence_summary (or a batch endpoint) would make it one call.";

export const overview: OptionDef[] = [
  {
    id: "a", title: "A · Triage home", tagline: "what needs attention now",
    notes: {
      structure: "One-line context sentence, then three blocks: alerts with evidence gaps, patterns detected now (with time strips), most recent alerts.",
      emphasis: "Actionable items. Gaps first, patterns second, totals reduced to a sentence.",
      pros: ["Every block leads somewhere useful", "Puts the POC's two strengths — MISSING evidence and clustering — on the first screen", "No vanity numbers"],
      cons: ["Gap list requires investigating all alerts up front", "Less 'dashboard' feel for demos"],
      suits: "Analysts starting a shift.",
      backend: [gapBackend],
      now: "Yes. Patterns and recency from GET /clusters + GET /alerts. Gaps via N× POST /investigate.",
    },
    render: () => <OverviewA />,
  },
  {
    id: "b", title: "B · Monitoring dashboard", tagline: "distribution and volume",
    notes: {
      structure: "Four KPI tiles, alerts-by-category bars, alerts-by-programme bars, detected-pattern list.",
      emphasis: "Shape of the alert population.",
      pros: ["Strong demo/executive impression", "Category mix is genuinely informative across 10 playbooks"],
      cons: ["Little to act on", "Category per alert is not on GET /alerts — needs investigate per alert or rule→category mapping"],
      suits: "Managers, stakeholders, demos.",
      backend: [gapBackend, "FUTURE BACKEND FIELD: category on the alert list (KB routing already knows it), or a GET /stats endpoint."],
      now: "Yes, with N× POST /investigate for categories and gaps.",
    },
    render: () => <OverviewB />,
  },
  {
    id: "c", title: "C · Remove Overview", tagline: "open on the queue",
    notes: {
      structure: "No Overview page. App opens on Alerts; headline numbers become a single strip above the table.",
      emphasis: "The queue is the product.",
      pros: ["One fewer page to maintain", "Fastest route to work", "Avoids duplicating the Alerts table, as today's Overview does"],
      cons: ["No landing summary for demos", "Pattern overview must then live on Clusters"],
      suits: "Daily analyst use.",
      backend: [gapBackend],
      now: "Yes.",
    },
    render: () => <OverviewC />,
  },
];
