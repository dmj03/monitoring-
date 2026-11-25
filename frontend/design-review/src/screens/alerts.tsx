import { alerts, clusterList, clusterOf, clusters, fmtTime, human, investigation, recent, SHOW } from "../data";
import { AppShell, Badge, Card, Prov, type OptionDef } from "../kit";
import { AlertFacts, ClusterContext, Counts, MissingPanel } from "../blocks";

const PAGE = recent(12);

function Filters({ extra = true }: { extra?: boolean }) {
  return (
    <div className="toolbar">
      <select className="inp"><option>All categories</option></select>
      <select className="inp"><option>All programmes</option></select>
      {extra ? <select className="inp"><option>Any evidence state</option><option>Has gaps</option></select> : null}
      {extra ? <select className="inp"><option>Any pattern</option><option>In a pattern</option></select> : null}
      <input className="inp" placeholder="Search alert, customer…" />
    </div>
  );
}

function AlertsA() {
  return (
    <AppShell active="alerts">
      <div className="stack">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div><h1 className="t">Alerts</h1><p className="lede">{alerts.length} alerts · newest first</p></div>
          <button className="btn p">+ Add alert</button>
        </div>
        <Filters />
        <div className="card">
          <table className="tb">
            <thead><tr><th>Alert</th><th>Category <Prov kind="computed" /></th><th>Customer</th><th>Programme</th><th>Pattern</th><th>Evidence <Prov kind="computed" /></th><th>Created</th></tr></thead>
            <tbody>
              {PAGE.map((a) => {
                const inv = investigation(a.alert_id);
                const c = clusterOf(a.alert_id);
                const g = inv.missing_evidence.length;
                return (
                  <tr key={a.alert_id}>
                    <td className="id">{a.alert_id}<div className="xs muted mono" style={{ fontWeight: 400 }}>{a.rule_id}</div></td>
                    <td>{human(inv.category)}</td>
                    <td className="mono">{a.customer_ref}</td>
                    <td className="mono">{a.programme_ref}</td>
                    <td>{c ? <Badge k="acc">{c.label.replace(" Pattern", "")}</Badge> : <span className="muted">—</span>}</td>
                    <td>{g ? <Badge k="warn">○ {g} missing</Badge> : <Badge k="ok">● complete</Badge>}</td>
                    <td className="sm muted">{fmtTime(a.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="row sm muted" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", justifyContent: "space-between" }}>
            <span>Showing 1–12 of {alerts.length}</span><span><button className="btn sm">‹</button> <button className="btn sm">›</button></span>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function AlertsB() {
  const sel = SHOW.gaps;
  const a = alerts.find((x) => x.alert_id === sel)!;
  const inv = investigation(sel);
  const list = [a, ...alerts.filter((x) => x.alert_id !== sel).slice(0, 9)];
  return (
    <AppShell active="alerts">
      <div className="stack">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h1 className="t">Alerts</h1><button className="btn p">+ Add alert</button>
        </div>
        <Filters extra={false} />
        <div className="split">
          <div className="l">
            {list.map((x) => {
              const i = investigation(x.alert_id);
              return (
                <div key={x.alert_id} className={`li${x.alert_id === sel ? " sel" : ""}`}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <b className="mono">{x.alert_id}</b>
                    {i.missing_evidence.length ? <Badge k="warn">○ {i.missing_evidence.length}</Badge> : null}
                  </div>
                  <div className="sm">{human(i.category)}</div>
                  <div className="xs muted mono">{x.customer_ref} · {x.programme_ref} · {fmtTime(x.created_at)}</div>
                </div>
              );
            })}
          </div>
          <div style={{ padding: 18 }} className="stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div><h2 className="s mono" style={{ fontSize: 18 }}>{a.alert_id}</h2><div className="lede">{human(inv.category)}</div></div>
              <button className="btn p">Open investigation →</button>
            </div>
            <AlertFacts a={a} />
            <Counts inv={inv} />
            <MissingPanel inv={inv} withKb={false} />
            <div><div className="eyebrow" style={{ marginBottom: 6 }}>Pattern</div><ClusterContext alertId={sel} compact /></div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function AlertsC() {
  const unclustered = alerts.filter((a) => !clusterList.membership[a.alert_id]);
  const byCat = new Map<string, typeof alerts>();
  for (const a of unclustered) {
    const c = investigation(a.alert_id).category;
    byCat.set(c, [...(byCat.get(c) ?? []), a]);
  }
  return (
    <AppShell active="alerts">
      <div className="stack">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div><h1 className="t">Alerts</h1><p className="lede">Grouped: detected patterns first, then by category</p></div>
          <button className="btn p">+ Add alert</button>
        </div>
        {clusters.slice(0, 2).map((c) => (
          <Card key={c.cluster_ref} title={<>{c.label} <span className="muted sm" style={{ fontWeight: 400 }}>· {c.alert_ids.length} alerts · {c.programme_refs.join(", ")}</span></>} right={<span className="xs muted">detected this run</span>}>
            <div className="chips">{c.alert_ids.map((id) => <Badge key={id} k="mute">{id}</Badge>)}</div>
          </Card>
        ))}
        <div className="xs muted">… {clusters.length - 2} more patterns</div>
        {[...byCat.entries()].slice(0, 3).map(([cat, list]) => (
          <Card key={cat} title={<>{human(cat)} <span className="muted sm" style={{ fontWeight: 400 }}>· {list.length} alerts</span></>}>
            <table className="tb"><tbody>{list.slice(0, 4).map((a) => {
              const g = investigation(a.alert_id).missing_evidence.length;
              return (
                <tr key={a.alert_id}><td className="id">{a.alert_id}</td><td className="mono">{a.customer_ref}</td><td className="mono">{a.programme_ref}</td>
                  <td>{g ? <Badge k="warn">○ {g} missing</Badge> : <Badge k="ok">● complete</Badge>}</td><td className="sm muted">{fmtTime(a.created_at)}</td></tr>
              );
            })}</tbody></table>
          </Card>
        ))}
      </div>
    </AppShell>
  );
}

const shared = [
  "Pattern column: available now from GET /clusters membership. Today's 'Cluster' column reads alert.cluster_ref, which is null for every stored alert, so it is always empty.",
  "Category + evidence columns: need N× POST /investigate, or FUTURE category/evidence_summary on GET /alerts.",
  "Server-side sorting (newest first) and filters by category / evidence state / pattern: FUTURE query parameters. GET /alerts filters only rule_id, programme_ref, customer_ref and returns dataset order.",
];

export const alertsScreens: OptionDef[] = [
  {
    id: "a", title: "A · Dense triage table", tagline: "scan many, decide where to look",
    notes: {
      structure: "Single table: alert + rule, category, customer, programme, pattern, evidence state, created. Filters above. Removes the 'Not investigated' column.",
      emphasis: "Which alerts have gaps and which belong to a pattern, at a glance.",
      pros: ["Familiar", "Highest throughput", "Fixes the always-empty Cluster column"],
      cons: ["Category/evidence columns cost N investigate calls without a backend change"],
      suits: "Analysts working a queue.",
      backend: shared, now: "Yes (with N× POST /investigate for two columns).",
    },
    render: () => <AlertsA />,
  },
  {
    id: "b", title: "B · List + preview", tagline: "inbox-style split view",
    notes: {
      structure: "Narrow list on the left, preview of the selected alert on the right: facts, evidence meter, gaps, pattern, link to the full investigation.",
      emphasis: "Triage without leaving the queue.",
      pros: ["Fast keyboard-style triage", "Preview answers 'do I need to open this?'"],
      cons: ["Less room for columns", "Needs an investigation per selection (cheap, deterministic)"],
      suits: "Analysts reviewing alerts one after another.",
      backend: shared, now: "Yes.",
    },
    render: () => <AlertsB />,
  },
  {
    id: "c", title: "C · Grouped by pattern, then category", tagline: "organised, not flat",
    notes: {
      structure: "Detected patterns as groups at the top, remaining alerts grouped by category.",
      emphasis: "Relationships between alerts.",
      pros: ["Makes clustering central", "Could replace a separate Clusters page"],
      cons: ["Grouping changes whenever clusters recompute", "Harder to page and filter", "Can imply clusters are stable containers"],
      suits: "Pattern-oriented review; demos.",
      backend: shared, now: "Yes.",
    },
    render: () => <AlertsC />,
  },
];
