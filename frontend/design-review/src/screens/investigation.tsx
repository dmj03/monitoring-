import { clusterOf, human, kbEvidence } from "../data";
import { AppShell, Badge, Card, EvStatus, EvValue, Prov, type OptionDef } from "../kit";
import {
  AiSummary, AlertFacts, Checklist, Classification, ClusterContext, Counts, EvidenceTable, MissingPanel, Outcomes,
  Technical, ToolCalls, useInv,
} from "../blocks";

// A — Summary → status → evidence by source → gaps → technical
function InvA({ id }: { id: string }) {
  const { inv } = useInv(id);
  const bySource = new Map<string, typeof inv.evidence>();
  for (const e of inv.evidence) bySource.set(e.tool, [...(bySource.get(e.tool) ?? []), e]);
  return (
    <AppShell active="alerts">
      <div className="stack">
        <div><div className="crumbs">Alerts / {id} / <b>Investigation</b></div><h1 className="t">Investigation</h1></div>
        <Card title="Investigation summary" right={<Prov kind="engine" />}>
          <div className="stack">
            <Classification inv={inv} />
            <Counts inv={inv} />
          </div>
        </Card>
        <MissingPanel inv={inv} />
        <Card title="Evidence by source" right={<span className="xs muted">{inv.tool_calls.length} tool calls · deduplicated</span>}>
          <div className="stack">
            {[...bySource.entries()].map(([tool, items]) => (
              <div key={tool}>
                <div className="row" style={{ marginBottom: 6 }}><span className="src" style={{ fontSize: 12 }}>{tool}</span>
                  {items.some((e) => e.status === "MISSING") ? <Badge k="warn">gap</Badge> : null}</div>
                <div className="grid3">
                  {items.map((e) => (
                    <div key={e.key} className={`ev-tile${e.status === "MISSING" ? " missing" : ""}`}>
                      <div className="k">{human(e.key)}</div>
                      <div className="v"><EvValue e={e} /></div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
        <AiSummary alertId={id} mode="skeleton" />
        <Technical inv={inv} />
      </div>
    </AppShell>
  );
}

// B — Workflow steps 1..5
function InvB({ id }: { id: string }) {
  const { a, inv } = useInv(id);
  const c = clusterOf(id);
  const gaps = inv.missing_evidence.length;
  return (
    <AppShell active="alerts">
      <div className="stack">
        <div><div className="crumbs">Alerts / {id} / <b>Investigation</b></div><h1 className="t">How this alert was investigated</h1></div>
        <div className="steps">
          {[
            ["1", "Alert identified", `${a.rule_id}`, "done"],
            ["2", "Playbook selected", human(inv.playbook_id), "done"],
            ["3", "Evidence gathered", `${inv.evidence.length} items · ${inv.tool_calls.length} calls`, "done"],
            ["4", "Gaps identified", gaps ? `${gaps} missing` : "none", gaps ? "gap" : "done"],
            ["5", "AI summary", "planned", "ai"],
            ["6", "Analyst review", "you", "human"],
          ].map(([n, t, d, s]) => (
            <div key={n} className={`step ${s}`}><div className="n">STEP {n}</div><div className="t">{t}</div><div className="xs muted">{d}</div></div>
          ))}
        </div>
        <div className="vtl">
          <div className="it">
            <h2 className="s">1 · Alert identified</h2>
            <div className="sm muted" style={{ margin: "4px 0 8px" }}>Raised upstream by rule {a.rule_id}. {c ? `Part of a ${c.label.toLowerCase()} detected in this run.` : "Not part of a detected pattern."}</div>
            <AlertFacts a={a} />
          </div>
          <div className="it">
            <h2 className="s">2 · Playbook selected <Prov kind="engine" /></h2>
            <div className="sm muted" style={{ margin: "4px 0" }}>Deterministic lookup in the KB routing table — no model involved.</div>
            <Classification inv={inv} />
          </div>
          <div className="it">
            <h2 className="s">3 · Evidence gathered</h2>
            <div className="sm muted" style={{ margin: "4px 0 8px" }}>The playbook asked for {inv.evidence.length} items; identical requests were merged into {inv.tool_calls.length} tool calls.</div>
            <div className="card"><EvidenceTable inv={inv} source={false} /></div>
          </div>
          <div className={`it${gaps ? " gap" : ""}`}>
            <h2 className="s">4 · Gaps identified</h2>
            <div style={{ marginTop: 6 }}><MissingPanel inv={inv} /></div>
          </div>
          <div className="it ai">
            <h2 className="s">5 · AI summary</h2>
            <div style={{ marginTop: 6 }}><AiSummary alertId={id} mode="skeleton" recommend={false} /></div>
          </div>
          <div className="it next">
            <h2 className="s">6 · Analyst review</h2>
            <div className="grid2" style={{ marginTop: 6 }}>
              <div><div className="eyebrow">Checklist</div><Checklist inv={inv} /></div>
              <div><div className="eyebrow">Permitted outcomes</div><Outcomes inv={inv} /></div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

// C — Three-column workspace: context | investigation | evidence
function InvC({ id }: { id: string }) {
  const { a, inv } = useInv(id);
  return (
    <AppShell active="alerts">
      <div className="stack">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h1 className="t">{id} · investigation</h1><Counts inv={inv} />
        </div>
        <div className="ws" style={{ gridTemplateColumns: "250px minmax(0,1fr) 340px" }}>
          <div className="stack">
            <Card title="Alert context"><AlertFacts a={a} layout="list" /></Card>
            <Card title="Pattern"><ClusterContext alertId={id} compact /></Card>
          </div>
          <div className="stack">
            <Card title="Investigation" right={<Prov kind="engine" />}><Classification inv={inv} /></Card>
            <Card title="Checklist"><Checklist inv={inv} /></Card>
            <Card title="Escalation guidance"><div className="sm" style={{ color: "var(--text-2)" }}>{inv.escalation_guidance}</div></Card>
            <AiSummary alertId={id} mode="skeleton" recommend={false} />
          </div>
          <Card title="Evidence">
            <div className="stack" style={{ gap: 6 }}>
              {inv.evidence.map((e) => {
                const k = kbEvidence(inv, e.key);
                return (
                  <div key={e.key} className="row" style={{ justifyContent: "space-between", borderBottom: "1px solid var(--border)", paddingBottom: 6 }}>
                    <div><div className="sm">{human(e.key)}</div><div className="xs"><EvValue e={e} /></div></div>
                    <div style={{ textAlign: "right" }}><EvStatus e={e} />{e.status === "MISSING" && k?.required ? <div className="xs muted">required</div> : null}</div>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>
        <details className="tech"><summary>Tool calls</summary><ToolCalls inv={inv} /></details>
      </div>
    </AppShell>
  );
}

const be = [
  "All engine content (classification, evidence, gaps, checklist, guidance, outcomes, tool calls) is available now from POST /investigate.",
  "'Required / drill-down only' marker on a MISSING item: FUTURE BACKEND FIELD — required + missing_behaviour exist per item in playbooks.yaml but are not in EvidenceOut.",
  "AI step: FUTURE backend + LLM. Not built.",
  "Design question independent of layout: auto-run the investigation on page open (it is deterministic, fast and stores nothing) vs keep the explicit 'Investigate' button.",
];

export const investigationScreens: OptionDef[] = [
  {
    id: "a", title: "A · Summary → status → evidence → gaps", tagline: "structured report", usesAlert: true,
    notes: {
      structure: "Engine summary card (category, focus, found/missing meter), gap banner, evidence grouped by the tool that supplied it, AI placeholder, technical details collapsed.",
      emphasis: "What was found, grouped the way it was gathered.",
      pros: ["Grouping by source explains why there are fewer calls than items", "Gaps get their own band"],
      cons: ["Source grouping is a technical notion — may need friendlier names (customer record, screening, …)"],
      suits: "Analysts and QA.",
      backend: be, now: "Yes except AI and the required flag.",
    },
    render: (_t, id) => <InvA id={id} />,
  },
  {
    id: "b", title: "B · Workflow 1–6", tagline: "show the pipeline", usesAlert: true,
    notes: {
      structure: "Step bar across the top, then a vertical timeline: alert → playbook → evidence → gaps → AI → analyst review. Each step shows its own output.",
      emphasis: "How the deterministic investigation works, and where AI and the human come in.",
      pros: ["Best at explaining the system to a newcomer or stakeholder", "Makes the AI boundary and the human-review boundary explicit", "Replaces today's decorative FlowStrip with real content"],
      cons: ["Long for daily use", "Steps 1–4 are instantaneous; a workflow can imply progress that doesn't really happen"],
      suits: "Demos, onboarding, audit walkthroughs.",
      backend: be, now: "Yes except step 5 content.",
    },
    render: (_t, id) => <InvB id={id} />,
  },
  {
    id: "c", title: "C · Context | Investigation | Evidence", tagline: "three-column workspace", usesAlert: true,
    notes: {
      structure: "Left: alert + pattern. Centre: classification, checklist, guidance, AI. Right: compact evidence list with status.",
      emphasis: "Reading guidance and evidence side by side.",
      pros: ["Checklist and evidence visible together — supports actually working the checklist"],
      cons: ["Wide screens only", "Evidence values get cramped"],
      suits: "Experienced analysts.",
      backend: be, now: "Yes except AI and the required flag.",
    },
    render: (_t, id) => <InvC id={id} />,
  },
];
