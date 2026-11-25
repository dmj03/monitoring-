import { alertById, clusterList, clusterOf, clusters, fmtTime, human, investigation, kb, SHOW } from "../data";
import { AppShell, Badge, Card, Prov, type OptionDef } from "../kit";
import { AlertFacts, ClusterContext, ClusterTimeline, Thresholds } from "../blocks";

function Members({ ids }: { ids: string[] }) {
  return (
    <table className="tb">
      <thead><tr><th>Alert</th><th>Customer</th><th>Created</th><th>Evidence</th></tr></thead>
      <tbody>{ids.map((id) => {
        const a = alertById(id);
        const g = investigation(id).missing_evidence.length;
        return (
          <tr key={id}><td className="id">{id}</td><td className="mono">{a.customer_ref}</td><td className="sm muted">{fmtTime(a.created_at)}</td>
            <td>{g ? <Badge k="warn">○ {g} missing</Badge> : <Badge k="ok">● complete</Badge>}</td></tr>
        );
      })}</tbody>
    </table>
  );
}

// A — simplified separate page: one card per detected pattern
function ClA() {
  return (
    <AppShell active="clusters">
      <div className="stack">
        <div>
          <h1 className="t">Patterns detected now</h1>
          <p className="lede">{clusterList.alerts_clustered} of {clusterList.alerts_considered} alerts fall into {clusters.length} patterns. Recomputed from current alerts every time this page loads — nothing is stored.</p>
        </div>
        {clusters.slice(0, 2).map((c) => (
          <Card key={c.cluster_ref} title={<>{c.label} <span className="sm muted" style={{ fontWeight: 400 }}>· {human(c.cluster_category)}</span></>}
            right={<span className="xs muted">run ref {c.cluster_ref}</span>}>
            <div className="grid2" style={{ gridTemplateColumns: "minmax(0,1.1fr) minmax(0,1fr)" }}>
              <div className="stack">
                <ClusterTimeline c={c} />
                <Thresholds c={c} />
                <div className="sm" style={{ color: "var(--text-2)" }}>{kb.clusters[c.cluster_type].purpose} <Prov kind="future" /></div>
              </div>
              <Members ids={c.alert_ids} />
            </div>
          </Card>
        ))}
        <div className="xs muted">… {clusters.length - 2} more</div>
      </div>
    </AppShell>
  );
}

// B — cluster context only inside Alert Detail; no Clusters page
function ClB() {
  const id = SHOW.clustered;
  const a = alertById(id);
  return (
    <AppShell active="alerts" nav={[["alerts", "Alerts", "≡"]]}>
      <div className="stack">
        <div className="banner info sm">No Clusters page. Pattern context appears on each member alert, and the Alerts list can filter "in a pattern".</div>
        <h1 className="t">{id} <span style={{ fontWeight: 400, color: "var(--text-2)" }}>· {human(investigation(id).category)}</span></h1>
        <AlertFacts a={a} />
        <Card title="Part of a detected pattern" right={<Prov kind="computed" />}>
          <div className="stack">
            <ClusterContext alertId={id} interp />
            <details className="tech"><summary>Other alerts in this pattern</summary><Members ids={clusterOf(id)!.alert_ids.filter((x) => x !== id)} /></details>
          </div>
        </Card>
        <div className="sm muted">… investigation continues below</div>
      </div>
    </AppShell>
  );
}

// D — four cluster types as a monitoring board
function ClD() {
  const types = Object.keys(kb.clusters);
  return (
    <AppShell active="clusters">
      <div className="stack">
        <div><h1 className="t">Pattern monitor</h1><p className="lede">The four configured pattern types and whether each is present in the current alerts.</p></div>
        <div className="grid2">
          {types.map((t) => {
            const found = clusters.filter((c) => c.cluster_type === t);
            const k = kb.clusters[t];
            return (
              <Card key={t} title={human(t)} right={found.length ? <Badge k="acc">{found.length} detected</Badge> : <Badge k="mute">none now</Badge>}>
                <div className="stack">
                  <div className="sm" style={{ color: "var(--text-2)" }}>{k.purpose} <Prov kind="future" /></div>
                  {found.map((c) => (
                    <div key={c.cluster_ref} className="stack" style={{ gap: 6 }}>
                      <ClusterTimeline c={c} />
                      <Thresholds c={c} />
                    </div>
                  ))}
                </div>
              </Card>
            );
          })}
        </div>
      </div>
    </AppShell>
  );
}

const be = [
  "All membership, thresholds, spans and members: available now from GET /clusters (+ GET /alerts for created_at/customer).",
  "Pattern purpose / interpretation / analyst guidance / boundaries text: FUTURE BACKEND FIELD — exists in UC3_Monitoring_KB/clusters/*.yaml but is not returned by the API.",
  "No durable IDs and no GET /clusters/{id}: every design shows the run ref as secondary text, or not at all.",
];

export const clusterScreens: OptionDef[] = [
  {
    id: "a", title: "A · Separate page, simplified", tagline: "one card per detected pattern",
    notes: {
      structure: "Each detected pattern is a card: time strip, threshold check, purpose, and member table with customer and evidence state. Run ref shown small, labelled 'run ref'.",
      emphasis: "Why each pattern formed and who is in it.",
      pros: ["Fixes today's master/detail split — everything visible", "Explains formation against thresholds", "Honest about recomputation"],
      cons: ["Still a separate place to visit", "Purpose text needs a backend field"],
      suits: "Pattern review sessions.",
      backend: be, now: "Yes, except the purpose text.",
    },
    render: () => <ClA />,
  },
  {
    id: "b", title: "B · Inside Alert Detail only", tagline: "no Clusters page",
    notes: {
      structure: "Remove the Clusters page. Each member alert shows a pattern panel (time strip, thresholds, interpretation, other members). Alerts list gets an 'in a pattern' filter.",
      emphasis: "Pattern as context for an individual investigation.",
      pros: ["Context where the analyst needs it", "Avoids any impression that clusters are standalone records"],
      cons: ["No single place to see all patterns", "Pattern-level review becomes awkward"],
      suits: "Alert-by-alert triage.",
      backend: be, now: "Yes.",
    },
    render: () => <ClB />,
  },
  {
    id: "c", title: "C · Both", tagline: "page + context",
    notes: {
      structure: "Option A's page AND option B's panel on Alert Detail, linked both ways (panel → page anchors by cluster type, not by run ref).",
      emphasis: "Both views of the same computed result.",
      pros: ["Covers both workflows"],
      cons: ["Two places to keep consistent", "Link from detail to page must not rely on cluster_ref being stable — link by type"],
      suits: "Mixed teams.",
      backend: be, now: "Yes.",
    },
    render: () => <div className="grid2" style={{ gap: 0 }}><ClA /><ClB /></div>,
  },
  {
    id: "d", title: "D · Pattern monitor", tagline: "four types as a board",
    notes: {
      structure: "Fixed board of the four configured pattern types; each shows whether it is present now, with time strip and thresholds.",
      emphasis: "Monitoring the pattern types, not browsing clusters.",
      pros: ["Stable layout (types are fixed, clusters are not)", "Shows a type with 'none now' — useful once data changes", "Could double as the Overview"],
      cons: ["Members one click deeper", "In the current data all four types are present, so the 'none' state isn't demonstrated"],
      suits: "Supervisors; Overview replacement.",
      backend: be, now: "Yes, except purpose text.",
    },
    render: () => <ClD />,
  },
];
