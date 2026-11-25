import { human, llmInput } from "../data";
import { AppShell, Badge, Card, Prov, type OptionDef } from "../kit";
import { AiSummary, AlertFacts, CITED, Classification, Counts, EvidenceTable, MissingPanel, useInv } from "../blocks";

function ModelInput({ id }: { id: string }) {
  const m = llmInput(id);
  if (!m) return <div className="sm muted">No captured model input for this alert.</div>;
  return (
    <div className="stack sm">
      <div className="banner info xs">Real output of InvestigationEngine.build_llm_input() for {id}. Exists in code; not exposed by the API; never sent anywhere.</div>
      <div className="grid2">
        <div>
          <div className="eyebrow">Evidence the model would see</div>
          <table className="tb"><tbody>{Object.entries(m.evidence).map(([k, v]) => (
            <tr key={k}><td>{human(k)}</td><td className="mono">{v === "MISSING" ? <Badge k="warn">MISSING</Badge> : Array.isArray(v) ? `[${v.length} items]` : String(v)}</td></tr>
          ))}</tbody></table>
        </div>
        <div className="stack">
          <div><div className="eyebrow">Permitted outcomes</div><div className="chips">{m.permitted_outcomes.map((o) => <Badge key={o} k="outline">{o}</Badge>)}</div></div>
          <div><div className="eyebrow">Instruction</div><div className="muted">{m.instruction}</div></div>
          <div><div className="eyebrow">Model must not (global, {m.global_boundaries.model_must_not.length} rules)</div>
            <ul style={{ margin: 0, paddingLeft: 16 }} className="muted">{m.global_boundaries.model_must_not.slice(0, 6).map((r) => <li key={r}>{r}</li>)}<li>…</li></ul></div>
          <div><div className="eyebrow">Outcome contract</div><div className="muted">{m.global_boundaries.outcome_contract}</div></div>
        </div>
      </div>
    </div>
  );
}

// A — summary card at the top of Alert Detail
function AiA({ id }: { id: string }) {
  const { a, inv } = useInv(id);
  return (
    <AppShell active="alerts">
      <div className="stack">
        <h1 className="t">{id} <span style={{ fontWeight: 400, color: "var(--text-2)" }}>· {human(inv.category)}</span></h1>
        <AlertFacts a={a} />
        <AiSummary alertId={id} />
        <Card title="Evidence" right={<Counts inv={inv} />}><EvidenceTable inv={inv} source={false} /></Card>
      </div>
    </AppShell>
  );
}

