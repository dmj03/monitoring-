// Reusable investigation fragments shared by several options.
import type { ReactNode } from "react";
import {
  alertById, clusterOf, fmtTime, fmtValue, human, investigation, kb, kbEvidence,
  type Alert, type Cluster, type Investigation,
} from "./data";
import { Badge, Card, EvStatus, EvValue, Meter, Prov } from "./kit";

export function AlertFacts({ a, layout = "grid" }: { a: Alert; layout?: "grid" | "list" }) {
  const rows: [string, ReactNode][] = [
    ["Rule", <span className="mono">{a.rule_id}</span>],
    ["Customer", <span className="mono">{a.customer_ref}</span>],
    ["Programme", <span className="mono">{a.programme_ref}</span>],
    ["Created", fmtTime(a.created_at)],
    ["Shared match key", a.shared_match_key ? <span className="mono">{a.shared_match_key}</span> : <span className="muted">none</span>],
  ];
  return layout === "grid" ? (
    <div className="grid4" style={{ gridTemplateColumns: "repeat(5,minmax(0,1fr))" }}>
      {rows.map(([k, v]) => (
        <div key={k}>
          <div className="eyebrow">{k}</div>
          <div style={{ marginTop: 3 }}>{v}</div>
        </div>
      ))}
    </div>
  ) : (
    <div className="stack" style={{ gap: 10 }}>
      {rows.map(([k, v]) => (
        <div key={k}>
          <div className="eyebrow">{k}</div>
          <div>{v}</div>
        </div>
      ))}
    </div>
  );
}

export function Counts({ inv }: { inv: Investigation }) {
  const m = inv.missing_evidence.length;
  const f = inv.evidence.length - m;
  return (
    <div className="row" style={{ gap: 8 }}>
      <Badge k="ok">{f} found</Badge>
      <Badge k={m ? "warn" : "mute"}>{m} missing</Badge>
      <div style={{ width: 120 }}><Meter found={f} missing={m} /></div>
    </div>
  );
}

