import { clusterOf, investigation, SHOW, human } from "../data";
import { Badge, Card, EvStatus, EvValue, Frame, Prov, THEMES, type OptionDef, type ThemeId } from "../kit";
import { ClusterTimeline, Thresholds } from "../blocks";

const DIRECTION: Record<ThemeId, { name: string; idea: string; density: string; type: string }> = {
  enterprise: {
    name: "A — Enterprise monitoring dashboard",
    idea: "Light, neutral, dense tables, dark navigation rail. Familiar to compliance teams using case-management tools.",
    density: "High — 8px rhythm, zebra rows, compact badges",
    type: "Segoe UI throughout; numbers in Consolas",
  },
  aiops: {
    name: "B — Modern AI operations console",
    idea: "Soft surfaces, large radius, elevated cards, a distinct violet reserved for AI content. Reads as a current product.",
    density: "Medium-low — 12px rhythm, generous cards",
    type: "Segoe UI with semibold headings",
  },
  minimal: {
    name: "C — Minimal analyst workspace",
    idea: "Paper-like, almost no chrome, top navigation, serif headings. Optimised for reading an investigation, not scanning many.",
    density: "Low — 14px rhythm, hairline rules instead of cards",
    type: "Georgia headings, Segoe UI body",
  },
  soc: {
    name: "D — Dark SOC-style console",
    idea: "Near-black, high-contrast status colour, monospace data, icon rail. Signals 'operations centre'.",
    density: "Very high — 6px rhythm, mono tables",
    type: "Consolas headings and data, Segoe UI prose",
  },
};

function Board({ theme }: { theme: ThemeId }) {
  const inv = investigation(SHOW.gaps);
  const found = inv.evidence.find((e) => e.status === "FOUND")!;
  const miss = inv.evidence.find((e) => e.status === "MISSING")!;
  const d = DIRECTION[theme];
  return (
    <Frame theme={theme}>
      <div className="inner" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
        <div>
          <div style={{ fontFamily: "var(--head)", fontWeight: 600, fontSize: 15 }}>{d.name}</div>
          <div className="sm" style={{ color: "var(--text-2)", marginTop: 4 }}>{d.idea}</div>
        </div>
        <div>
          <div className="ds-h">Navigation</div>
          <div className="side" style={{ borderRadius: "var(--radius)", padding: 8 }}>
            <a className="on" style={{ padding: "6px 10px" }}><span className="ico">≡</span>Alerts</a>
            <a style={{ padding: "6px 10px" }}><span className="ico">⋰</span>Clusters</a>
          </div>
        </div>
        <div>
          <div className="ds-h">Typography</div>
          <h1 className="t">ALRT-0073</h1>
          <div className="lede">Linked party or counterparty review</div>
          <div className="xs muted" style={{ marginTop: 4 }}>{d.type}</div>
        </div>
        <div>
          <div className="ds-h">Stat card</div>
          <div className="card"><div className="bd stat"><div className="n">11</div><div className="l">alerts with evidence gaps</div></div></div>
        </div>
        <div>
          <div className="ds-h">Table · density: {d.density}</div>
          <div className="card" style={{ overflow: "hidden" }}>
            <table className="tb">
              <thead><tr><th>Evidence</th><th>Status</th><th>Value</th></tr></thead>
              <tbody>
                <tr><td>{human(found.key)}</td><td><EvStatus e={found} /></td><td><EvValue e={found} /></td></tr>
                <tr><td>{human(miss.key)}</td><td><EvStatus e={miss} /></td><td><EvValue e={miss} /></td></tr>
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <div className="ds-h">Status badges</div>
          <div className="chips">
            <Badge k="ok">● Found</Badge><Badge k="warn">○ Missing</Badge><Badge k="acc">Repeated velocity</Badge>
            <Badge k="mute">TM-VEL-014</Badge><Badge k="outline">Escalate to mlro</Badge><Badge k="ai">✦ AI planned</Badge>
          </div>
        </div>
        <div>
          <div className="ds-h">Provenance labels</div>
          <div className="chips"><Prov kind="engine" /><Prov kind="kb" /><Prov kind="ai" /><Prov kind="future" /></div>
        </div>
        <div>
          <div className="ds-h">Actions</div>
          <div className="row"><button className="btn p">Investigate alert</button><button className="btn">Add alert</button><button className="btn ghost">Technical details</button></div>
        </div>
        <div>
          <div className="ds-h">AI content container</div>
          <div className="ai-box" style={{ padding: 10 }}>
            <div className="sm" style={{ color: "var(--ai)", fontWeight: 600 }}>✦ AI summary — planned</div>
            <div className="skel" style={{ width: "90%" }} /><div className="skel" style={{ width: "70%" }} />
          </div>
        </div>
      </div>
    </Frame>
  );
}

