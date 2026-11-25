import { human, kb } from "../data";
import { AppShell, Badge, Card, Prov, type OptionDef } from "../kit";
import {
  AiSummary, AlertFacts, Checklist, Classification, ClusterContext, Counts, EvidenceTable, MissingPanel,
  Outcomes, Technical, useInv,
} from "../blocks";
import { EvStatus, EvValue } from "../kit";

function Header({ id, right }: { id: string; right?: React.ReactNode }) {
  const { a, inv } = useInv(id);
  return (
    <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
      <div>
        <div className="crumbs">Alerts / <b>{a.alert_id}</b></div>
        <h1 className="t" style={{ marginTop: 4 }}>{a.alert_id} <span style={{ fontWeight: 400, color: "var(--text-2)" }}>· {human(inv.category)}</span></h1>
      </div>
      <div className="row">{right ?? <><button className="btn">Re-run investigation</button></>}</div>
    </div>
  );
}

// A — Investigation-first
function DetailA({ id }: { id: string }) {
  const { a, inv } = useInv(id);
  return (
    <AppShell active="alerts">
      <div className="stack">
        <Header id={id} />
        <div className="card"><div className="bd"><AlertFacts a={a} /></div></div>
        <AiSummary alertId={id} />
        <div className="grid2">
          <Card title="Classification" right={<Prov kind="engine" />}><Classification inv={inv} /></Card>
          <Card title="Pattern context" right={<Prov kind="computed" />}><ClusterContext alertId={id} compact /></Card>
        </div>
        <Card title="Evidence" right={<Counts inv={inv} />}>
          <div className="stack">
            <MissingPanel inv={inv} />
            <EvidenceTable inv={inv} source={false} onlyMissingFirst />
          </div>
        </Card>
        <Card title="Analyst review">
          <div className="grid2">
            <div><div className="eyebrow" style={{ marginBottom: 6 }}>Checklist</div><Checklist inv={inv} /></div>
            <div><div className="eyebrow" style={{ marginBottom: 6 }}>Permitted outcomes</div><Outcomes inv={inv} /></div>
          </div>
        </Card>
        <Technical inv={inv} />
      </div>
    </AppShell>
  );
}