export function EvidenceTable({ inv, source = true, required = false, onlyMissingFirst = false }: {
  inv: Investigation; source?: boolean; required?: boolean; onlyMissingFirst?: boolean;
}) {
  const rows = onlyMissingFirst
    ? [...inv.evidence].sort((a, b) => (a.status === "MISSING" ? -1 : 0) - (b.status === "MISSING" ? -1 : 0))
    : inv.evidence;
  return (
    <table className="tb">
      <thead>
        <tr>
          <th>Evidence</th>
          <th>Status</th>
          <th>Value</th>
          {required ? <th>Bears on outcome <Prov kind="future" /></th> : null}
          {source ? <th>Source</th> : null}
        </tr>
      </thead>
      <tbody>
        {rows.map((e) => {
          const k = kbEvidence(inv, e.key);
          return (
            <tr key={e.key}>
              <td>{human(e.key)}</td>
              <td><EvStatus e={e} /></td>
              <td><EvValue e={e} /></td>
              {required ? <td className="sm">{k?.required ? "Yes — required" : "No — drill-down only"}</td> : null}
              {source ? <td><span className="src">{e.tool}.{e.field}</span></td> : null}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function MissingPanel({ inv, withKb = true }: { inv: Investigation; withKb?: boolean }) {
  if (!inv.missing_evidence.length)
    return (
      <div className="banner ok">
        All {inv.evidence.length} evidence items required by the playbook were found in the source data.
      </div>
    );
  return (
    <div className="banner warn" style={{ display: "block" }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>
        {inv.missing_evidence.length} evidence item{inv.missing_evidence.length > 1 ? "s" : ""} not found in the source data
      </div>
      <div className="stack" style={{ gap: 4, color: "var(--text-2)" }}>
        {inv.missing_evidence.map((k) => {
          const ev = inv.evidence.find((e) => e.key === k)!;
          const kbe = kbEvidence(inv, k);
          return (
            <div key={k} className="row" style={{ gap: 8 }}>
              <span>○ <b>{human(k)}</b></span>
              <span className="src">{ev.tool}.{ev.field}</span>
              {withKb && kbe ? (
                <span className="xs muted">
                  {kbe.required ? "required — may support insufficient evidence" : "drill-down only"} <Prov kind="future" />
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="xs" style={{ marginTop: 8, color: "var(--text-3)" }}>
        Missing is a finding, not a failure. The investigation completed.
      </div>
    </div>
  );
}

export function Classification({ inv, withPurpose = false }: { inv: Investigation; withPurpose?: boolean }) {
  const p = kb.playbooks[inv.playbook_id];
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 8 }}>
        <span style={{ fontFamily: "var(--head)", fontSize: 17, fontWeight: 600 }}>{human(inv.category)}</span>
        <Badge k="mute">{inv.rule_id} → {inv.playbook_id}</Badge>
      </div>
      <div style={{ color: "var(--text-2)" }}>{inv.investigation_focus}</div>
      {withPurpose && p ? (
        <div className="sm muted">
          {p.description} <Prov kind="future" />
        </div>
      ) : null}
    </div>
  );
}

export function Checklist({ inv }: { inv: Investigation }) {
  return (
    <ol style={{ margin: 0, paddingLeft: 18, color: "var(--text-2)" }}>
      {inv.checklist.map((c) => <li key={c} style={{ marginBottom: 4 }}>{c}</li>)}
    </ol>
  );
}

export function Outcomes({ inv, note = true }: { inv: Investigation; note?: boolean }) {
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="chips">
        {inv.allowed_outcomes.map((o) => <Badge key={o} k="outline">{human(o)}</Badge>)}
      </div>
      {note ? (
        <div className="xs muted">
          The outcomes this playbook permits. Recording an analyst decision needs a FUTURE endpoint; nothing is submitted today.
        </div>
      ) : null}
    </div>
  );
}

export function ToolCalls({ inv }: { inv: Investigation }) {
  return (
    <table className="tb">
      <thead><tr><th>Tool</th><th>Arguments</th><th>Supplied evidence</th></tr></thead>
      <tbody>
        {inv.tool_calls.map((c, i) => (
          <tr key={i}>
            <td className="mono">{c.tool}</td>
            <td className="src">{Object.entries(c.args).map(([k, v]) => `${k}=${String(v)}`).join("  ")}</td>
            <td className="sm">{inv.evidence.filter((e) => e.tool === c.tool).map((e) => human(e.key)).join(", ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Technical({ inv }: { inv: Investigation }) {
  return (
    <details className="tech">
      <summary>Technical details — {inv.tool_calls.length} tool calls, sources, AI boundaries</summary>
      <div className="stack" style={{ marginTop: 10 }}>
        <ToolCalls inv={inv} />
        <div className="sm">
          <div className="eyebrow">Playbook AI boundaries</div>
          <div className="muted">{inv.ai_boundaries}</div>
        </div>
        <div className="xs muted">Tool calls carry no result field by design; values appear only in the evidence above.</div>
      </div>
    </details>
  );
}

/** Cluster context derived from GET /clusters membership. Never from alert.cluster_ref. */
export function ClusterContext({ alertId, compact = false, interp = false }: { alertId: string; compact?: boolean; interp?: boolean }) {
  const c = clusterOf(alertId);
  if (!c)
    return (
      <div className="sm muted">
        Not part of any pattern detected in the current run (4 cluster types checked).
      </div>
    );
  const ki = kb.clusters[c.cluster_type];
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 8 }}>
        <Badge k="acc">{c.label}</Badge>
        <span className="sm">{c.alert_ids.length} alerts · {c.customer_refs.length} customers · {c.programme_refs.join(", ")} · {c.time_span_hours}h span</span>
      </div>
      {!compact ? <ClusterTimeline c={c} me={alertId} /> : null}
      {!compact ? <Thresholds c={c} /> : null}
      {interp && ki ? (
        <div className="sm" style={{ color: "var(--text-2)" }}>
          {ki.interpretation_guidance} <Prov kind="future" />
        </div>
      ) : null}
      <div className="xs muted">
        Detected in this run from current alerts. Membership can change as alerts are added; run ref {c.cluster_ref} is not a durable ID.
      </div>
    </div>
  );
}

export function Thresholds({ c }: { c: Cluster }) {
  const n = c.alert_ids.length;
  const cu = c.customer_refs.length;
  const th = c.thresholds as Record<string, number>;
  return (
    <div className="thr">
      <span className="t">alerts <b>{n}</b> / min {th.min_alerts}</span>
      <span className="t">customers <b>{cu}</b> / min {th.min_customers}</span>
      <span className="t">span <b>{c.time_span_hours}h</b> / window {th.time_window_hours}h</span>
    </div>
  );
}

export function ClusterTimeline({ c, me }: { c: Cluster; me?: string }) {
  const ts = c.alert_ids.map((id) => ({ id, t: Date.parse(alertById(id).created_at ?? "") }));
  const t0 = Math.min(...ts.map((x) => x.t));
  const win = Number((c.thresholds as Record<string, number>).time_window_hours) * 3600e3;
  const span = Math.max(win, Math.max(...ts.map((x) => x.t)) - t0) * 1.08;
  const pos = (t: number) => 3 + ((t - t0) / span) * 94;
  return (
    <div>
      <div className="tline">
        <div className="win" style={{ left: "3%", width: `${(win / span) * 94}%` }} title="configured time window" />
        {ts.map((x) => (
          <span key={x.id} className={`pt${x.id === me ? " me" : ""}`} style={{ left: `${pos(x.t)}%` }} title={x.id} />
        ))}
        {me ? <span className="lab" style={{ left: `${pos(ts.find((x) => x.id === me)!.t)}%` }}>this alert</span> : null}
      </div>
      <div className="xs muted">Each dot is a member alert by created_at. Shaded band = configured window.</div>
    </div>
  );
}

// ------------------------------------------------------------------ AI placeholder
/** Hand-written illustrative text. Restates only real evidence values, each cited. NOT model output. */
const ILLUSTRATIVE: Record<string, ReactNode[]> = {
  "ALRT-0001": [
    <>Transaction activity of 868 <C k="transaction_activity" /> stands well above both the customer baseline of 124 <C k="customer_transaction_baseline" /> and the programme baseline of 148 <C k="programme_transaction_baseline" />. The evidence pack does not state the period these counts cover.</>,
    <>Verification evidence is in place: KYB complete <C k="kyb_status" />, beneficial owner verified <C k="beneficial_owner_status" />, source of funds declared as retained earnings <C k="source_of_funds" />. No prior alerts are recorded <C k="prior_alerts" />.</>,
    <>The customer was onboarded on 2026-09-22 <C k="onboarding_date" />, so the customer baseline reflects a very short history.</>,
    <>The alert sits in a repeated-velocity pattern with 5 other alerts in programme P001 <span className="cite">cluster engine</span>. The pattern makes a programme-level question askable; it does not establish wrongdoing.</>,
  ],
  "ALRT-0073": [
    <>Activity is spread across 9 counterparties <C k="counterparty_spread" />, with the top counterparty CP-3842 <C k="top_counterparty" /> taking a 0.52 share <C k="top_counterparty_share" />. One counterparty is newly seen <C k="new_counterparty_count" />.</>,
    <>Counterparty jurisdictions are PL, CY and AE <C k="counterparty_jurisdictions" />; the customer's segment is travel services <C k="industry_segment" /> and KYB is complete <C k="kyb_status" />.</>,
    <>Whether the counterparty is shared with other customers cannot be assessed: that evidence was not found <C k="shared_counterparties" miss />. Prior alert history was also not found <C k="prior_alerts" miss />. Both gaps are recorded as findings and are not treated as negative or clear.</>,
  ],
  "ALRT-0067": [
    <>Screening reports a possible match <C k="screening_status" /> against the EU consolidated sanctions list <C k="list_type" />, with a confidence band reported as high <C k="match_confidence_band" />. Review is required <C k="review_required" />.</>,
    <>This is not a confirmed match and is not cleared. There have been 3 previous screening reviews <C k="previous_screening_reviews" />; the customer is a partnership <C k="entity_type" /> in DE <C k="customer_country" /> with beneficial owner verified <C k="beneficial_owner_status" />.</>,
  ],
};

/** Evidence keys each illustrative summary actually cites (kept in step with ILLUSTRATIVE above). */
export const CITED: Record<string, string[]> = {
  "ALRT-0001": ["transaction_activity", "customer_transaction_baseline", "programme_transaction_baseline", "kyb_status",
    "beneficial_owner_status", "source_of_funds", "prior_alerts", "onboarding_date"],
  "ALRT-0073": ["counterparty_spread", "top_counterparty", "top_counterparty_share", "new_counterparty_count",
    "counterparty_jurisdictions", "industry_segment", "kyb_status", "shared_counterparties", "prior_alerts"],
  "ALRT-0067": ["screening_status", "list_type", "match_confidence_band", "review_required", "previous_screening_reviews",
    "entity_type", "customer_country", "beneficial_owner_status"],
};

function C({ k, miss = false }: { k: string; miss?: boolean }) {
  return <span className={`cite${miss ? " miss" : ""}`}>{miss ? "missing: " : ""}{k}</span>;
}

export function AiSummary({ alertId, mode = "text", title = "AI-generated investigation summary — planned", recommend = true }: {
  alertId: string; mode?: "text" | "skeleton"; title?: string; recommend?: boolean;
}) {
  const inv = investigation(alertId);
  const paras = ILLUSTRATIVE[alertId];
  return (
    <section className="ai-box">
      <div className="hd row" style={{ padding: "10px 14px", justifyContent: "space-between" }}>
        <span style={{ fontWeight: 600, color: "var(--ai)" }}>✦ {title}</span>
        <Prov kind="ai" />
      </div>
      <div style={{ padding: "12px 14px" }}>
        {mode === "skeleton" || !paras ? (
          <>
            <div className="skel" style={{ width: "92%" }} />
            <div className="skel" style={{ width: "84%" }} />
            <div className="skel" style={{ width: "60%" }} />
          </>
        ) : (
          paras.map((p, i) => <p key={i}>{p}</p>)
        )}
        {recommend ? (
          <div className="row sm" style={{ marginTop: 6, gap: 8 }}>
            <span className="muted">Suggested outcome for analyst confirmation:</span>
            <Badge k="outline">one of {inv.allowed_outcomes.length} permitted outcomes</Badge>
            <span className="xs muted">(the model would pick from allowed_outcomes only; never final)</span>
          </div>
        ) : null}
      </div>
      <div className="placeholder-flag">
        {mode === "skeleton" || !paras
          ? "Placeholder. No model is connected."
          : "Illustrative text written by hand for layout review — NOT model output. Every statement cites an evidence key."}
      </div>
    </section>
  );
}

export function useInv(alertId: string) {
  return { a: alertById(alertId), inv: investigation(alertId), cluster: clusterOf(alertId) };
}

export { Card, fmtValue };