// B — dedicated AI analysis section, AFTER evidence, with evidence-used column
function AiB({ id }: { id: string }) {
  const { inv } = useInv(id);
  return (
    <AppShell active="alerts">
      <div className="stack">
        <h1 className="t">{id} <span style={{ fontWeight: 400, color: "var(--text-2)" }}>· {human(inv.category)}</span></h1>
        <Card title="Evidence (deterministic)" right={<Prov kind="engine" />}><EvidenceTable inv={inv} /></Card>
        <MissingPanel inv={inv} />
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 280px", gap: 14, alignItems: "start" }}>
          <AiSummary alertId={id} title="AI analysis — planned" />
          <Card title="Evidence cited">
            <div className="stack xs" style={{ gap: 4 }}>
              {inv.evidence.map((e) => {
                const cited = (CITED[id] ?? []).includes(e.key);
                return (
                  <div key={e.key} className="row" style={{ justifyContent: "space-between", opacity: cited ? 1 : 0.6 }}>
                    <span className={`cite${e.status === "MISSING" ? " miss" : ""}`}>{e.key}</span>
                    <span className="muted">{!cited ? "not cited" : e.status === "MISSING" ? "cited as gap" : "cited"}</span>
                  </div>
                );
              })}
            </div>
            <div className="xs muted" style={{ marginTop: 8 }}>
              Shows what the summary used and what it left out. A FUTURE check could reject any summary that cites a key not in the pack,
              or that omits a MISSING item.
            </div>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}

// C — right-side assistant panel
function AiC({ id }: { id: string }) {
  const { a, inv } = useInv(id);
  return (
    <AppShell active="alerts">
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 360px", gap: 16, alignItems: "start" }}>
        <div className="stack">
          <h1 className="t">{id}</h1>
          <Card title="Classification" right={<Prov kind="engine" />}><Classification inv={inv} /></Card>
          <AlertFacts a={a} />
          <Card title="Evidence" right={<Counts inv={inv} />}><EvidenceTable inv={inv} source={false} /></Card>
        </div>
        <aside className="ai-box" style={{ position: "sticky", top: 0 }}>
          <div className="hd row" style={{ padding: "10px 14px", justifyContent: "space-between" }}>
            <b style={{ color: "var(--ai)" }}>✦ Assistant</b><Prov kind="ai" />
          </div>
          <div style={{ padding: 12 }} className="stack sm">
            <div className="card"><div className="bd"><b>Summarise this evidence pack</b><div className="skel" /><div className="skel" style={{ width: "70%" }} /></div></div>
            <div className="card"><div className="bd"><b>What is missing and does it matter?</b><div className="skel" style={{ width: "80%" }} /></div></div>
            <div className="card"><div className="bd"><b>Which permitted outcome fits, and why?</b><div className="skel" style={{ width: "60%" }} /></div></div>
            <input className="inp" placeholder="Ask about this alert… (disabled)" disabled />
            <div className="xs" style={{ color: "var(--ai)" }}>Placeholder. No model is connected. Answers would be limited to this alert's evidence pack.</div>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}

const be = [
  "FUTURE endpoint e.g. POST /investigate/summary (or a flag on /investigate) that calls a model with build_llm_input() — which already exists — and returns text + cited evidence keys + one recommended outcome from permitted_outcomes.",
  "FUTURE server-side guard: reject output that cites keys not in the pack, fills a MISSING value, or recommends an outcome outside allowed_outcomes.",
  "Storage of summaries is a separate decision: today nothing about an investigation is persisted.",
];
const input = "Input (exists today, shown in 'Model input'): alert id/rule/category, investigation_focus, checklist, escalation_guidance, global + playbook boundaries, evidence map with MISSING literals, missing_evidence, permitted_outcomes, instruction.";

export const aiScreens: OptionDef[] = [
  {
    id: "a", title: "A · Summary card on top", tagline: "read first", usesAlert: true,
    notes: {
      structure: "AI summary card directly under the alert facts, above the evidence table. Every sentence carries citation chips; missing items are cited as gaps.",
      emphasis: "A fast narrative answer.",
      pros: ["Highest visibility", "Simple to build", "Citations sit next to the claims"],
      cons: ["Analysts may read the AI before the evidence — anchoring", "Implies more authority than a recommendation should have"],
      suits: "Throughput-focused triage; demos.",
      backend: [input, ...be],
      now: "Layout only. Text needs a model.",
    },
    render: (_t, id) => <AiA id={id} />,
  },
  {
    id: "b", title: "B · Dedicated AI analysis after evidence", tagline: "evidence first, AI second", usesAlert: true,
    notes: {
      structure: "Deterministic evidence and gaps first; the AI analysis section comes after, with a side list of which evidence keys it cited.",
      emphasis: "The engine is the source of truth; AI interprets it.",
      pros: ["Strongest separation of deterministic vs generated", "Citation list makes uncited / missing items visible", "Reduces anchoring"],
      cons: ["AI is below the fold", "More vertical space"],
      suits: "Regulated review, QA, MLRO.",
      backend: [input, ...be],
      now: "Layout only.",
    },
    render: (_t, id) => <AiB id={id} />,
  },
  {
    id: "c", title: "C · Assistant side panel", tagline: "on demand", usesAlert: true,
    notes: {
      structure: "Investigation in the main column; a sticky assistant panel with three fixed prompts and a (disabled) free-text box.",
      emphasis: "AI as a tool the analyst invokes, not a verdict on the page.",
      pros: ["Most clearly optional", "Natural home for follow-up questions later"],
      cons: ["Free-text chat is a larger scope (conversation state, guardrails)", "Easy to ignore; least demo impact"],
      suits: "Analysts who want help only on request.",
      backend: [input, ...be, "Free-text Q&A: additional FUTURE endpoint and guardrails beyond a one-shot summary."],
      now: "Layout only.",
    },
    render: (_t, id) => <AiC id={id} />,
  },
  {
    id: "input", title: "What the model would receive", tagline: "real build_llm_input()", usesAlert: true,
    notes: {
      structure: "Not a design option — the actual payload the engine already builds for this alert.",
      emphasis: "What any AI summary is allowed to know.",
      pros: ["Grounds the AI discussion in what exists"], cons: ["—"], suits: "Deciding the AI design.",
      backend: ["build_llm_input() exists; no endpoint exposes it; nothing sends it."],
      now: "n/a",
    },
    render: (_t, id) => <AppShell active="alerts"><h1 className="t" style={{ marginBottom: 12 }}>Model input · {id}</h1><ModelInput id={id} /></AppShell>,
  },
];