function MissingVariants({ theme }: { theme: ThemeId }) {
  const inv = investigation(SHOW.gaps);
  const e = inv.evidence.find((x) => x.status === "MISSING")!;
  return (
    <Frame theme={theme}>
      <div style={{ padding: 16 }} className="grid4">
        <Card title="M1 · Badge + dash (current app)">
          <div className="row"><Badge k="warn">MISSING</Badge><span className="muted">—</span></div>
          <div className="xs muted" style={{ marginTop: 8 }}>Compact. Risk: "—" can be overlooked.</div>
        </Card>
        <Card title="M2 · Dashed gap tile">
          <div className="ev-tile missing"><div className="k">{human(e.key)}</div><div className="v">Not in source data</div></div>
          <div className="xs muted" style={{ marginTop: 8 }}>Gap is visible as a hole in the grid. Takes space.</div>
        </Card>
        <Card title="M3 · Explicit sentence">
          <span className="ev-gap">○ Not found in source data — recorded as a finding</span>
          <div className="xs muted" style={{ marginTop: 8 }}>Unambiguous wording. Verbose in tables.</div>
        </Card>
        <Card title="M4 · Icon pair, legend once">
          <div className="row"><span style={{ color: "var(--ok)" }}>●</span> found <span style={{ color: "var(--warn)", marginLeft: 12 }}>○</span> missing</div>
          <div className="xs muted" style={{ marginTop: 8 }}>Densest. Relies on a legend; weakest for accessibility.</div>
        </Card>
      </div>
      <div className="xs muted" style={{ padding: "0 16px 14px" }}>
        All four keep MISSING distinct from false / failed / 0, and never show a value for it. Red is deliberately not used: red reads as failure.
      </div>
    </Frame>
  );
}

function ClusterVariants({ theme }: { theme: ThemeId }) {
  const c = clusterOf(SHOW.clustered)!;
  return (
    <Frame theme={theme}>
      <div style={{ padding: 16 }} className="grid4">
        <Card title="V1 · Time strip">
          <ClusterTimeline c={c} me={SHOW.clustered} />
        </Card>
        <Card title="V2 · Threshold check">
          <Thresholds c={c} />
          <div className="xs muted" style={{ marginTop: 8 }}>Shows why it formed, against KB thresholds.</div>
        </Card>
        <Card title="V3 · Member grid">
          <div className="chips">{c.alert_ids.map((id) => <Badge key={id} k={id === SHOW.clustered ? "acc" : "mute"}>{id}</Badge>)}</div>
        </Card>
        <Card title="V4 · Sentence only">
          <div className="sm">Part of a <b>{c.label.toLowerCase()}</b>: {c.alert_ids.length} alerts, {c.customer_refs.length} customers, {c.programme_refs.join(", ")}, within {c.time_span_hours}h.</div>
        </Card>
      </div>
    </Frame>
  );
}

const common = {
  backend: ["None. Visual direction is presentation only."],
  now: "Fully implementable with the current API.",
};

export const designSystem: OptionDef[] = [
  {
    id: "directions",
    title: "Four directions",
    tagline: "same content, four visual systems",
    notes: {
      structure: "Each column renders identical real content (ALRT-0073) in one visual system: navigation, type, stat card, table, badges, provenance labels, actions and the AI container.",
      emphasis: "Compare tone, density and how clearly MISSING and AI-planned content stand out.",
      pros: ["Isolates the style decision from the layout decision", "Every other screen can be viewed in any direction via the switcher at top right"],
      cons: ["Small samples understate how density feels across a full page — check full screens too"],
      suits: "Deciding the look before layouts.",
      ...common,
    },
    ownFrame: true,
    render: () => <div className="ds-board">{THEMES.map((t) => <div key={t.id}><Board theme={t.id} /></div>)}</div>,
  },
  {
    id: "missing",
    title: "MISSING state",
    tagline: "4 ways to show a gap",
    notes: {
      structure: "Four renderings of the same MISSING evidence item, in the currently selected direction.",
      emphasis: "A gap must be noticed, and must not read as false, failed, error or zero.",
      pros: ["M2/M3 make the gap impossible to miss", "M1/M4 keep tables compact"],
      cons: ["M3 is wordy in a 10-row table", "M4 depends on a legend"],
      suits: "All users; this is a correctness question as much as a visual one.",
      ...common,
    },
    ownFrame: true,
    render: (theme) => <MissingVariants theme={theme} />,
  },
  {
    id: "cluster-viz",
    title: "Cluster visualisation",
    tagline: "4 ways to show a pattern",
    notes: {
      structure: "Four renderings of cluster context for ALRT-0001 (member of a repeated-velocity pattern).",
      emphasis: "Show that the alert is part of a pattern, why it formed, and that the grouping is computed per run.",
      pros: ["V1 shows the time window concretely", "V2 explains formation with KB thresholds", "V4 costs no space"],
      cons: ["V1 needs created_at for every member (available via GET /alerts)", "V3 says little on its own"],
      suits: "V1/V2 for analysts, V4 for a summary line.",
      ...common,
    },
    ownFrame: true,
    render: (theme) => <ClusterVariants theme={theme} />,
  },
];