// B — Analyst workspace (3 columns)
function DetailB({ id }: { id: string }) {
  const { a, inv } = useInv(id);
  return (
    <AppShell active="alerts">
      <div className="stack">
        <Header id={id} />
        <div className="ws">
          <div className="stack">
            <Card title="Alert"><AlertFacts a={a} layout="list" /></Card>
            <Card title="Pattern"><ClusterContext alertId={id} compact /></Card>
          </div>
          <div className="stack">
            <Card title="Classification" right={<Prov kind="engine" />}><Classification inv={inv} /></Card>
            <Card title="Evidence" right={<Counts inv={inv} />}>
              <EvidenceTable inv={inv} source={false} />
            </Card>
            <Technical inv={inv} />
          </div>
          <div className="stack">
            <AiSummary alertId={id} mode="skeleton" title="AI summary — planned" recommend={false} />
            <Card title="Review checklist"><Checklist inv={inv} /></Card>
            <Card title="Decision">
              <Outcomes inv={inv} note={false} />
              <textarea className="inp" rows={3} placeholder="Analyst rationale…" style={{ marginTop: 10 }} disabled />
              <div className="row" style={{ marginTop: 8 }}><button className="btn p" disabled>Record decision</button><Prov kind="future" /></div>
            </Card>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

// C — Evidence-first
function DetailC({ id }: { id: string }) {
  const { a, inv } = useInv(id);
  return (
    <AppShell active="alerts">
      <div className="stack">
        <Header id={id} />
        <div className="row sm" style={{ gap: 18, color: "var(--text-2)" }}>
          <span className="mono">{a.rule_id}</span><span className="mono">{a.customer_ref}</span><span className="mono">{a.programme_ref}</span>
          <span>{inv.playbook_id}</span><Counts inv={inv} />
        </div>
        <div className="ev-grid">
          {inv.evidence.map((e) => (
            <div key={e.key} className={`ev-tile${e.status === "MISSING" ? " missing" : ""}`}>
              <div className="row" style={{ justifyContent: "space-between" }}><span className="k">{human(e.key)}</span><EvStatus e={e} /></div>
              <div className="v"><EvValue e={e} /></div>
              <div className="src" style={{ marginTop: 6 }} title={`${e.tool}.${e.field}`}>{e.tool}.{e.field}</div>
            </div>
          ))}
        </div>
        <div className="grid2">
          <Card title="What to establish"><div className="sm" style={{ color: "var(--text-2)", marginBottom: 8 }}>{inv.investigation_focus}</div><Checklist inv={inv} /></Card>
          <div className="stack">
            <Card title="Pattern"><ClusterContext alertId={id} compact /></Card>
            <AiSummary alertId={id} mode="skeleton" recommend={false} />
          </div>
        </div>
        <Technical inv={inv} />
      </div>
    </AppShell>
  );
}

// D — Executive summary
function DetailD({ id }: { id: string }) {
  const { inv } = useInv(id);
  const p = kb.playbooks[inv.playbook_id];
  return (
    <AppShell active="alerts">
      <div className="stack" style={{ maxWidth: 860 }}>
        <Header id={id} right={<Badge k="mute">{inv.rule_id}</Badge>} />
        <p className="lede" style={{ fontSize: 15 }}>{p.description} <Prov kind="future" /></p>
        <AiSummary alertId={id} />
        <div className="grid3">
          <div className="card"><div className="bd stat"><div className="n">{inv.evidence.length - inv.missing_evidence.length}/{inv.evidence.length}</div><div className="l">evidence items found</div></div></div>
          <div className="card"><div className="bd stat"><div className="n" style={{ color: inv.missing_evidence.length ? "var(--warn)" : undefined }}>{inv.missing_evidence.length}</div><div className="l">gaps recorded</div></div></div>
          <div className="card"><div className="bd stat"><div className="n">{inv.allowed_outcomes.length}</div><div className="l">permitted outcomes</div></div></div>
        </div>
        <MissingPanel inv={inv} withKb={false} />
        <Card title="Pattern"><ClusterContext alertId={id} compact /></Card>
        <details className="tech"><summary>Full evidence ({inv.evidence.length} items)</summary><div style={{ marginTop: 8 }}><EvidenceTable inv={inv} /></div></details>
        <details className="tech"><summary>Playbook checklist and guidance</summary><div style={{ marginTop: 8 }}><Checklist inv={inv} /><p className="sm muted">{inv.escalation_guidance}</p></div></details>
        <Technical inv={inv} />
      </div>
    </AppShell>
  );
}

const pattern = "Pattern context: available now from GET /clusters membership (fixes today's always-empty Cluster field).";
const decision = "Recording an analyst decision / rationale: FUTURE endpoint (e.g. POST /alerts/{id}/decision) and a persistence decision — not in the current architecture.";
const ai = "AI summary: FUTURE backend — an endpoint that sends build_llm_input() to a model and returns text with evidence citations. Not built.";

export const alertDetail: OptionDef[] = [
  {
    id: "a", title: "A · Investigation-first", tagline: "summary on top, technical at the bottom", usesAlert: true,
    notes: {
      structure: "Header → alert facts strip → AI summary (planned) → classification + pattern → evidence with gaps first → checklist + outcomes → collapsed technical details.",
      emphasis: "The story of the investigation, top to bottom, in the order the engine produces it.",
      pros: ["Mirrors the pipeline Alert → Pattern → Playbook → Evidence → Summary → Review", "Technical noise collapsed", "Works without AI: the AI card simply isn't rendered"],
      cons: ["Long page", "Summary on top risks being read before evidence"],
      suits: "Analysts new to the case; reviewers; demos.",
      backend: [pattern, ai],
      now: "Everything except the AI card and the 'bears on outcome' flag.",
    },
    render: (_t, id) => <DetailA id={id} />,
  },
  {
    id: "b", title: "B · Analyst workspace", tagline: "context | investigation | review", usesAlert: true,
    notes: {
      structure: "Three columns: alert + pattern (left), classification + evidence (centre), AI + checklist + decision (right).",
      emphasis: "Working the case: everything visible at once.",
      pros: ["Feels like a real case-management tool", "Decision area makes the human-review step explicit"],
      cons: ["Needs a wide screen", "Decision panel has nothing to submit to yet — must be visibly disabled", "Busy"],
      suits: "Experienced analysts on large monitors.",
      backend: [pattern, decision, ai],
      now: "Layout and all engine content yes; decision recording no.",
    },
    render: (_t, id) => <DetailB id={id} />,
  },
  {
    id: "c", title: "C · Evidence-first", tagline: "evidence grid dominates", usesAlert: true,
    notes: {
      structure: "Compact facts line, then a tile per evidence item (gaps as dashed tiles), then focus/checklist and pattern, AI secondary.",
      emphasis: "The facts the engine gathered. Gaps are visual holes.",
      pros: ["Most honest framing: evidence is the source of truth", "MISSING is impossible to overlook", "AI clearly subordinate"],
      cons: ["Less narrative", "Tiles get long for list values (transaction_records)"],
      suits: "Evidence-driven analysts; audit/QA.",
      backend: [pattern, ai],
      now: "Yes, fully.",
    },
    render: (_t, id) => <DetailC id={id} />,
  },
  {
    id: "d", title: "D · Executive summary", tagline: "readable narrative, details folded", usesAlert: true,
    notes: {
      structure: "Single narrow column: playbook description, AI summary, three headline numbers, gaps, pattern; all evidence and guidance in expanders.",
      emphasis: "A human-readable answer to 'what is this alert about?'.",
      pros: ["Readable by non-specialists", "Very calm"],
      cons: ["Depends heavily on the AI summary, which does not exist yet", "Hides evidence one click away", "Playbook description is not in the API"],
      suits: "Managers, MLRO review, stakeholders.",
      backend: [ai, "FUTURE BACKEND FIELD: playbook description/purpose on the investigation response (exists in KB)."],
      now: "Only partially — without AI it is a thin page.",
    },
    render: (_t, id) => <DetailD id={id} />,
  },
];
